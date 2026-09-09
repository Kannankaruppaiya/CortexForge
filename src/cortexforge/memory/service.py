"""Layered Memory Service (L0-L6) for CortexForge."""

import hashlib
import math
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.cognition.authority import (
    Authority,
    authority_from_source,
    authority_rank,
    is_proposal_only,
)
from cortexforge.cognition.epistemics import EpistemicState, EvidenceType
from cortexforge.core.models import (
    Memory,
    MemoryEvidence,
    MemoryRelation,
    MemoryVersion,
    Project,
)
from cortexforge.core.schemas import MemoryCreate
from cortexforge.embeddings.provider import EmbeddingProvider, get_embedding_provider
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.confidence import ConfidenceScorer
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState
from cortexforge.security.redactor import sanitize_text


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm1 * norm2)))


# Memory types whose assertions are costly to get wrong, and therefore route
# through review unless the user stated them directly (section 43).
class MemoryServiceError(Exception):
    """Base exception for memory service operations."""


class ConcurrentModificationError(MemoryServiceError):
    """Raised when an update specifies an expected_version that differs from current state."""

    def __init__(self, memory_id: str, current_version: int, expected_version: int) -> None:
        super().__init__(
            f"Concurrent modification on memory '{memory_id}': expected version {expected_version}, but current version is {current_version}"
        )
        self.memory_id = memory_id
        self.current_version = current_version
        self.expected_version = expected_version


_REVIEW_REQUIRED_TYPES: frozenset[str] = frozenset(
    {"CONSTRAINT", "ARCHITECTURE", "SECURITY"}
)

_EPISTEMIC_FOR_TYPE: dict[str, str] = {
    "DECISION": EpistemicState.DECISION.value,
    "CONSTRAINT": EpistemicState.CONSTRAINT.value,
    "ARCHITECTURE": EpistemicState.CONSTRAINT.value,
    "CONVENTION": EpistemicState.CONSTRAINT.value,
    "LESSON": EpistemicState.LESSON.value,
    "FAILURE": EpistemicState.FAILURE.value,
    "FIX": EpistemicState.SUCCESS.value,
    "FACT": EpistemicState.FACT.value,
}

_EVIDENCE_TYPE_FOR_SOURCE: dict[str, str] = {
    "code": EvidenceType.CODE.value,
    "verified_code": EvidenceType.CODE.value,
    "symbol": EvidenceType.SYMBOL.value,
    "ast": EvidenceType.AST.value,
    "git": EvidenceType.GIT.value,
    "commit": EvidenceType.COMMIT.value,
    "diff": EvidenceType.DIFF.value,
    "test": EvidenceType.TEST.value,
    "test_result": EvidenceType.TEST_RESULT.value,
    "config": EvidenceType.CONFIG.value,
    "schema": EvidenceType.SCHEMA.value,
    "api_contract": EvidenceType.API_CONTRACT.value,
    "doc": EvidenceType.DOCUMENTATION.value,
    "documentation": EvidenceType.DOCUMENTATION.value,
    "review": EvidenceType.REVIEW.value,
    "user": EvidenceType.USER_CONFIRMATION.value,
    "agent_observation": EvidenceType.AGENT_OBSERVATION.value,
}


class MemoryService:
    """Core memory engine managing layered project memory and lifecycle."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        conflict_resolver: ConflictResolver | None = None,
        claim_service: ClaimService | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.claim_service = claim_service or ClaimService()

    async def create_memory(
        self, session: AsyncSession, project_id: str, payload: MemoryCreate
    ) -> Memory:
        """Create a new grounded project memory with versioning and embeddings."""
        # Sanitize untrusted input against secrets and prompt injections
        sanitized_title = sanitize_text(payload.title)
        sanitized_content = sanitize_text(payload.content)
        sanitized_summary = sanitize_text(payload.summary)

        # Generate embedding for sanitized memory content
        embed_res = await self.embedding_provider.embed_text(f"{sanitized_title}\n{sanitized_content}")

        # Resolve the source onto the authority hierarchy once, here, so that every
        # downstream decision (confidence, conflict arbitration, activation) reads
        # one value rather than re-interpreting a free-form source string.
        authority = authority_from_source(payload.authority or payload.source_type)

        # Confidence is always derived, never taken on trust from the caller. A
        # client that asks for confidence 1.0 does not get it: the score comes from
        # the authority and evidence actually presented (section 8).
        conf_res = ConfidenceScorer.score(
            authority=authority,
            evidence=payload.evidence,
            status="ACTIVE",
        )
        confidence = conf_res.score

        # Explicit cognitive layer
        layer = (payload.layer or "L1").upper()

        # Activation policy (sections 7, 23, 43).
        #
        # Three questions decide the initial state, in order:
        #
        # 1. Can this source establish truth at all? An LLM, repository prose or
        #    untrusted input cannot. It enters as a CANDIDATE and must be verified
        #    or approved before anything believes it.
        # 2. Is it high-impact and *unevidenced*? A security constraint or
        #    architectural rule asserted without grounding is exactly the kind of
        #    claim that is expensive to get wrong, so it waits for a human.
        #    A high-impact claim that *is* grounded in code takes the evidence
        #    path instead -- routing verifiable claims through human review would
        #    make grounding pointless.
        # 3. Otherwise: grounded claims are believed and subsequently verified;
        #    ungrounded ones are recorded as UNVERIFIED rather than presented as
        #    established.
        is_high_impact = payload.memory_type.upper() in _REVIEW_REQUIRED_TYPES
        speaks_for_itself = authority_rank(authority) >= authority_rank(
            Authority.USER_CONFIRMED
        )

        if is_proposal_only(authority):
            status = MemoryState.CANDIDATE.value
        elif is_high_impact and not payload.evidence and not speaks_for_itself:
            status = MemoryState.REVIEW_REQUIRED.value
        elif payload.evidence:
            status = MemoryState.ACTIVE.value
        else:
            status = MemoryState.UNVERIFIED.value

        memory = Memory(
            project_id=project_id,
            layer=layer,
            memory_type=payload.memory_type.upper(),
            title=sanitized_title,
            content=sanitized_content,
            summary=sanitized_summary,
            status=status,
            confidence=confidence,
            confidence_components={
                **conf_res.components,
                "explanation": conf_res.explanation,
            },
            authority=authority.value,
            epistemic_state=_EPISTEMIC_FOR_TYPE.get(
                payload.memory_type.upper(), EpistemicState.OBSERVATION.value
            ),
            importance=payload.importance,
            freshness_score=payload.freshness_score,
            source_type=payload.source_type,
            scope=(payload.scope or "PROJECT").upper(),
            branch=payload.branch,
            workspace=payload.workspace,
            source_reference=payload.source_reference,
            source_commit=payload.source_commit,
            valid_from_commit=payload.valid_from_commit or payload.source_commit,
            valid_to_commit=payload.valid_to_commit,
            valid_from_time=payload.valid_from_time or datetime.now(UTC),
            valid_to_time=payload.valid_to_time,
            created_by=payload.created_by,
            version=1,
            supersedes_id=payload.supersedes_id,
            superseded_by_id=payload.superseded_by_id,
            conflict_group=payload.conflict_group,
            embedding={"vector": embed_res.vector},
            embedding_model=embed_res.model,
            embedding_version=embed_res.version,
            last_verified_at=datetime.now(UTC),
        )
        session.add(memory)
        await session.flush()

        # Attach initial version audit record
        version_record = MemoryVersion(
            memory_id=memory.id,
            version=1,
            previous_version=None,
            content=memory.content,
            change_reason="Initial memory creation",
        )
        session.add(version_record)

        # Attach evidences
        if payload.evidence:
            project = await session.get(Project, project_id)
            for ev in payload.evidence:
                ev_hash = ev.evidence_hash
                if not ev_hash and project and project.local_path:
                    abs_p = os.path.join(project.local_path, ev.file_path.replace("/", os.sep))
                    if os.path.exists(abs_p):
                        try:
                            with open(abs_p, "r", encoding="utf-8", errors="ignore") as f:
                                lines = f.readlines()
                            if ev.line_start is not None:
                                s_idx = max(0, ev.line_start - 1)
                                e_idx = ev.line_end if ev.line_end is not None else ev.line_start
                                snip = (
                                    "" if ev.line_start > len(lines)
                                    else "".join(lines[s_idx:e_idx])
                                )
                            else:
                                snip = "".join(lines)
                            # An anchor that resolves to nothing is not grounding.
                            # Hashing an empty snippet would produce the constant
                            # sha256 of the empty string, which would then "match"
                            # at verification time and make a claim about
                            # non-existent code verify as true.
                            ev_hash = (
                                hashlib.sha256(snip.strip().encode("utf-8")).hexdigest()
                                if snip.strip()
                                else None
                            )
                        except (OSError, UnicodeDecodeError):
                            ev_hash = None
                if not ev_hash:
                    # A location-only fingerprint. It identifies the evidence for
                    # deduplication but carries no content, so verification treats
                    # it as unverifiable rather than as confirmed.
                    ev_hash = hashlib.sha256(
                        f"unresolved:{ev.file_path}:{ev.line_start or 0}".encode()
                    ).hexdigest()

                ev_obj = MemoryEvidence(
                    memory_id=memory.id,
                    source_type=ev.source_type,
                    evidence_type=_EVIDENCE_TYPE_FOR_SOURCE.get(
                        (ev.source_type or "").lower(), EvidenceType.CODE.value
                    ),
                    authority=authority_from_source(ev.source_type).value,
                    source_id=ev.source_id,
                    file_path=ev.file_path,
                    symbol_id=ev.symbol_id,
                    commit_sha=ev.commit_sha,
                    line_start=ev.line_start,
                    line_end=ev.line_end,
                    evidence_hash=ev_hash,
                    snippet_hash=ev.snippet_hash or ev_hash,
                    ast_fingerprint=ev.ast_fingerprint,
                    confidence=ev.confidence,
                )
                session.add(ev_obj)
            await session.flush()

        # Decompose the memory into individually evaluable claims. This happens at
        # creation so that a memory is verifiable from the moment it exists, rather
        # than only once something later thinks to decompose it (section 4).
        await session.refresh(memory, ["evidences"])
        await self.claim_service.sync_memory_claims(
            session, memory, commit_sha=payload.source_commit
        )

        # Check and resolve semantic contradictions against existing memories
        await self.conflict_resolver.check_and_resolve(
            session=session,
            project_id=project_id,
            candidate_memory=memory,
            candidate_vector=embed_res.vector,
        )

        await session.commit()
        await session.refresh(memory)
        return memory

    async def get_memory(self, session: AsyncSession, memory_id: str) -> Memory | None:
        """Fetch memory with evidences and versions loaded."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences), selectinload(Memory.versions))
            .where(Memory.id == memory_id)
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    async def update_memory(
        self,
        session: AsyncSession,
        memory_id: str,
        content: str,
        change_reason: str,
        title: str | None = None,
        summary: str | None = None,
        expected_version: int | None = None,
    ) -> Memory | None:
        """Mutate memory content and increment version audit trail with optimistic locking."""
        memory = await self.get_memory(session, memory_id)
        if not memory:
            return None

        if expected_version is not None and memory.version != expected_version:
            raise ConcurrentModificationError(
                memory_id=memory.id,
                current_version=memory.version,
                expected_version=expected_version,
            )

        prev_version = memory.version
        memory.version += 1
        memory.content = sanitize_text(content)
        if title:
            memory.title = sanitize_text(title)
        if summary:
            memory.summary = sanitize_text(summary)

        # Recompute embedding
        embed_res = await self.embedding_provider.embed_text(f"{memory.title}\n{memory.content}")
        memory.embedding = {"vector": embed_res.vector}
        memory.updated_at = datetime.now(UTC)

        version_record = MemoryVersion(
            memory_id=memory.id,
            version=memory.version,
            previous_version=prev_version,
            content=content,
            change_reason=change_reason,
        )
        session.add(version_record)
        await session.commit()
        await session.refresh(memory)
        return memory

    async def transition_memory_status(
        self,
        session: AsyncSession,
        memory_id: str,
        new_status: str,
        reason: str,
        superseded_by_id: str | None = None,
        conflict_group: str | None = None,
    ) -> Memory | None:
        """Safely transition memory status adhering to finite state machine rules."""
        memory = await self.get_memory(session, memory_id)
        if not memory:
            return None

        ver = MemoryLifecycleManager.transition(
            memory,
            new_state=new_status,
            reason=reason,
            superseded_by_id=superseded_by_id,
            conflict_group=conflict_group,
        )
        if ver:
            session.add(ver)

        if superseded_by_id:
            rel = MemoryRelation(
                source_memory_id=superseded_by_id,
                target_memory_id=memory.id,
                relation_type="supersedes",
                confidence=1.0,
            )
            session.add(rel)

        await session.commit()
        await session.refresh(memory)
        return memory

    async def deprecate_memory(
        self,
        session: AsyncSession,
        memory_id: str,
        superseded_by_id: str | None = None,
        reason: str = "Explicitly deprecated",
    ) -> Memory | None:
        """Mark memory as superseded or archived via lifecycle manager."""
        return await self.transition_memory_status(
            session=session,
            memory_id=memory_id,
            new_status=MemoryState.SUPERSEDED.value,
            reason=reason,
            superseded_by_id=superseded_by_id,
        )

    async def list_memories(
        self,
        session: AsyncSession,
        project_id: str,
        layer: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        min_importance: float = 0.0,
        as_of_time: datetime | None = None,
        at_commit: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Memory]:
        """Query memories with multi-attribute and temporal validity filtering."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(Memory.project_id == project_id)
        )
        if layer:
            stmt = stmt.where(Memory.layer == layer.upper())
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())
        if status:
            stmt = stmt.where(Memory.status == status.upper())
        if min_importance > 0.0:
            stmt = stmt.where(Memory.importance >= min_importance)

        if as_of_time:
            stmt = stmt.where(
                or_(Memory.valid_from_time.is_(None), Memory.valid_from_time <= as_of_time),
                or_(Memory.valid_to_time.is_(None), Memory.valid_to_time > as_of_time),
            )
        elif status == "ACTIVE":
            stmt = stmt.where(
                or_(Memory.valid_to_time.is_(None), Memory.valid_to_time > datetime.now(UTC))
            )

        if at_commit:
            stmt = stmt.where(
                or_(Memory.valid_from_commit.is_(None), Memory.valid_from_commit == at_commit),
                or_(Memory.valid_to_commit.is_(None), Memory.valid_to_commit != at_commit),
            )

        stmt = stmt.order_by(Memory.importance.desc(), Memory.created_at.desc()).offset(offset).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def search_memories(
        self,
        session: AsyncSession,
        project_id: str,
        query: str,
        memory_type: str | None = None,
        as_of_time: datetime | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Vector semantic similarity search over project memories with temporal filtering."""
        query_embed = await self.embedding_provider.embed_text(query)
        qvec = query_embed.vector

        effective_time = as_of_time or datetime.now(UTC)
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(["ACTIVE", "UNVERIFIED", "STALE"]),
                or_(Memory.valid_to_time.is_(None), Memory.valid_to_time > effective_time),
            )
        )
        if as_of_time:
            stmt = stmt.where(
                or_(Memory.valid_from_time.is_(None), Memory.valid_from_time <= as_of_time)
            )
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())

        res = await session.execute(stmt)
        memories = list(res.scalars().all())

        scored: list[dict[str, Any]] = []
        for mem in memories:
            emb_data = mem.embedding or {}
            mvec = emb_data.get("vector")
            sim = cosine_similarity(qvec, mvec) if mvec else 0.0

            # Lexical boost
            q_terms = set(query.lower().split())
            content_lower = f"{mem.title} {mem.summary} {mem.content}".lower()
            overlap = sum(1 for t in q_terms if t in content_lower)
            lex_score = overlap / max(1, len(q_terms))

            combined_score = (0.65 * sim) + (0.35 * lex_score)
            scored.append({
                "memory": mem,
                "similarity": round(sim, 4),
                "combined_score": round(combined_score, 4),
            })

        scored.sort(key=lambda x: x["combined_score"], reverse=True)
        return scored[:limit]

    async def get_decisions(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch all active architectural decisions."""
        return await self.list_memories(session, project_id, memory_type="DECISION", status="ACTIVE")

    async def get_failures(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch historical failures and anti-patterns."""
        return await self.list_memories(session, project_id, memory_type="FAILURE")

    async def get_constraints(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch operational and architectural constraints."""
        return await self.list_memories(session, project_id, memory_type="CONSTRAINT", status="ACTIVE")

    async def resolve_project_conflicts(
        self, session: AsyncSession, project_id: str
    ) -> list[Any]:
        """Run batch conflict resolution over all active/unverified memories in project."""
        stmt = (
            select(Memory)
            .where(
                Memory.project_id == project_id,
                Memory.status.in_([MemoryState.ACTIVE.value, MemoryState.UNVERIFIED.value]),
            )
            .order_by(Memory.created_at.asc())
        )
        res = await session.execute(stmt)
        mems = list(res.scalars().all())

        all_detections = []
        for mem in mems:
            detections = await self.conflict_resolver.check_and_resolve(
                session=session,
                project_id=project_id,
                candidate_memory=mem,
            )
            all_detections.extend(detections)

        await session.commit()
        return all_detections

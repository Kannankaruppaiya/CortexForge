"""Persistence and deduplication for claims (specification sections 4 and 25).

This service is the only place claims are created. It guarantees three things the
rest of the system relies on:

* **Logical identity.** A claim is identified by ``claim_key`` -- a hash of its
  canonical proposition, scope and project -- not by its row id. Re-observing the
  same proposition returns the existing claim instead of creating a second one, so
  running the same ingestion twice leaves one claim (section 37).
* **Evidence is attached, not assumed.** Claims inherit the grounding of the memory
  they came from, and each evidence link records its own type, relation and
  authority so that contradicting evidence is representable (section 5).
* **Nothing is asserted as true here.** Claims are created ``PROPOSED`` or
  ``UNVERIFIED``. Only the verification engine may move a claim to ``VERIFIED``.
"""

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.cognition.authority import (
    Authority,
    authority_from_source,
    is_proposal_only,
)
from cortexforge.cognition.claims import (
    ExtractedClaim,
    claim_similarity,
    extract_claims,
)
from cortexforge.cognition.epistemics import (
    ClaimStatus,
    EpistemicState,
    EvidenceRelation,
    EvidenceType,
)
from cortexforge.core.models import Claim, ClaimEvidence, CodeEntity, Memory

logger = logging.getLogger(__name__)

# Memory types whose statements are intrinsically decisions or constraints rather
# than observations about code.
_EPISTEMIC_BY_MEMORY_TYPE: dict[str, str] = {
    "DECISION": EpistemicState.DECISION.value,
    "CONSTRAINT": EpistemicState.CONSTRAINT.value,
    "LESSON": EpistemicState.LESSON.value,
    "FAILURE": EpistemicState.FAILURE.value,
    "FIX": EpistemicState.SUCCESS.value,
    "FACT": EpistemicState.FACT.value,
    "ARCHITECTURE": EpistemicState.CONSTRAINT.value,
    "CONVENTION": EpistemicState.CONSTRAINT.value,
    "WARNING": EpistemicState.OBSERVATION.value,
    "EPISODE": EpistemicState.OBSERVATION.value,
    "TASK_STATE": EpistemicState.OBSERVATION.value,
}

# Similarity above which two claims in the same project are reported as probable
# duplicates. It flags for review; it never merges automatically.
NEAR_DUPLICATE_THRESHOLD = 0.72


def evidence_fingerprint(
    evidence_type: str,
    relation: str,
    file_path: str | None,
    qualified_name: str | None,
    line_start: int | None,
    line_end: int | None,
    content_hash: str | None,
    commit_sha: str | None,
) -> str:
    """Stable identity for an evidence item, so re-observation is idempotent."""
    material = "|".join(
        [
            (evidence_type or "").upper(),
            (relation or "").upper(),
            file_path or "",
            qualified_name or "",
            str(line_start or ""),
            str(line_end or ""),
            content_hash or "",
            commit_sha or "",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _independence_group(file_path: str | None, commit_sha: str | None) -> str:
    """Evidence from the same file at the same commit is one independent source."""
    return hashlib.sha256(f"{file_path or ''}@{commit_sha or ''}".encode()).hexdigest()[:32]


class ClaimService:
    """Creates, deduplicates and grounds claims extracted from memories."""

    async def sync_memory_claims(
        self,
        session: AsyncSession,
        memory: Memory,
        commit_sha: str | None = None,
        flush: bool = True,
    ) -> list[Claim]:
        """Extract the claims in a memory and reconcile them with what is stored.

        Existing claims for the memory whose propositions are still present are
        reused (preserving their verification history); ones that are gone are
        retired rather than deleted, so a memory edit does not erase the record
        that CortexForge once believed something.
        """
        scope = (memory.scope or "PROJECT").upper()
        scope_ref = memory.source_reference
        extracted = extract_claims(
            project_id=memory.project_id,
            text=memory.content,
            title=memory.title,
            scope=scope,
            scope_ref=scope_ref,
        )

        existing_res = await session.execute(
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .where(Claim.memory_id == memory.id)
        )
        existing = {c.claim_key: c for c in existing_res.scalars().all()}

        authority = authority_from_source(memory.authority or memory.source_type)
        epistemic = _EPISTEMIC_BY_MEMORY_TYPE.get(
            (memory.memory_type or "").upper(), EpistemicState.OBSERVATION.value
        )

        # A proposal-authority statement (LLM, repository text, untrusted) starts as
        # PROPOSED and can only become believed through verification (section 23).
        initial_status = (
            ClaimStatus.PROPOSED.value
            if is_proposal_only(authority)
            else ClaimStatus.UNVERIFIED.value
        )

        live: list[Claim] = []
        seen_keys: set[str] = set()

        for item in extracted:
            seen_keys.add(item.claim_key)
            claim = existing.get(item.claim_key)
            if claim is None:
                claim = await self._get_or_create_shared_claim(
                    session,
                    memory=memory,
                    item=item,
                    scope=scope,
                    scope_ref=scope_ref,
                    authority=authority,
                    epistemic=epistemic,
                    status=initial_status,
                    commit_sha=commit_sha,
                )
            else:
                # Text may have been rephrased without changing the proposition.
                claim.text = item.text
                claim.subject = item.subject
                claim.predicate = item.predicate
                claim.authority = authority.value
            live.append(claim)

        # Propositions no longer present in the memory are retired, not deleted.
        for key, claim in existing.items():
            if key not in seen_keys and claim.status != ClaimStatus.RETIRED.value:
                claim.status = ClaimStatus.RETIRED.value
                claim.valid_to_commit = commit_sha or claim.valid_to_commit

        if flush:
            await session.flush()

        for claim in live:
            await self.attach_memory_evidence(session, claim, memory, commit_sha=commit_sha)

        if flush:
            await session.flush()

        return live

    async def _get_or_create_shared_claim(
        self,
        session: AsyncSession,
        memory: Memory,
        item: ExtractedClaim,
        scope: str,
        scope_ref: str | None,
        authority: Authority,
        epistemic: str,
        status: str,
        commit_sha: str | None,
    ) -> Claim:
        """Return the project-wide claim for this proposition, creating it if new.

        Two memories asserting the same thing converge on one claim. The claim keeps
        the *higher* of the two authorities, because the proposition is as
        well-sourced as its best source -- but it does not inherit the higher
        source's verification, which must be earned separately.
        """
        res = await session.execute(
            select(Claim).where(
                Claim.project_id == memory.project_id,
                Claim.claim_key == item.claim_key,
                Claim.valid_to_commit.is_(None),
                Claim.status != ClaimStatus.RETIRED.value,
            )
        )
        shared = res.scalars().first()

        if shared is not None:
            from cortexforge.cognition.authority import authority_rank

            if authority_rank(authority) > authority_rank(shared.authority):
                shared.authority = authority.value
            if shared.memory_id is None:
                shared.memory_id = memory.id
            return shared

        claim = Claim(
            project_id=memory.project_id,
            memory_id=memory.id,
            text=item.text,
            canonical_text=item.canonical_text,
            claim_key=item.claim_key,
            subject=item.subject,
            predicate=item.predicate,
            epistemic_state=epistemic,
            authority=authority.value,
            status=status,
            scope=scope,
            scope_ref=scope_ref,
            confidence=0.0,
            confidence_components={},
            valid_from_commit=commit_sha or memory.source_commit,
            branch=memory.branch,
            workspace=memory.workspace,
        )
        session.add(claim)
        return claim

    async def attach_memory_evidence(
        self,
        session: AsyncSession,
        claim: Claim,
        memory: Memory,
        commit_sha: str | None = None,
    ) -> list[ClaimEvidence]:
        """Ground a claim in the code evidence recorded on its memory."""
        if not memory.evidences:
            return []

        # Evidence links and symbol names are fetched explicitly rather than through
        # relationship attributes: an implicit lazy load inside an async session
        # raises MissingGreenlet, so the queries belong here where they are awaited.
        existing_res = await session.execute(
            select(ClaimEvidence.evidence_hash).where(ClaimEvidence.claim_id == claim.id)
        )
        existing_hashes = set(existing_res.scalars().all())

        symbol_names = await self._symbol_names(
            session, [ev.symbol_id for ev in memory.evidences if ev.symbol_id]
        )
        created: list[ClaimEvidence] = []

        for ev in memory.evidences:
            evidence_type = (ev.evidence_type or EvidenceType.CODE.value).upper()
            relation = (ev.relation or EvidenceRelation.SUPPORTS.value).upper()
            qualified = symbol_names.get(ev.symbol_id) if ev.symbol_id else None
            fingerprint = evidence_fingerprint(
                evidence_type,
                relation,
                ev.file_path,
                qualified,
                ev.line_start,
                ev.line_end,
                ev.snippet_hash or ev.evidence_hash,
                ev.commit_sha or commit_sha,
            )
            if fingerprint in existing_hashes:
                continue
            existing_hashes.add(fingerprint)

            link = ClaimEvidence(
                claim_id=claim.id,
                evidence_type=evidence_type,
                relation=relation,
                authority=(ev.authority or Authority.CODE_VERIFIED.value),
                source=memory.source_reference,
                commit_sha=ev.commit_sha or commit_sha,
                branch=ev.branch or memory.branch,
                workspace=ev.workspace or memory.workspace,
                file_path=ev.file_path,
                symbol_id=ev.symbol_id,
                qualified_name=qualified,
                line_start=ev.line_start,
                line_end=ev.line_end,
                content_hash=ev.snippet_hash or ev.evidence_hash,
                ast_fingerprint=ev.ast_fingerprint,
                evidence_hash=fingerprint,
                independence_group=_independence_group(ev.file_path, ev.commit_sha or commit_sha),
                detail={"memory_evidence_id": ev.id},
            )
            session.add(link)
            created.append(link)

        return created

    @staticmethod
    async def _symbol_names(
        session: AsyncSession, symbol_ids: list[str]
    ) -> dict[str, str]:
        """Qualified names for the given code entities, in one query."""
        if not symbol_ids:
            return {}
        res = await session.execute(
            select(CodeEntity.id, CodeEntity.qualified_name).where(
                CodeEntity.id.in_(set(symbol_ids))
            )
        )
        return {row[0]: row[1] for row in res.all()}

    async def add_evidence(
        self,
        session: AsyncSession,
        claim: Claim,
        evidence_type: str,
        relation: str = EvidenceRelation.SUPPORTS.value,
        authority: str = Authority.AGENT_OBSERVED.value,
        file_path: str | None = None,
        qualified_name: str | None = None,
        symbol_id: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        content_hash: str | None = None,
        ast_fingerprint: str | None = None,
        commit_sha: str | None = None,
        branch: str | None = None,
        workspace: str | None = None,
        source: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> ClaimEvidence | None:
        """Attach one evidence item, returning ``None`` if it is already recorded.

        Both supporting and contradicting evidence go through here: recording that
        a test refutes a claim is as important as recording that code supports it.
        """
        fingerprint = evidence_fingerprint(
            evidence_type, relation, file_path, qualified_name, line_start, line_end,
            content_hash, commit_sha,
        )
        existing = await session.execute(
            select(ClaimEvidence).where(
                ClaimEvidence.claim_id == claim.id,
                ClaimEvidence.evidence_hash == fingerprint,
            )
        )
        if existing.scalars().first() is not None:
            return None

        link = ClaimEvidence(
            claim_id=claim.id,
            evidence_type=(evidence_type or EvidenceType.CODE.value).upper(),
            relation=(relation or EvidenceRelation.SUPPORTS.value).upper(),
            authority=authority,
            source=source,
            commit_sha=commit_sha,
            branch=branch,
            workspace=workspace,
            file_path=file_path,
            symbol_id=symbol_id,
            qualified_name=qualified_name,
            line_start=line_start,
            line_end=line_end,
            content_hash=content_hash,
            ast_fingerprint=ast_fingerprint,
            evidence_hash=fingerprint,
            independence_group=_independence_group(file_path, commit_sha),
            detail=detail or {},
        )
        session.add(link)
        return link

    async def find_near_duplicates(
        self,
        session: AsyncSession,
        project_id: str,
        canonical_text: str,
        exclude_claim_id: str | None = None,
        threshold: float = NEAR_DUPLICATE_THRESHOLD,
    ) -> list[tuple[Claim, float]]:
        """Find claims that probably say the same thing in different words.

        Returned for review and for consolidation's duplicate check. Nothing is
        merged on the strength of this signal alone.
        """
        tokens = tuple(canonical_text.split())
        if not tokens:
            return []

        res = await session.execute(
            select(Claim).where(
                Claim.project_id == project_id,
                Claim.status.notin_([ClaimStatus.RETIRED.value, ClaimStatus.SUPERSEDED.value]),
            )
        )
        matches: list[tuple[Claim, float]] = []
        for other in res.scalars().all():
            if exclude_claim_id and other.id == exclude_claim_id:
                continue
            similarity = claim_similarity(tokens, tuple(other.canonical_text.split()))
            if similarity >= threshold:
                matches.append((other, round(similarity, 4)))

        return sorted(matches, key=lambda pair: pair[1], reverse=True)

    async def get_claims_for_memory(
        self, session: AsyncSession, memory_id: str
    ) -> list[Claim]:
        """All non-retired claims belonging to a memory, with their evidence."""
        res = await session.execute(
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .where(Claim.memory_id == memory_id, Claim.status != ClaimStatus.RETIRED.value)
            .order_by(Claim.created_at)
        )
        return list(res.scalars().all())

    async def find_claims_touching(
        self,
        session: AsyncSession,
        project_id: str,
        file_paths: list[str] | None = None,
        qualified_names: list[str] | None = None,
    ) -> list[Claim]:
        """Claims whose evidence points at any of the given files or symbols.

        This is the join that makes reconciliation incremental: a change touches
        evidence, evidence identifies claims, and only those claims are re-evaluated
        rather than the whole project (section 55).
        """
        if not file_paths and not qualified_names:
            return []

        conditions = []
        if file_paths:
            normalized = [p.replace("\\", "/") for p in file_paths]
            conditions.append(ClaimEvidence.file_path.in_(normalized))
        if qualified_names:
            conditions.append(ClaimEvidence.qualified_name.in_(qualified_names))

        from sqlalchemy import or_

        stmt = (
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
            .where(
                Claim.project_id == project_id,
                Claim.status != ClaimStatus.RETIRED.value,
                or_(*conditions),
            )
            .distinct()
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

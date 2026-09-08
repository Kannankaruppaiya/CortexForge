"""Safe memory consolidation (specification sections 23, 25 and 26).

Consolidation turns many episodes into one durable lesson. The previous
implementation did it in the one way the specification explicitly forbids:

    LLM synthesis -> ACTIVE -> archive the source episodes

all inside a single transaction. That makes an LLM the author of durable project
truth and destroys the evidence trail in the same breath.

The pipeline here is:

    episodes -> cluster -> proposed lesson -> CANDIDATE -> deduplicate
             -> evidence validation -> conflict check -> verification -> ACTIVE

with two hard rules:

* **Sources are archived only after the lesson is durably promoted.** If promotion
  does not happen -- because the lesson could not be verified, duplicated an
  existing one, or contradicted established knowledge -- the episodes stay exactly
  where they were. Consolidation never destroys what it failed to replace.
* **Consolidation is idempotent.** A cluster is identified by a fingerprint over
  its member episodes, so running consolidation repeatedly over the same events
  converges on one lesson rather than accumulating near-duplicates.

The LLM's role is narrowed to what it is good at: proposing a phrasing. It cannot
decide that the phrasing is true.
"""

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.cognition.authority import Authority
from cortexforge.cognition.claims import canonicalize
from cortexforge.cognition.epistemics import EpistemicState
from cortexforge.core.models import Memory, MemoryEvidence, MemoryRelation
from cortexforge.llm.provider import LLMProvider, get_llm_provider
from cortexforge.memory.claims import NEAR_DUPLICATE_THRESHOLD, ClaimService
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.memory.conflict_resolver import ConflictResolver, cosine_similarity
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.memory.service import MemoryService

logger = logging.getLogger(__name__)

# Source states an episode may be in and still be eligible for consolidation.
# UNVERIFIED is included: an episode is a record that something happened, and it
# does not need code grounding to be worth learning from.
_CONSOLIDATABLE_STATES = (
    MemoryState.ACTIVE.value,
    MemoryState.UNVERIFIED.value,
)

_EPISODIC_TYPES = ("EPISODE", "FAILURE", "FIX", "TASK_STATE")

# Similarity at or above which two episodes belong in one cluster.
CLUSTER_SIMILARITY_THRESHOLD = 0.50
CLUSTER_OVERLAP_THRESHOLD = 0.25


def cluster_fingerprint(project_id: str, member_ids: list[str]) -> str:
    """Stable identity for a cluster of episodes.

    Order-independent, so the same set of episodes always yields the same
    fingerprint no matter what order the clustering pass encountered them in.
    """
    material = f"{project_id}|" + ",".join(sorted(member_ids))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class MemoryConsolidationEngine:
    """Consolidates episodes into durable lessons, without letting an LLM decide truth."""

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        llm_provider: LLMProvider | None = None,
        conflict_resolver: ConflictResolver | None = None,
        claim_service: ClaimService | None = None,
    ) -> None:
        self.memory_service = memory_service or MemoryService()
        self.llm_provider = llm_provider or get_llm_provider()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.claims = claim_service or ClaimService()

    async def consolidate_project_memories(
        self, session: AsyncSession, project_id: str
    ) -> dict[str, Any]:
        """Alias retained for existing callers."""
        return await self.consolidate_project(session, project_id)

    async def consolidate_project(
        self,
        session: AsyncSession,
        project_id: str,
        auto_promote: bool = False,
    ) -> dict[str, Any]:
        """Cluster episodes and propose durable lessons.

        ``auto_promote`` is off by default. With it off, lessons are created in
        ``REVIEW_REQUIRED`` and wait for approval; the source episodes are left
        untouched. With it on, a lesson that clears deduplication, evidence and
        conflict checks is activated and only then are its sources archived.
        """
        episodes = await self._load_episodes(session, project_id)
        if len(episodes) < 2:
            return self._empty_result(
                "Not enough episodic memories to trigger consolidation (minimum 2)."
            )

        clusters = self._cluster(episodes)
        existing_lessons = await self._load_lessons(session, project_id)
        known_fingerprints = {
            (lesson.source_reference or "").removeprefix("cluster:")
            for lesson in existing_lessons
        }

        created = 0
        promoted = 0
        archived = 0
        skipped_duplicate = 0
        skipped_conflict = 0
        proposals: list[dict[str, Any]] = []

        for cluster in clusters:
            fingerprint = cluster_fingerprint(project_id, [m.id for m in cluster])

            # Idempotency: this exact set of episodes already produced a lesson.
            if fingerprint in known_fingerprints:
                skipped_duplicate += 1
                continue

            if self._cluster_contains_contradiction(cluster):
                # Consolidating contradictory episodes would synthesise a consensus
                # that none of them supports. The cluster is left alone and reported.
                skipped_conflict += 1
                continue

            proposal = await self._propose_lesson(cluster)
            duplicate = await self._find_duplicate_lesson(
                session, project_id, proposal["content"], existing_lessons
            )
            if duplicate is not None:
                skipped_duplicate += 1
                proposals.append(
                    {
                        "cluster_fingerprint": fingerprint,
                        "status": "SKIPPED_DUPLICATE",
                        "duplicate_of": duplicate.id,
                        "title": proposal["title"],
                    }
                )
                continue

            lesson = await self._create_candidate_lesson(
                session, project_id, proposal, cluster, fingerprint
            )
            created += 1
            existing_lessons.append(lesson)
            known_fingerprints.add(fingerprint)

            record: dict[str, Any] = {
                "cluster_fingerprint": fingerprint,
                "memory_id": lesson.id,
                "title": lesson.title,
                "status": lesson.status,
                "source_episode_ids": [m.id for m in cluster],
            }

            if auto_promote:
                did_promote = await self._promote(session, lesson)
                record["status"] = lesson.status
                if did_promote:
                    promoted += 1
                    archived += await self._archive_sources(session, lesson, cluster)
                else:
                    record["promotion_blocked"] = (
                        "lesson could not be verified; source episodes retained"
                    )
            proposals.append(record)

        await session.commit()
        return {
            "clusters_consolidated": len(clusters),
            "durable_memories_created": created,
            "durable_memories_promoted": promoted,
            "memories_archived": archived,
            "contradictions_detected": skipped_conflict,
            "duplicates_skipped": skipped_duplicate,
            "auto_promote": auto_promote,
            "proposals": proposals,
            "message": (
                f"{created} lesson(s) proposed"
                + (f", {promoted} promoted, {archived} source episode(s) archived"
                   if auto_promote else " and left awaiting review")
            ),
        }

    # --------------------------------------------------------------- loading

    async def _load_episodes(self, session: AsyncSession, project_id: str) -> list[Memory]:
        res = await session.execute(
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(_CONSOLIDATABLE_STATES),
                Memory.memory_type.in_(_EPISODIC_TYPES),
            )
            .order_by(Memory.created_at)
        )
        return list(res.scalars().all())

    async def _load_lessons(self, session: AsyncSession, project_id: str) -> list[Memory]:
        res = await session.execute(
            select(Memory).where(
                Memory.project_id == project_id,
                Memory.memory_type == "LESSON",
                Memory.status.notin_(
                    [MemoryState.ARCHIVED.value, MemoryState.INVALIDATED.value]
                ),
            )
        )
        return list(res.scalars().all())

    # ------------------------------------------------------------ clustering

    def _cluster(self, episodes: list[Memory]) -> list[list[Memory]]:
        """Group related episodes by embedding similarity and lexical overlap."""
        clusters: list[list[Memory]] = []
        assigned: set[str] = set()

        for i, first in enumerate(episodes):
            if first.id in assigned:
                continue
            vector_a = (first.embedding or {}).get("vector")
            cluster = [first]
            assigned.add(first.id)

            for j, other in enumerate(episodes):
                if other.id in assigned or i == j:
                    continue
                vector_b = (other.embedding or {}).get("vector")
                similarity = (
                    cosine_similarity(vector_a, vector_b) if vector_a and vector_b else 0.0
                )
                _, tokens_a = canonicalize(f"{first.title} {first.summary}")
                _, tokens_b = canonicalize(f"{other.title} {other.summary}")
                shared = set(tokens_a) & set(tokens_b)
                overlap = len(shared) / max(1, len(set(tokens_a) | set(tokens_b)))

                if (
                    similarity >= CLUSTER_SIMILARITY_THRESHOLD
                    or overlap >= CLUSTER_OVERLAP_THRESHOLD
                    or len(shared) >= 2
                ):
                    cluster.append(other)
                    assigned.add(other.id)

            if len(cluster) >= 2:
                clusters.append(cluster)

        return clusters

    def _cluster_contains_contradiction(self, cluster: list[Memory]) -> bool:
        for index, first in enumerate(cluster):
            for other in cluster[index + 1 :]:
                is_contradiction, _ = self.conflict_resolver.detect_contradiction_heuristics(
                    first.content, other.content
                )
                if is_contradiction:
                    return True
        return False

    # -------------------------------------------------------------- proposal

    async def _propose_lesson(self, cluster: list[Memory]) -> dict[str, str]:
        """Ask the LLM for a phrasing. Its output is a proposal, nothing more."""
        summaries = "\n".join(f"- {m.title}: {m.content}" for m in cluster)
        prompt = (
            "You are an expert software architect. Given the following related "
            "episodes and failures from a software engineering project, synthesize "
            "a durable architectural lesson or constraint:\n\n"
            f"{summaries}\n\n"
            "Output format:\n"
            "TITLE: <Concise Rule Title>\n"
            "SUMMARY: <Single-sentence durable invariant>\n"
            "CONTENT: <Explanation of failure cause, invariant constraint, and verified prevention>"
        )
        response = await self.llm_provider.generate(
            prompt=prompt,
            system_prompt="Extract durable software engineering principles.",
        )

        title = f"Consolidated guideline from {len(cluster)} episodes"
        summary = "Consolidated operational invariant"
        content = response.content

        for line in response.content.splitlines():
            if line.startswith("TITLE:"):
                title = line.removeprefix("TITLE:").strip() or title
            elif line.startswith("SUMMARY:"):
                summary = line.removeprefix("SUMMARY:").strip() or summary
            elif line.startswith("CONTENT:"):
                content = line.removeprefix("CONTENT:").strip() or content

        return {
            "title": title,
            "summary": summary,
            "content": content,
            "provider": response.provider,
            "model": response.model,
        }

    async def _find_duplicate_lesson(
        self,
        session: AsyncSession,
        project_id: str,
        content: str,
        existing_lessons: list[Memory],
    ) -> Memory | None:
        """Detect a lesson that already says this, by canonical claim overlap."""
        _, proposed_tokens = canonicalize(content)
        if not proposed_tokens:
            return None
        proposed = set(proposed_tokens)

        for lesson in existing_lessons:
            _, tokens = canonicalize(lesson.content)
            if not tokens:
                continue
            existing = set(tokens)
            overlap = len(proposed & existing) / max(1, len(proposed | existing))
            if overlap >= NEAR_DUPLICATE_THRESHOLD:
                return lesson
        return None

    async def _create_candidate_lesson(
        self,
        session: AsyncSession,
        project_id: str,
        proposal: dict[str, str],
        cluster: list[Memory],
        fingerprint: str,
    ) -> Memory:
        """Create the lesson as a reviewable candidate, never as active truth."""
        embedding = await self.memory_service.embedding_provider.embed_text(
            f"{proposal['title']}\n{proposal['content']}"
        )

        lesson = Memory(
            project_id=project_id,
            layer="L5",
            memory_type="LESSON",
            title=proposal["title"],
            content=proposal["content"],
            summary=proposal["summary"],
            # LLM-proposed text enters as REVIEW_REQUIRED. It is a proposal awaiting
            # a decision, which is the whole point of section 23.
            status=MemoryState.REVIEW_REQUIRED.value,
            authority=Authority.LLM_GENERATED.value,
            epistemic_state=EpistemicState.LESSON.value,
            confidence=0.0,
            importance=0.85,
            source_type="consolidation",
            source_reference=f"cluster:{fingerprint}",
            created_by=f"consolidation:{proposal.get('provider', 'unknown')}",
            version=1,
            embedding={"vector": embedding.vector},
            embedding_model=embedding.model,
            embedding_version=embedding.version,
        )
        session.add(lesson)
        await session.flush()

        # Inherit the grounding of every source episode. The lesson is only as
        # evidenced as the episodes behind it, and that evidence is what a reviewer
        # or the verification engine will judge it on.
        inherited: set[str] = set()
        for episode in cluster:
            for evidence in episode.evidences or []:
                key = f"{evidence.file_path}:{evidence.line_start}:{evidence.evidence_hash}"
                if key in inherited:
                    continue
                inherited.add(key)
                session.add(
                    MemoryEvidence(
                        memory_id=lesson.id,
                        source_type=evidence.source_type,
                        evidence_type=evidence.evidence_type,
                        relation=evidence.relation,
                        authority=evidence.authority,
                        source_id=evidence.source_id,
                        file_path=evidence.file_path,
                        symbol_id=evidence.symbol_id,
                        commit_sha=evidence.commit_sha,
                        branch=evidence.branch,
                        workspace=evidence.workspace,
                        line_start=evidence.line_start,
                        line_end=evidence.line_end,
                        evidence_hash=evidence.evidence_hash,
                        snippet_hash=evidence.snippet_hash,
                        ast_fingerprint=evidence.ast_fingerprint,
                        confidence=evidence.confidence,
                    )
                )

            # Provenance is recorded now, at proposal time, so the derivation is
            # traceable even if the lesson is never promoted.
            session.add(
                MemoryRelation(
                    source_memory_id=lesson.id,
                    target_memory_id=episode.id,
                    relation_type="derived_from",
                    confidence=1.0,
                )
            )

        await session.flush()
        await session.refresh(lesson, ["evidences"])
        await self.claims.sync_memory_claims(session, lesson)

        scored = ConfidenceScorer.score(
            authority=Authority.LLM_GENERATED,
            evidence=EvidenceSummary.from_items(lesson.evidences),
            status=lesson.status,
        )
        lesson.confidence = scored.score
        lesson.confidence_components = {
            **scored.components,
            "explanation": scored.explanation,
            "derived_from_episodes": len(cluster),
        }
        return lesson

    # ------------------------------------------------------------- promotion

    async def _promote(self, session: AsyncSession, lesson: Memory) -> bool:
        """Activate a lesson only if its inherited evidence actually holds.

        Returns True when the lesson became ACTIVE. A lesson with no evidence is
        never promoted: an LLM's summary of episodes that themselves had no code
        grounding is not durable project knowledge, however plausible it reads.
        """
        from cortexforge.memory.verification import MemoryVerificationEngine

        if not lesson.evidences:
            logger.info(
                "Lesson %s not promoted: no inherited evidence to verify", lesson.id
            )
            return False

        verifier = MemoryVerificationEngine()
        await verifier.verify_single_memory(session, lesson, verifier="consolidation")

        if lesson.status == MemoryState.REVIEW_REQUIRED.value:
            try:
                version = MemoryLifecycleManager.transition(
                    lesson,
                    MemoryState.ACTIVE.value,
                    reason=(
                        "Consolidated lesson promoted: inherited evidence verified "
                        "against the current repository"
                    ),
                    actor="consolidation",
                    verified=True,
                )
                if version is not None:
                    session.add(version)
            except InvalidStateTransitionError as exc:
                logger.info("Lesson %s could not be promoted: %s", lesson.id, exc)
                return False

        return lesson.status == MemoryState.ACTIVE.value

    async def _archive_sources(
        self, session: AsyncSession, lesson: Memory, cluster: list[Memory]
    ) -> int:
        """Archive source episodes -- only ever called after successful promotion."""
        if lesson.status != MemoryState.ACTIVE.value:
            raise RuntimeError(
                "Refusing to archive source episodes for a lesson that was not promoted. "
                "Consolidation must never destroy what it failed to replace."
            )

        archived = 0
        for episode in cluster:
            try:
                version = MemoryLifecycleManager.transition(
                    episode,
                    MemoryState.ARCHIVED.value,
                    reason=f"Consolidated into durable lesson {lesson.id} after verification",
                    actor="consolidation",
                )
                if version is not None:
                    session.add(version)
                archived += 1
            except InvalidStateTransitionError as exc:
                logger.info("Episode %s not archived: %s", episode.id, exc)
        return archived

    async def approve_lesson(
        self,
        session: AsyncSession,
        memory_id: str,
        approver: str,
        reason: str = "",
        archive_sources: bool = True,
    ) -> Memory:
        """Approve a proposed lesson, activating it and archiving its sources.

        This is the human decision the review state exists to wait for, so the
        approved lesson takes on the approver's authority rather than remaining
        LLM-authored.
        """
        lesson = await session.get(Memory, memory_id)
        if lesson is None:
            raise ValueError(f"Memory {memory_id} does not exist.")
        if lesson.status != MemoryState.REVIEW_REQUIRED.value:
            raise ValueError(
                f"Memory {memory_id} is {lesson.status}; only REVIEW_REQUIRED "
                "memories can be approved."
            )

        version = MemoryLifecycleManager.transition(
            lesson,
            MemoryState.ACTIVE.value,
            reason=reason or f"Approved by {approver}",
            actor=approver,
            verified=True,
        )
        if version is not None:
            session.add(version)

        lesson.authority = Authority.REVIEW_CONFIRMED.value
        scored = ConfidenceScorer.score(
            authority=Authority.REVIEW_CONFIRMED,
            evidence=EvidenceSummary.from_items(lesson.evidences),
            status=lesson.status,
        )
        lesson.confidence = scored.score
        lesson.confidence_components = {
            **scored.components,
            "explanation": scored.explanation,
            "approved_by": approver,
        }

        if archive_sources:
            sources = await session.execute(
                select(Memory)
                .join(MemoryRelation, MemoryRelation.target_memory_id == Memory.id)
                .where(
                    MemoryRelation.source_memory_id == lesson.id,
                    MemoryRelation.relation_type == "derived_from",
                )
            )
            await self._archive_sources(session, lesson, list(sources.scalars().all()))

        await session.commit()
        await session.refresh(lesson)
        return lesson

    @staticmethod
    def _empty_result(message: str) -> dict[str, Any]:
        return {
            "clusters_consolidated": 0,
            "durable_memories_created": 0,
            "durable_memories_promoted": 0,
            "memories_archived": 0,
            "contradictions_detected": 0,
            "duplicates_skipped": 0,
            "auto_promote": False,
            "proposals": [],
            "message": message,
        }

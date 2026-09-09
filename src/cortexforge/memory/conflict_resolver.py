"""Semantic conflict detection and authority-based resolution (sections 6, 10, 18).

Two memories that disagree are arbitrated on *authority first*, not on confidence.
A confident LLM-generated statement cannot supersede a user decision, an approved
review, a verified test, or direct code evidence, however sure of itself it sounds.
Confidence only breaks ties within one authority level.

Contradiction is also distinguished from historical succession (section 18): a
memory that was true at commit A and a memory that became true at commit B are not
in conflict, they are two points on a timeline. Treating succession as conflict is
how a memory layer ends up permanently uncertain about a project that simply
changed.

When authority and evidence genuinely do not settle a disagreement, both memories
are marked CONFLICTED and the dispute is surfaced rather than resolved by
preference (section 30).
"""

import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.cognition.authority import (
    Authority,
    authority_from_source,
    authority_rank,
    is_proposal_only,
    resolve_authority_conflict,
)
from cortexforge.core.models import Memory
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState


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


# Antonym / polarity pairs indicating semantic contradiction
CONTRADICTION_PATTERNS = [
    (r"\brequired\b", r"\b(not required|no longer required|optional|unnecessary)\b"),
    (r"\buses\b|\busing\b|\benabled\b", r"\b(removed|deprecated|disabled|replaced with|migrated from)\b"),
    (r"\bmust\b|\balways\b", r"\b(never|should not|must not|prohibited)\b"),
    (r"\bsynchronous\b|\bsync\b", r"\b(asynchronous|async)\b"),
    (r"\bdeprecated\b", r"\b(recommended|standard|current)\b"),
    (r"\bsupports\b|\bsupported\b", r"\b(unsupported|no longer supported|dropped support)\b"),
]


@dataclass
class ConflictDetection:
    """Detected conflict pair with analytical details."""

    existing_memory_id: str
    existing_title: str
    candidate_title: str
    similarity: float
    is_contradiction: bool
    contradiction_reason: str
    authority_winner: str  # "candidate", "existing", or "unresolved"
    action_taken: str  # "superseded_existing", "conflicted_both", "rejected_candidate", "temporal_succession"
    resolution_reason: str = ""
    candidate_authority: str = Authority.AGENT_OBSERVED.value
    existing_authority: str = Authority.AGENT_OBSERVED.value


class ConflictResolver:
    """Detects and resolves semantic contradictions and superseded knowledge."""

    def __init__(self, semantic_similarity_threshold: float = 0.58) -> None:
        self.sim_threshold = semantic_similarity_threshold

    def detect_contradiction_heuristics(self, text_a: str, text_b: str) -> tuple[bool, str]:
        """Detect opposing polarity or explicit contradiction between two text passages."""
        a_lower = text_a.lower()
        b_lower = text_b.lower()

        for pat_pos, pat_neg in CONTRADICTION_PATTERNS:
            # Check if text_a has positive and text_b has negative, or vice versa
            pos_in_a = bool(re.search(pat_pos, a_lower))
            neg_in_a = bool(re.search(pat_neg, a_lower))
            pos_in_b = bool(re.search(pat_pos, b_lower))
            neg_in_b = bool(re.search(pat_neg, b_lower))

            if (pos_in_a and neg_in_b) or (neg_in_a and pos_in_b):
                return True, f"Opposing predicates detected matching pattern ({pat_pos} vs {pat_neg})"

        # Check for explicit removal/replacement mention
        if ("no longer" in b_lower or "removed" in b_lower or "replaced" in b_lower) and any(
            w in b_lower for w in a_lower.split() if len(w) > 4
        ):
            return True, "Candidate explicitly references removal or supersession of subject in existing memory"

        return False, ""

    async def check_and_resolve(
        self,
        session: AsyncSession,
        project_id: str,
        candidate_memory: Memory,
        candidate_vector: list[float] | None = None,
    ) -> list[ConflictDetection]:
        """Examine active and candidate memories for contradictions and resolve authority."""
        # Ensure candidate_memory has its evidences loaded to avoid greenlet lazy load issues
        cand_stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(Memory.id == candidate_memory.id)
        )
        cand_res = await session.execute(cand_stmt)
        cand_loaded = cand_res.scalars().first()
        if cand_loaded:
            candidate_memory = cand_loaded

        # Conflict detection is scoped to one branch.
        #
        # Two branches are allowed to believe different things: they describe
        # different code, and that is the point of a branch (section 19). A
        # memory recorded on `feature` contradicting one on `main` is not a
        # conflict to resolve here -- it is a divergence, and reconciling it is
        # the merge's job (section 20). Resolving it at creation time would let
        # work on one branch silently supersede knowledge on another that was
        # never merged.
        #
        # Memories with no branch are project-wide and participate everywhere.
        branch_scope = [Memory.branch.is_(None)]
        if candidate_memory.branch:
            branch_scope.append(Memory.branch == candidate_memory.branch)

        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.id != candidate_memory.id,
                or_(*branch_scope),
                Memory.status.in_([
                    MemoryState.ACTIVE.value,
                    MemoryState.UNVERIFIED.value,
                    MemoryState.CONFLICTED.value,
                    MemoryState.STALE.value,
                    # Proposals are included: a candidate contradicted by a
                    # higher-authority statement should be resolved now, not left
                    # waiting for a reviewer to discover it is already refuted.
                    MemoryState.CANDIDATE.value,
                    MemoryState.REVIEW_REQUIRED.value,
                ]),
            )
        )
        res = await session.execute(stmt)
        existing_memories = list(res.scalars().all())

        c_vec = candidate_vector
        if not c_vec and candidate_memory.embedding:
            c_vec = candidate_memory.embedding.get("vector")

        c_text = f"{candidate_memory.title} {candidate_memory.content}"
        c_authority = authority_from_source(
            candidate_memory.authority or candidate_memory.source_type
        )

        conflicts: list[ConflictDetection] = []

        for existing in existing_memories:
            # 1. Semantic overlap check
            m_vec = (existing.embedding or {}).get("vector")
            sim = cosine_similarity(c_vec, m_vec) if (c_vec and m_vec) else 0.0

            # Lexical entity overlap check
            c_words = set(c_text.lower().split())
            e_words = set(f"{existing.title} {existing.content}".lower().split())
            jaccard = len(c_words & e_words) / max(1, len(c_words | e_words))
            shared_content_words = {w for w in (c_words & e_words) if len(w) > 3}

            # Shared evidence files/symbols
            shared_evidence = False
            if candidate_memory.evidences and existing.evidences:
                c_files = {e.file_path for e in candidate_memory.evidences}
                e_files = {e.file_path for e in existing.evidences}
                shared_evidence = bool(c_files & e_files)

            is_overlap = (
                sim >= self.sim_threshold
                or jaccard >= 0.20
                or len(shared_content_words) >= 2
                or (shared_evidence and sim >= 0.35)
            )
            if not is_overlap:
                continue

            # 2. Contradiction detection
            is_contra, contra_reason = self.detect_contradiction_heuristics(
                existing.content, candidate_memory.content
            )
            if not is_contra:
                # Same topic, but no contradiction (complementary or elaboration)
                continue

            # 3. Historical succession is not contradiction (section 18).
            #    If the existing memory's validity window already closed before the
            #    candidate's opened, the two describe different eras of the project
            #    and neither is wrong.
            if self._is_temporal_succession(existing, candidate_memory):
                conflicts.append(
                    ConflictDetection(
                        existing_memory_id=existing.id,
                        existing_title=existing.title,
                        candidate_title=candidate_memory.title,
                        similarity=round(sim, 3),
                        is_contradiction=False,
                        contradiction_reason=contra_reason,
                        authority_winner="candidate",
                        action_taken="temporal_succession",
                        resolution_reason=(
                            "The earlier memory's validity window closed before this one "
                            "opened, so this is the project changing over time rather than "
                            "two statements disagreeing about the same moment."
                        ),
                        candidate_authority=c_authority.value,
                        existing_authority=authority_from_source(
                            existing.authority or existing.source_type
                        ).value,
                    )
                )
                continue

            # 4. Authority-first arbitration. Authority decides outright when the
            #    levels differ; confidence is only a tie-breaker within a level.
            e_authority = authority_from_source(existing.authority or existing.source_type)

            # Direct code grounding raises the *effective* authority of an otherwise
            # unverified statement, because a claim anchored in code that a parser
            # confirmed is better sourced than the same words with no anchor.
            has_c_verified_code = any(
                (e.authority or e.source_type or "").lower()
                in ("code", "verified_code", "code_verified")
                for e in (candidate_memory.evidences or [])
            )
            has_e_verified_code = any(
                (e.authority or e.source_type or "").lower()
                in ("code", "verified_code", "code_verified")
                for e in (existing.evidences or [])
            )
            if has_c_verified_code and authority_rank(c_authority) < authority_rank(
                Authority.CODE_VERIFIED
            ):
                c_authority = Authority.CODE_VERIFIED
            if has_e_verified_code and authority_rank(e_authority) < authority_rank(
                Authority.CODE_VERIFIED
            ):
                e_authority = Authority.CODE_VERIFIED

            c_ts = candidate_memory.created_at.timestamp() if candidate_memory.created_at else 0.0
            e_ts = existing.created_at.timestamp() if existing.created_at else 0.0
            is_candidate_newer = c_ts >= e_ts

            # A memory that has already failed verification does not get to defend
            # its position on confidence. Its stored confidence reflects what was
            # believed when it was last checked, and it has since been contradicted
            # by the repository -- so a newer statement at no lower authority
            # supersedes it outright rather than tying with it.
            existing_already_disbelieved = existing.status in (
                MemoryState.STALE.value,
                MemoryState.CONFLICTED.value,
            )
            if (
                existing_already_disbelieved
                and is_candidate_newer
                and authority_rank(c_authority) >= authority_rank(e_authority)
            ):
                winner = "a"
                resolution_reason = (
                    f"the existing memory is {existing.status} -- it already failed "
                    f"verification -- and this newer statement is at no lower authority "
                    f"({c_authority.value} vs {e_authority.value})"
                )
            else:
                winner, resolution_reason = resolve_authority_conflict(
                    c_authority,
                    e_authority,
                    a_confidence=candidate_memory.confidence,
                    b_confidence=existing.confidence,
                    a_recency_wins=(is_candidate_newer and has_c_verified_code),
                )

            if winner == "a":
                authority_winner = "candidate"
                conflict_id = existing.conflict_group or f"conf-{uuid.uuid4().hex[:8]}"
                existing.conflict_group = conflict_id
                candidate_memory.conflict_group = conflict_id
                existing.superseded_by_id = candidate_memory.id
                candidate_memory.supersedes_id = existing.id

                # Close the superseded memory's validity window
                existing.valid_to_time = candidate_memory.valid_from_time or datetime.now(UTC)
                if candidate_memory.valid_from_commit or candidate_memory.source_commit:
                    existing.valid_to_commit = candidate_memory.valid_from_commit or candidate_memory.source_commit

                # A statement that was never believed is *rejected*, not superseded.
                # Supersession says "this used to be our position"; a candidate that
                # was refuted before anyone accepted it never was, and recording it
                # as superseded would put a claim in the project's history that the
                # project never actually held.
                was_believed = existing.status not in (
                    MemoryState.CANDIDATE.value,
                    MemoryState.REVIEW_REQUIRED.value,
                )
                target = (
                    MemoryState.SUPERSEDED.value if was_believed else MemoryState.INVALIDATED.value
                )
                action = "superseded_existing" if was_believed else "rejected_existing_proposal"
                verb = "Superseded" if was_believed else "Rejected before activation"

                ver = MemoryLifecycleManager.transition(
                    existing,
                    target,
                    reason=(
                        f"{verb} by memory {candidate_memory.id} "
                        f"({resolution_reason}): {contra_reason}"
                    ),
                    superseded_by_id=candidate_memory.id if was_believed else None,
                    conflict_group=conflict_id,
                    commit_sha=existing.valid_to_commit,
                )
                if ver:
                    session.add(ver)


            elif winner == "b" and has_e_verified_code and not has_c_verified_code:
                # The existing memory is code-grounded and the candidate is not.
                # The candidate does not get to overwrite verified knowledge with an
                # unevidenced assertion (section 23) -- but it is not thrown away
                # either. It may well be true and simply lack grounding, so it is
                # held for review or evidence rather than being killed off:
                # INVALIDATED is terminal, and a statement nobody has disproven has
                # not earned that. Only proposal-only sources (an LLM, repository
                # prose, untrusted input) are rejected outright, because there is
                # nobody to come back with evidence for them.
                authority_winner = "existing"
                conflict_id = existing.conflict_group or f"conf-{uuid.uuid4().hex[:8]}"
                existing.conflict_group = conflict_id
                candidate_memory.conflict_group = conflict_id

                if is_proposal_only(c_authority):
                    target = MemoryState.INVALIDATED.value
                    action = "rejected_candidate"
                    detail = (
                        f"Rejected: {c_authority.value} output contradicts code-grounded "
                        f"memory {existing.id} and presents no evidence of its own"
                    )
                else:
                    target = MemoryState.REVIEW_REQUIRED.value
                    action = "candidate_held_for_review"
                    detail = (
                        f"Held for review: contradicts code-grounded memory "
                        f"{existing.id} without presenting code evidence. It may be "
                        "correct, but it cannot displace verified knowledge until it "
                        "is grounded or a reviewer decides"
                    )

                ver = MemoryLifecycleManager.transition(
                    candidate_memory,
                    target,
                    reason=f"{detail} ({resolution_reason})",
                    conflict_group=conflict_id,
                )
                if ver:
                    session.add(ver)

            else:
                # Neither authority nor evidence settles it. Both memories are held
                # as CONFLICTED and the disagreement is surfaced, because inventing
                # a winner here is exactly how a memory layer starts asserting
                # things it has no grounds for (section 30).
                authority_winner = "unresolved" if winner == "unresolved" else "existing"
                action = "conflicted_both"

                conflict_id = existing.conflict_group or f"conf-{uuid.uuid4().hex[:8]}"
                existing.conflict_group = conflict_id
                candidate_memory.conflict_group = conflict_id

                ver_e = MemoryLifecycleManager.transition(
                    existing,
                    MemoryState.CONFLICTED.value,
                    reason=f"Conflict detected with memory {candidate_memory.id}: {contra_reason}",
                    conflict_group=conflict_id,
                )
                if ver_e:
                    session.add(ver_e)

                ver_c = MemoryLifecycleManager.transition(
                    candidate_memory,
                    MemoryState.CONFLICTED.value,
                    reason=f"Conflict detected with memory {existing.id}: {contra_reason}",
                    conflict_group=conflict_id,
                )
                if ver_c:
                    session.add(ver_c)

            conflicts.append(
                ConflictDetection(
                    existing_memory_id=existing.id,
                    existing_title=existing.title,
                    candidate_title=candidate_memory.title,
                    similarity=round(sim, 3),
                    is_contradiction=True,
                    contradiction_reason=contra_reason,
                    authority_winner=authority_winner,
                    action_taken=action,
                    resolution_reason=resolution_reason,
                    candidate_authority=c_authority.value,
                    existing_authority=e_authority.value,
                )
            )

        return conflicts

    @staticmethod
    def _is_temporal_succession(earlier: Memory, later: Memory) -> bool:
        """True when two memories describe different eras rather than disagreeing.

        Succession requires an explicit, closed validity window on the earlier
        memory that does not overlap the later one's. Absent that evidence the
        answer is False: an unstated timeline is not a licence to assume the
        disagreement away.
        """
        if not earlier.valid_to_commit and not earlier.valid_to_time:
            return False
        if (
            earlier.valid_to_commit
            and later.valid_from_commit
            and later.valid_from_commit == earlier.valid_to_commit
        ):
            return True
        if earlier.valid_to_time and later.valid_from_time:
            return _as_utc(earlier.valid_to_time) <= _as_utc(later.valid_from_time)
        if earlier.valid_to_commit and later.valid_from_commit:
            return True
        if earlier.valid_to_time:
            later_time = later.valid_from_time or later.created_at
            if later_time:
                return _as_utc(earlier.valid_to_time) <= _as_utc(later_time)
        # The earlier memory is explicitly closed and the later one is open-ended:
        # treat as succession only when the later memory actually postdates it.
        if later.valid_from_commit or later.valid_from_time:
            return True
        return _as_utc(earlier.created_at) < _as_utc(later.created_at)


def _as_utc(value: datetime | None) -> datetime:
    """Normalize a timestamp to an aware UTC datetime for comparison.

    SQLite returns naive datetimes while PostgreSQL returns aware ones, so
    comparing two timestamps directly raises depending on which database is in
    use. Normalizing here keeps temporal reasoning backend-independent.
    """
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)

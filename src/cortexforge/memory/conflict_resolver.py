"""Automated semantic conflict and contradiction resolution engine."""

import math
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import Memory
from cortexforge.memory.confidence import SOURCE_AUTHORITY_WEIGHTS
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
    action_taken: str  # "superseded_existing", "conflicted_both", "rejected_candidate"


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

        # Find active or conflicted memories in the same project and layer
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.id != candidate_memory.id,
                Memory.status.in_([
                    MemoryState.ACTIVE.value,
                    MemoryState.UNVERIFIED.value,
                    MemoryState.CONFLICTED.value,
                    MemoryState.STALE.value,
                ]),
            )
        )
        res = await session.execute(stmt)
        existing_memories = list(res.scalars().all())

        c_vec = candidate_vector
        if not c_vec and candidate_memory.embedding:
            c_vec = candidate_memory.embedding.get("vector")

        c_text = f"{candidate_memory.title} {candidate_memory.content}"
        c_auth = SOURCE_AUTHORITY_WEIGHTS.get(candidate_memory.source_type.lower(), 0.5)

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

            # 3. Authority & Evidence Arbitration
            e_auth = SOURCE_AUTHORITY_WEIGHTS.get(existing.source_type.lower(), 0.5)

            # Code-grounded evidences win over ungrounded claims
            has_c_verified_code = any(e.source_type in ("code", "verified_code") for e in (candidate_memory.evidences or []))
            has_e_verified_code = any(e.source_type in ("code", "verified_code") for e in (existing.evidences or []))

            # Commit recency: candidate is newer
            c_ts = candidate_memory.created_at.timestamp() if candidate_memory.created_at else 0.0
            e_ts = existing.created_at.timestamp() if existing.created_at else 0.0
            is_candidate_newer = c_ts >= e_ts

            if (c_auth > e_auth) or (c_auth >= e_auth and has_c_verified_code and is_candidate_newer):
                # Candidate wins -> Existing is SUPERSEDED
                authority_winner = "candidate"
                action = "superseded_existing"

                conflict_id = existing.conflict_group or f"conf-{uuid.uuid4().hex[:8]}"
                existing.conflict_group = conflict_id
                candidate_memory.conflict_group = conflict_id
                existing.superseded_by_id = candidate_memory.id
                candidate_memory.supersedes_id = existing.id

                ver = MemoryLifecycleManager.transition(
                    existing,
                    MemoryState.SUPERSEDED.value,
                    reason=f"Superseded by higher-authority/newer memory {candidate_memory.id}: {contra_reason}",
                    superseded_by_id=candidate_memory.id,
                    conflict_group=conflict_id,
                )
                if ver:
                    session.add(ver)


            elif e_auth > c_auth and has_e_verified_code and not has_c_verified_code:
                # Existing has verified code and candidate is ungrounded -> Candidate is rejected / conflicted
                authority_winner = "existing"
                action = "rejected_candidate"

                ver = MemoryLifecycleManager.transition(
                    candidate_memory,
                    MemoryState.INVALIDATED.value,
                    reason=f"Rejected: contradicts verified existing memory {existing.id} without code evidence",
                )
                if ver:
                    session.add(ver)

            else:
                # Unresolved ambiguity between two conflicting claims -> mark both CONFLICTED
                authority_winner = "unresolved"
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
                )
            )

        return conflicts

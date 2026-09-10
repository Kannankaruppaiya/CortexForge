"""Memory verification, expressed in terms of claim verification.

This module used to answer a coarse question -- "do this memory's evidence hashes
still match?" -- and had two defects the specification calls out directly:

* it treated a memory as atomically true or stale, with no way to say that one
  sentence in it survived a change and another did not (section 4); and
* it added ``+0.05`` to confidence on every successful check, so re-verifying
  unchanged evidence manufactured certainty out of repetition (section 8).

It now delegates to :class:`~cortexforge.verification.engine.ClaimVerificationEngine`.
A memory's state is derived from the states of the propositions inside it, and its
confidence is *recomputed* from evidence and the latest outcome rather than nudged.
A memory is exactly as sound as its weakest claim.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider
from cortexforge.cognition.epistemics import ClaimStatus
from cortexforge.core.models import Claim, Memory, Project
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.verification.engine import ClaimVerificationEngine

logger = logging.getLogger(__name__)

# How the worst claim in a memory maps onto that memory's lifecycle state.
#
# REFUTED means the proposition's referent is gone or disproven, which
# invalidates the memory. PARTIALLY_VERIFIED means the code still exists but
# changed underneath the claim -- the memory needs revisiting, so STALE. UNKNOWN
# leaves the memory where it is: not knowing is not grounds for demotion.
_WORST_CLAIM_TO_STATE: dict[str, str | None] = {
    ClaimStatus.REFUTED.value: MemoryState.INVALIDATED.value,
    ClaimStatus.CONFLICTED.value: MemoryState.CONFLICTED.value,
    ClaimStatus.STALE.value: MemoryState.STALE.value,
    ClaimStatus.PARTIALLY_VERIFIED.value: MemoryState.STALE.value,
    ClaimStatus.UNKNOWN.value: None,
    ClaimStatus.PROPOSED.value: None,
    ClaimStatus.UNVERIFIED.value: None,
    ClaimStatus.VERIFIED.value: MemoryState.ACTIVE.value,
}

# Ordering used to pick the governing claim: the worst one wins.
_CLAIM_SEVERITY: dict[str, int] = {
    ClaimStatus.REFUTED.value: 6,
    ClaimStatus.CONFLICTED.value: 5,
    ClaimStatus.STALE.value: 4,
    ClaimStatus.PARTIALLY_VERIFIED.value: 3,
    ClaimStatus.UNKNOWN.value: 2,
    ClaimStatus.PROPOSED.value: 2,
    ClaimStatus.UNVERIFIED.value: 1,
    ClaimStatus.VERIFIED.value: 0,
}


class MemoryVerificationEngine:
    """Verifies memories by verifying the claims they contain."""

    def __init__(self, parser_provider: TreeSitterProvider | None = None) -> None:
        self.parser = parser_provider or TreeSitterProvider()
        self.claims = ClaimService()
        self.claim_engine = ClaimVerificationEngine(parser_provider=self.parser)

    async def verify_project_memories(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str | None = None,
        branch: str | None = None,
        workspace: str | None = None,
        verifier: str = "system",
    ) -> dict[str, int]:
        """Verify every verifiable memory in a project.

        Returns counts keyed ``verified`` / ``stale`` / ``deprecated`` / ``unknown``.
        ``unknown`` is reported rather than folded into either of the other two:
        a project where half the memories cannot be checked is a materially
        different situation from one where half were confirmed (section 30).
        """
        project = await session.get(Project, project_id)
        if not project:
            return {"verified": 0, "stale": 0, "deprecated": 0, "unknown": 0}

        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences), selectinload(Memory.claims))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(
                    [
                        MemoryState.ACTIVE.value,
                        MemoryState.UNVERIFIED.value,
                        MemoryState.STALE.value,
                        MemoryState.CANDIDATE.value,
                    ]
                ),
            )
        )
        memories = list((await session.execute(stmt)).scalars().all())
        if not memories:
            return {"verified": 0, "stale": 0, "deprecated": 0, "unknown": 0}

        # Make sure every memory's propositions exist before anything is checked.
        claim_ids: list[str] = []
        for memory in memories:
            claims = await self.claims.sync_memory_claims(
                session, memory, commit_sha=commit_sha
            )
            claim_ids.extend(c.id for c in claims)

        await self.claim_engine.verify_project(
            session,
            project_id,
            commit_sha=commit_sha,
            branch=branch,
            workspace=workspace,
            verifier=verifier,
            trigger="memory_verification",
            claim_ids=claim_ids,
        )

        counts = {"verified": 0, "stale": 0, "deprecated": 0, "unknown": 0}
        for memory in memories:
            status = await self._apply_claim_outcomes(
                session, memory, commit_sha, verifier
            )
            if status == MemoryState.ACTIVE.value:
                counts["verified"] += 1
            elif status == MemoryState.STALE.value:
                counts["stale"] += 1
            elif status in (
                MemoryState.SUPERSEDED.value,
                MemoryState.INVALIDATED.value,
            ):
                counts["deprecated"] += 1
            else:
                counts["unknown"] += 1

        await session.commit()
        return counts

    async def verify_single_memory(
        self,
        session: AsyncSession,
        memory: Memory,
        project_root: str | None = None,
        commit_sha: str | None = None,
        verifier: str = "system",
    ) -> str:
        """Verify one memory and return its resulting lifecycle state.

        ``project_root`` is accepted for call-site compatibility; the path is
        resolved from the project record so that evidence can never be checked
        against a directory the caller nominated (section 41).
        """
        claims = await self.claims.sync_memory_claims(
            session, memory, commit_sha=commit_sha
        )
        if not claims:
            # Nothing evaluable was found in the text. That is a statement about the
            # memory, not a verdict on it: the state is left untouched.
            memory.last_verified_at = datetime.now(UTC)
            return memory.status

        await self.claim_engine.verify_project(
            session,
            memory.project_id,
            commit_sha=commit_sha,
            verifier=verifier,
            trigger="memory_verification",
            claim_ids=[c.id for c in claims],
        )
        return await self._apply_claim_outcomes(session, memory, commit_sha, verifier)

    async def _apply_claim_outcomes(
        self,
        session: AsyncSession,
        memory: Memory,
        commit_sha: str | None,
        verifier: str,
    ) -> str:
        """Derive a memory's state and confidence from its claims' verdicts."""
        res = await session.execute(
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .where(
                Claim.memory_id == memory.id,
                Claim.status != ClaimStatus.RETIRED.value,
            )
        )
        claims = list(res.scalars().all())
        if not claims:
            memory.last_verified_at = datetime.now(UTC)
            return memory.status

        worst = max(claims, key=lambda c: _CLAIM_SEVERITY.get(c.status, 0))
        target_state = _WORST_CLAIM_TO_STATE.get(worst.status)

        if target_state and target_state != memory.status:
            reason = (
                f'Claim "{worst.text[:120]}" resolved to {worst.status}: '
                f"{(worst.confidence_components or {}).get('explanation', 'see verification result')}"
            )
            try:
                version = MemoryLifecycleManager.transition(
                    memory,
                    new_state=target_state,
                    reason=reason,
                    actor=verifier,
                    commit_sha=commit_sha,
                    # Re-activation is permitted here precisely because this call
                    # site *is* the verification that earned it.
                    verified=(target_state == MemoryState.ACTIVE.value),
                )
                if version is not None:
                    session.add(version)
            except InvalidStateTransitionError as exc:
                logger.info(
                    "Verification transition refused for memory %s: %s", memory.id, exc
                )

        # Confidence is recomputed from scratch. There is deliberately no code path
        # here that reads the old confidence -- see the module docstring.
        summary = EvidenceSummary.from_items(memory.evidences)
        scored = ConfidenceScorer.score(
            authority=memory.authority or memory.source_type,
            evidence=summary,
            last_outcome=worst.last_outcome,
            last_verified_at=worst.last_verified_at,
            status=memory.status,
            conflict_group=memory.conflict_group,
        )
        memory.confidence = scored.score
        memory.confidence_components = {
            **scored.components,
            "explanation": scored.explanation,
            "governing_claim_id": worst.id,
            "governing_claim_status": worst.status,
        }
        memory.last_verified_at = datetime.now(UTC)
        memory.updated_at = datetime.now(UTC)
        return memory.status

    async def memory_verification_report(
        self, session: AsyncSession, memory: Memory
    ) -> dict[str, Any]:
        """Per-claim verification detail for a memory, for API and MCP display."""
        res = await session.execute(
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .where(
                Claim.memory_id == memory.id, Claim.status != ClaimStatus.RETIRED.value
            )
        )
        claims = list(res.scalars().all())
        return {
            "memory_id": memory.id,
            "status": memory.status,
            "confidence": memory.confidence,
            "confidence_explanation": (memory.confidence_components or {}).get(
                "explanation"
            ),
            "claims": [
                {
                    "id": claim.id,
                    "text": claim.text,
                    "status": claim.status,
                    "last_outcome": claim.last_outcome,
                    "confidence": claim.confidence,
                    "authority": claim.authority,
                    "evidence_count": len(claim.evidence_links or []),
                    "explanation": (claim.confidence_components or {}).get(
                        "explanation"
                    ),
                }
                for claim in claims
            ],
        }


def project_root_for(project: Project) -> str:
    """Canonical on-disk root for a project."""
    return os.path.realpath(project.local_path)

"""Cognitive reconciliation engine (specification section 9).

Change impact analysis and cognitive reconciliation are deliberately separate
concerns. Impact analysis answers a mechanical question -- *what did this diff
touch?* Reconciliation answers a cognitive one -- *given what it touched, what
should we now believe?* Conflating them is what produced the previous behaviour
where every memory grounded in a modified file was bluntly marked stale.

The pipeline is:

    change -> affected evidence -> affected claims -> claim evaluation -> decision

and every decision is written to ``memory_decisions`` with its code, reason code,
reason, consulted evidence, and the memory versions on either side. Nothing about
why a memory changed state is left implicit, so reconciliation can be audited,
replayed and benchmarked (sections 36 and 50).

Decisions are idempotent: replaying the same change produces the same decision row
rather than a second one (section 37).
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.treesitter.semantic_diff import (
    SemanticChange,
    SemanticChangeType,
)
from cortexforge.cognition.epistemics import (
    ClaimStatus,
    DecisionCode,
    ReasonCode,
    VerificationOutcome,
)
from cortexforge.core.models import (
    Claim,
    ClaimEvidence,
    CodeEntity,
    Memory,
    MemoryDecision,
)
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.verification.engine import ClaimVerificationEngine

logger = logging.getLogger(__name__)

# Which lifecycle state each decision drives the memory to. KEEP, REANCHOR and
# UNKNOWN deliberately map to no transition: re-anchoring evidence to a renamed
# symbol maintains grounding without changing what is believed, and UNKNOWN is a
# statement about our knowledge, not grounds for demoting the memory.
_DECISION_TO_STATE: dict[str, str | None] = {
    DecisionCode.KEEP.value: None,
    DecisionCode.REANCHOR.value: None,
    DecisionCode.UNKNOWN.value: None,
    DecisionCode.AMEND.value: None,
    DecisionCode.REVISE.value: MemoryState.STALE.value,
    DecisionCode.STALE.value: MemoryState.STALE.value,
    DecisionCode.CONFLICT.value: MemoryState.CONFLICTED.value,
    DecisionCode.SUPERSEDE.value: MemoryState.SUPERSEDED.value,
    DecisionCode.INVALIDATE.value: MemoryState.INVALIDATED.value,
}


@dataclass
class ReconciliationOutcome:
    """One memory's reconciliation result."""

    memory_id: str
    memory_title: str
    decision: str
    reason_code: str
    reason: str
    claim_id: str | None = None
    previous_status: str | None = None
    new_status: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReconciliationReport:
    """Everything reconciliation concluded from one change."""

    project_id: str
    change_set_id: str | None
    commit_sha: str | None
    claims_evaluated: int = 0
    outcomes: list[ReconciliationOutcome] = field(default_factory=list)
    verification_run_id: str | None = None
    reanchored_symbols: list[str] = field(default_factory=list)

    def by_decision(self, decision: str) -> list[ReconciliationOutcome]:
        """Outcomes carrying a given decision code."""
        return [o for o in self.outcomes if o.decision == decision]

    @property
    def summary(self) -> dict[str, int]:
        """Count of outcomes per decision code."""
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.decision] = counts.get(outcome.decision, 0) + 1
        return counts


class MemoryReconciliationEngine:
    """Decides what to believe after a change, and records why."""

    def __init__(
        self,
        claim_service: ClaimService | None = None,
        verification_engine: ClaimVerificationEngine | None = None,
    ) -> None:
        self.claims = claim_service or ClaimService()
        self.verifier = verification_engine or ClaimVerificationEngine()

    @staticmethod
    def compute_decision_key(
        memory_id: str,
        change_set_id: str | None,
        decision: str,
        reason_code: str,
        commit_sha: str | None,
    ) -> str:
        """Identity of a decision, so replaying a change does not duplicate it."""
        material = "|".join(
            [memory_id, change_set_id or "", decision, reason_code, commit_sha or ""]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def reconcile(
        self,
        session: AsyncSession,
        project_id: str,
        semantic_changes: list[SemanticChange],
        changed_files: list[str] | None = None,
        change_set_id: str | None = None,
        commit_sha: str | None = None,
        branch: str | None = None,
        workspace: str | None = None,
        apply_transitions: bool = True,
        actor: str = "reconciliation",
    ) -> ReconciliationReport:
        """Reconcile project memory against a set of semantic changes."""
        report = ReconciliationReport(
            project_id=project_id, change_set_id=change_set_id, commit_sha=commit_sha
        )

        # 1. change -> affected evidence -> affected claims.
        affected_claims = await self._affected_claims(
            session, project_id, semantic_changes, changed_files
        )
        report.claims_evaluated = len(affected_claims)
        if not affected_claims:
            return report

        # 2. Re-anchor evidence for renamed and moved symbols *before* verifying, so
        #    that a rename is recognised as continuity rather than as a deletion.
        reanchored = await self._reanchor_renames(session, affected_claims, semantic_changes)
        report.reanchored_symbols = reanchored
        await session.flush()

        # 3. Claim evaluation against the repository as it now stands.
        run = await self.verifier.verify_project(
            session,
            project_id,
            commit_sha=commit_sha,
            branch=branch,
            workspace=workspace,
            verifier=actor,
            trigger="reconciliation",
            claim_ids=[c.id for c in affected_claims],
        )
        report.verification_run_id = run.id

        # 4. Claim outcomes -> memory decisions.
        memories = await self._load_memories(session, affected_claims)
        for memory in memories.values():
            claims = [c for c in affected_claims if c.memory_id == memory.id]
            if not claims:
                continue
            outcome = await self._decide(
                session,
                memory=memory,
                claims=claims,
                semantic_changes=semantic_changes,
                reanchored=reanchored,
                change_set_id=change_set_id,
                commit_sha=commit_sha,
                branch=branch,
                workspace=workspace,
                verification_run_id=run.id,
                apply_transitions=apply_transitions,
                actor=actor,
            )
            report.outcomes.append(outcome)

        await session.flush()
        return report

    # ------------------------------------------------------------- pipeline

    async def _affected_claims(
        self,
        session: AsyncSession,
        project_id: str,
        semantic_changes: list[SemanticChange],
        changed_files: list[str] | None,
    ) -> list[Claim]:
        """Claims whose evidence points at something this change touched.

        Only these claims are re-evaluated. A project with ten thousand claims and a
        one-symbol change evaluates the handful that could possibly be affected
        (section 55), rather than re-verifying everything.
        """
        files = {f.replace("\\", "/") for f in (changed_files or [])}
        files.update(change.file_path.replace("\\", "/") for change in semantic_changes)
        qualified = {change.qualified_name for change in semantic_changes if change.qualified_name}
        # A rename's old identity is what existing evidence still points at.
        for change in semantic_changes:
            old = change.details.get("renamed_from") or change.details.get("old_qualified_name")
            if old:
                qualified.add(old)

        return await self.claims.find_claims_touching(
            session, project_id, file_paths=sorted(files), qualified_names=sorted(qualified)
        )

    async def _reanchor_renames(
        self,
        session: AsyncSession,
        claims: list[Claim],
        semantic_changes: list[SemanticChange],
    ) -> list[str]:
        """Point evidence at the new location of renamed or moved symbols.

        A rename is not a deletion followed by a creation; it is the same logical
        symbol under a new name. Re-anchoring keeps the claim grounded so that
        verification confirms it rather than reporting the old name as missing.
        """
        renames: dict[str, SemanticChange] = {}
        for change in semantic_changes:
            if change.change_type not in (
                SemanticChangeType.SYMBOL_RENAMED,
                SemanticChangeType.SYMBOL_MOVED,
            ):
                continue
            old = (
                change.details.get("renamed_from")
                or change.details.get("old_qualified_name")
                or change.details.get("old_name")
            )
            if old:
                renames[old] = change

        if not renames:
            return []

        reanchored: list[str] = []
        for claim in claims:
            for link in claim.evidence_links or []:
                change = renames.get(link.qualified_name or "")
                if change is None:
                    continue
                previous = link.qualified_name
                link.qualified_name = change.qualified_name
                link.file_path = change.file_path
                if change.after_line_range:
                    link.line_start, link.line_end = change.after_line_range
                if change.after_fingerprint:
                    link.ast_fingerprint = change.after_fingerprint
                link.state = "MOVED"
                link.detail = {
                    **(link.detail or {}),
                    "reanchored_from": previous,
                    "reanchor_reason": change.change_type.value,
                }

                entity = await session.execute(
                    select(CodeEntity).where(
                        CodeEntity.project_id == claim.project_id,
                        CodeEntity.qualified_name == change.qualified_name,
                    )
                )
                found = entity.scalars().first()
                if found is not None:
                    link.symbol_id = found.id

                reanchored.append(f"{previous} -> {change.qualified_name}")

        return reanchored

    async def _load_memories(
        self, session: AsyncSession, claims: list[Claim]
    ) -> dict[str, Memory]:
        memory_ids = {c.memory_id for c in claims if c.memory_id}
        if not memory_ids:
            return {}
        res = await session.execute(
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(Memory.id.in_(memory_ids))
        )
        return {m.id: m for m in res.scalars().all()}

    # ------------------------------------------------------------- decision

    async def _decide(
        self,
        session: AsyncSession,
        memory: Memory,
        claims: list[Claim],
        semantic_changes: list[SemanticChange],
        reanchored: list[str],
        change_set_id: str | None,
        commit_sha: str | None,
        branch: str | None,
        workspace: str | None,
        verification_run_id: str | None,
        apply_transitions: bool,
        actor: str,
    ) -> ReconciliationOutcome:
        """Choose and record one decision for a memory, from its claims' outcomes."""
        decision, reason_code, reason, driving_claim = self._classify(
            memory, claims, semantic_changes, reanchored
        )

        previous_status = memory.status
        previous_version = memory.version
        target_state = _DECISION_TO_STATE.get(decision)
        new_status = previous_status

        if apply_transitions and target_state and target_state != previous_status:
            try:
                version = MemoryLifecycleManager.transition(
                    memory,
                    new_state=target_state,
                    reason=reason,
                    actor=actor,
                    commit_sha=commit_sha,
                )
                if version is not None:
                    session.add(version)
                new_status = memory.status
            except InvalidStateTransitionError as exc:
                # The finite state machine refused. That is a legitimate answer, not
                # an error to route around: the decision is downgraded to UNKNOWN and
                # the refusal is recorded so a human can see what was attempted.
                logger.info("Reconciliation transition refused for %s: %s", memory.id, exc)
                decision = DecisionCode.UNKNOWN.value
                reason_code = ReasonCode.BEHAVIOUR_UNVERIFIED.value
                reason = f"{reason} (lifecycle refused {previous_status} -> {target_state}: {exc})"

        # Confidence is recomputed from the memory's evidence and its claims' latest
        # outcomes -- never adjusted by a delta (section 8).
        self._recompute_memory_confidence(memory, claims)

        evidence_payload = [
            {
                "claim_id": claim.id,
                "claim": claim.text[:200],
                "status": claim.status,
                "outcome": claim.last_outcome,
                "confidence": claim.confidence,
            }
            for claim in claims
        ]

        key = self.compute_decision_key(memory.id, change_set_id, decision, reason_code, commit_sha)
        existing = await session.execute(
            select(MemoryDecision).where(
                MemoryDecision.project_id == memory.project_id,
                MemoryDecision.idempotency_key == key,
            )
        )
        if existing.scalars().first() is None:
            session.add(
                MemoryDecision(
                    project_id=memory.project_id,
                    memory_id=memory.id,
                    claim_id=driving_claim.id if driving_claim else None,
                    change_set_id=change_set_id,
                    verification_run_id=verification_run_id,
                    decision=decision,
                    reason_code=reason_code,
                    reason=reason,
                    evidence=evidence_payload,
                    previous_status=previous_status,
                    new_status=new_status,
                    previous_version=previous_version,
                    new_version=memory.version,
                    commit_sha=commit_sha,
                    branch=branch,
                    workspace=workspace,
                    actor=actor,
                    idempotency_key=key,
                )
            )

        return ReconciliationOutcome(
            memory_id=memory.id,
            memory_title=memory.title,
            decision=decision,
            reason_code=reason_code,
            reason=reason,
            claim_id=driving_claim.id if driving_claim else None,
            previous_status=previous_status,
            new_status=new_status,
            evidence=evidence_payload,
        )

    def _classify(
        self,
        memory: Memory,
        claims: list[Claim],
        semantic_changes: list[SemanticChange],
        reanchored: list[str],
    ) -> tuple[str, str, str, Claim | None]:
        """Map claim outcomes onto a decision code, reason code and explanation.

        Ordered from most to least severe. A memory is treated as unsound as soon as
        any one of its claims is refuted -- a paragraph containing one false sentence
        is not two-thirds true.
        """
        refuted = [c for c in claims if c.status == ClaimStatus.REFUTED.value]
        conflicted = [c for c in claims if c.status == ClaimStatus.CONFLICTED.value]
        partial = [c for c in claims if c.status == ClaimStatus.PARTIALLY_VERIFIED.value]
        unknown = [c for c in claims if c.status == ClaimStatus.UNKNOWN.value]
        verified = [c for c in claims if c.status == ClaimStatus.VERIFIED.value]

        removed_symbols = {
            change.qualified_name
            for change in semantic_changes
            if change.change_type == SemanticChangeType.SYMBOL_REMOVED
        }
        signature_changes = {
            change.qualified_name
            for change in semantic_changes
            if change.change_type == SemanticChangeType.SIGNATURE_CHANGED
        }

        if refuted:
            claim = refuted[0]
            grounded_in_removed = any(
                (link.qualified_name in removed_symbols)
                for c in refuted
                for link in (c.evidence_links or [])
            )
            if grounded_in_removed:
                return (
                    DecisionCode.INVALIDATE.value,
                    ReasonCode.SYMBOL_REMOVED.value,
                    (
                        f"The symbol this memory depends on was removed, so its claim "
                        f"\"{claim.text[:120]}\" no longer has any referent."
                    ),
                    claim,
                )
            return (
                DecisionCode.INVALIDATE.value,
                ReasonCode.EVIDENCE_MISSING.value,
                (
                    f"Claim \"{claim.text[:120]}\" was refuted: "
                    f"{(claim.confidence_components or {}).get('explanation', 'grounding evidence no longer holds')}"
                ),
                claim,
            )

        if conflicted:
            claim = conflicted[0]
            return (
                DecisionCode.CONFLICT.value,
                ReasonCode.TEST_CONTRADICTION.value,
                (
                    f"Verification policies disagree about claim "
                    f"\"{claim.text[:120]}\"; the memory is held as conflicted rather "
                    "than resolved by preference."
                ),
                claim,
            )

        if partial:
            claim = partial[0]
            signature_affected = any(
                link.qualified_name in signature_changes
                for c in partial
                for link in (c.evidence_links or [])
            )
            reason_code = (
                ReasonCode.SIGNATURE_CHANGED.value
                if signature_affected
                else ReasonCode.BODY_CHANGED.value
            )
            what = "signature" if signature_affected else "implementation"
            return (
                DecisionCode.REVISE.value,
                reason_code,
                (
                    f"The {what} behind claim \"{claim.text[:120]}\" changed. The code "
                    "still exists, but its continued existence does not prove the "
                    "behaviour this memory describes, so the memory needs revision "
                    "rather than deletion."
                ),
                claim,
            )

        if reanchored and verified:
            return (
                DecisionCode.REANCHOR.value,
                ReasonCode.SYMBOL_RENAMED.value,
                (
                    "Grounding symbol was renamed or moved; evidence was re-anchored "
                    f"({'; '.join(reanchored[:3])}) and the claim re-verified against "
                    "its new location. What the memory asserts is unchanged."
                ),
                verified[0],
            )

        if unknown and not verified:
            claim = unknown[0]
            return (
                DecisionCode.UNKNOWN.value,
                ReasonCode.NO_EVIDENCE.value,
                (
                    f"Claim \"{claim.text[:120]}\" could not be decided from the "
                    "repository. It is reported as unknown rather than assumed to "
                    "have survived the change."
                ),
                claim,
            )

        if verified:
            return (
                DecisionCode.KEEP.value,
                ReasonCode.EVIDENCE_INTACT.value,
                (
                    f"All {len(verified)} claim(s) re-verified against the changed "
                    "repository; this memory was not affected by the change."
                ),
                verified[0],
            )

        return (
            DecisionCode.UNKNOWN.value,
            ReasonCode.NOT_AFFECTED.value,
            "No claim reached a decisive outcome for this change.",
            claims[0] if claims else None,
        )

    @staticmethod
    def _recompute_memory_confidence(memory: Memory, claims: list[Claim]) -> None:
        """Derive memory confidence from its claims and evidence.

        A memory is exactly as trustworthy as its least trustworthy claim, so the
        minimum -- not the mean -- governs. Averaging would let one well-evidenced
        sentence launder a refuted one.
        """
        summary = EvidenceSummary.from_items(memory.evidences)
        worst_outcome = None
        if claims:
            severity = {
                VerificationOutcome.FAILED.value: 5,
                VerificationOutcome.CONFLICTED.value: 4,
                VerificationOutcome.UNKNOWN.value: 3,
                VerificationOutcome.PARTIALLY_VERIFIED.value: 2,
                VerificationOutcome.VERIFIED.value: 1,
                VerificationOutcome.NOT_APPLICABLE.value: 0,
            }
            worst_outcome = max(
                (c.last_outcome for c in claims if c.last_outcome),
                key=lambda o: severity.get(o, 0),
                default=None,
            )

        scored = ConfidenceScorer.score(
            authority=memory.authority or memory.source_type,
            evidence=summary,
            last_outcome=worst_outcome,
            last_verified_at=memory.last_verified_at,
            status=memory.status,
            conflict_group=memory.conflict_group,
        )
        memory.confidence = scored.score
        memory.confidence_components = {
            **scored.components,
            "explanation": scored.explanation,
            "worst_claim_outcome": worst_outcome,
            "claims_considered": len(claims),
        }


async def claim_evidence_for_files(
    session: AsyncSession, project_id: str, file_paths: list[str]
) -> list[ClaimEvidence]:
    """All claim evidence anchored in the given files, for impact reporting."""
    if not file_paths:
        return []
    normalized = [p.replace("\\", "/") for p in file_paths]
    res = await session.execute(
        select(ClaimEvidence)
        .join(Claim, Claim.id == ClaimEvidence.claim_id)
        .where(Claim.project_id == project_id, ClaimEvidence.file_path.in_(normalized))
    )
    return list(res.scalars().all())

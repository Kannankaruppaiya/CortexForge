"""Claim verification engine (specification sections 4, 7, 8 and 30).

Verification answers one question per claim: *given the repository as it is now,
does the evidence still support this proposition?* The answer is one of

    VERIFIED | PARTIALLY_VERIFIED | FAILED | CONFLICTED | UNKNOWN | NOT_APPLICABLE

and it is always recorded -- claim, policy, evidence consulted, commit, branch,
workspace, verifier, reason and reason code -- so that a belief can be traced back
to the check that produced it.

Three disciplines separate this from the naive "does the file still exist" check it
replaces:

* **UNKNOWN is a real answer.** A claim with no usable evidence is not true and not
  false. Saying so is more useful to an agent than a confident guess.
* **Verification never inflates confidence.** Confidence is recomputed by
  :class:`~cortexforge.memory.confidence.ConfidenceScorer` from the evidence and
  the *latest* outcome. Verifying unchanged evidence repeatedly changes nothing.
* **Runs are idempotent.** A run is keyed on the state it examined, so re-verifying
  an unchanged project reuses the previous run instead of manufacturing a fresh
  timestamp that would make stale knowledge look recently confirmed.
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider
from cortexforge.cognition.epistemics import (
    POSITIVE_OUTCOMES,
    ClaimStatus,
    EvidenceRelation,
    ReasonCode,
    VerificationOutcome,
)
from cortexforge.core.models import (
    Claim,
    ClaimEvidence,
    Memory,
    Project,
    TestCaseResult,
    TestRun,
    VerificationPolicy,
    VerificationResult,
    VerificationRun,
)
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.security.path_safety import PathSecurity, PathSecurityError
from cortexforge.verification.policies import (
    ensure_default_policies,
    resolve_policies_for_claim,
)

logger = logging.getLogger(__name__)


@dataclass
class ClaimVerdict:
    """The outcome of checking one claim under one policy."""

    claim_id: str
    policy_name: str
    policy_version: int
    outcome: str
    reason: str
    reason_code: str | None = None
    evidence_checked: list[dict[str, Any]] = field(default_factory=list)
    policy_id: str | None = None


# Outcome precedence when several policies evaluate one claim. A refutation from
# any policy outranks a confirmation from another: it takes one counterexample to
# break a claim and unanimity to establish it.
_OUTCOME_PRECEDENCE: dict[str, int] = {
    VerificationOutcome.CONFLICTED.value: 60,
    VerificationOutcome.FAILED.value: 50,
    VerificationOutcome.UNKNOWN.value: 40,
    VerificationOutcome.PARTIALLY_VERIFIED.value: 30,
    VerificationOutcome.VERIFIED.value: 20,
    VerificationOutcome.NOT_APPLICABLE.value: 10,
}

_OUTCOME_TO_CLAIM_STATUS: dict[str, str] = {
    VerificationOutcome.VERIFIED.value: ClaimStatus.VERIFIED.value,
    VerificationOutcome.PARTIALLY_VERIFIED.value: ClaimStatus.PARTIALLY_VERIFIED.value,
    VerificationOutcome.FAILED.value: ClaimStatus.REFUTED.value,
    VerificationOutcome.CONFLICTED.value: ClaimStatus.CONFLICTED.value,
    VerificationOutcome.UNKNOWN.value: ClaimStatus.UNKNOWN.value,
    VerificationOutcome.NOT_APPLICABLE.value: ClaimStatus.UNVERIFIED.value,
}


class ClaimVerificationEngine:
    """Evaluates claims against the repository under versioned policies."""

    def __init__(self, parser_provider: TreeSitterProvider | None = None) -> None:
        self.parser = parser_provider or TreeSitterProvider()

    # ------------------------------------------------------------------ runs

    @staticmethod
    def compute_run_key(
        project_id: str,
        commit_sha: str | None,
        branch: str | None,
        workspace: str | None,
        claim_ids: list[str],
        policy_names: list[str],
        evidence_state_digest: str = "",
    ) -> str:
        """Identity of a verification run: the exact state it would examine.

        The key covers the claims, the policies, the commit *and* a digest of the
        current on-disk content of every grounding file. Including the file content
        matters: an uncommitted edit changes what verification would find while
        leaving the commit sha untouched, so a commit-only key would happily reuse
        an answer that the working tree has already invalidated.

        With the digest included, an unchanged tree reuses its run -- which is what
        stops a scheduler from re-stamping every claim as freshly verified on every
        tick (sections 8 and 37) -- while any real change forces a fresh evaluation.
        """
        material = "|".join(
            [
                project_id,
                commit_sha or "",
                branch or "",
                workspace or "",
                ",".join(sorted(claim_ids)),
                ",".join(sorted(policy_names)),
                evidence_state_digest,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def compute_evidence_state_digest(
        self, project_root: str, claims: list[Claim]
    ) -> str:
        """Digest the current content of every file the given claims are grounded in.

        Files are hashed once each regardless of how many claims reference them, and
        a missing file contributes a distinct marker, so deletion changes the digest
        just as an edit does.
        """
        paths = sorted(
            {
                link.file_path
                for claim in claims
                for link in (claim.evidence_links or [])
                if link.file_path
            }
        )
        hasher = hashlib.sha256()
        for rel_path in paths:
            abs_path = self._resolve(project_root, rel_path)
            hasher.update(rel_path.encode("utf-8"))
            if abs_path is None or not os.path.exists(abs_path):
                hasher.update(b"\x00missing")
                continue
            try:
                with open(abs_path, "rb") as handle:
                    hasher.update(hashlib.sha256(handle.read()).digest())
            except OSError:
                hasher.update(b"\x00unreadable")
        return hasher.hexdigest()

    async def verify_project(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str | None = None,
        branch: str | None = None,
        workspace: str | None = None,
        verifier: str = "system",
        trigger: str = "manual",
        claim_ids: list[str] | None = None,
        reuse_identical_run: bool = True,
    ) -> VerificationRun:
        """Verify a project's claims and record the run and every result.

        When ``claim_ids`` is given only those claims are evaluated, which is how
        reconciliation stays incremental instead of re-checking the whole project
        after a one-line change.
        """
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist.")

        policies = await ensure_default_policies(session, project_id)

        claim_stmt = (
            select(Claim)
            .options(selectinload(Claim.evidence_links))
            .where(
                Claim.project_id == project_id,
                Claim.status.notin_(
                    [ClaimStatus.RETIRED.value, ClaimStatus.SUPERSEDED.value]
                ),
            )
        )
        if claim_ids:
            claim_stmt = claim_stmt.where(Claim.id.in_(claim_ids))
        claims = list((await session.execute(claim_stmt)).scalars().all())

        project_root = os.path.realpath(project.local_path)
        run_key = self.compute_run_key(
            project_id,
            commit_sha,
            branch,
            workspace,
            [c.id for c in claims],
            [p.name for p in policies],
            self.compute_evidence_state_digest(project_root, claims),
        )

        if reuse_identical_run:
            existing = await session.execute(
                select(VerificationRun).where(
                    VerificationRun.project_id == project_id,
                    VerificationRun.idempotency_key == run_key,
                )
            )
            prior = existing.scalars().first()
            if prior is not None:
                # Nothing about the examined state changed, so the previous answer
                # still stands. Returning it -- rather than re-running -- is what
                # keeps repeated verification from looking like new evidence.
                logger.debug(
                    "Reusing verification run %s for unchanged state", prior.id
                )
                return prior

        run = VerificationRun(
            project_id=project_id,
            idempotency_key=run_key,
            commit_sha=commit_sha,
            branch=branch,
            workspace=workspace,
            verifier=verifier,
            trigger=trigger,
            status="RUNNING",
            claims_evaluated=len(claims),
        )
        session.add(run)
        await session.flush()

        counts: dict[str, int] = {}

        for claim in claims:
            verdicts = await self._evaluate_claim(
                session, claim, policies, project_root, commit_sha
            )
            for verdict in verdicts:
                session.add(
                    VerificationResult(
                        run_id=run.id,
                        claim_id=claim.id,
                        policy_id=verdict.policy_id,
                        policy_name=verdict.policy_name,
                        policy_version=verdict.policy_version,
                        outcome=verdict.outcome,
                        reason_code=verdict.reason_code,
                        reason=verdict.reason,
                        confidence=0.0,
                        evidence_checked=verdict.evidence_checked,
                        commit_sha=commit_sha,
                        branch=branch,
                        workspace=workspace,
                        verifier=verifier,
                    )
                )

            final = self._combine_verdicts(verdicts)
            counts[final.outcome] = counts.get(final.outcome, 0) + 1
            await self._apply_verdict_to_claim(session, claim, final, commit_sha)

        run.verified_count = counts.get(VerificationOutcome.VERIFIED.value, 0)
        run.partially_verified_count = counts.get(
            VerificationOutcome.PARTIALLY_VERIFIED.value, 0
        )
        run.failed_count = counts.get(VerificationOutcome.FAILED.value, 0)
        run.conflicted_count = counts.get(VerificationOutcome.CONFLICTED.value, 0)
        run.unknown_count = counts.get(VerificationOutcome.UNKNOWN.value, 0)
        run.not_applicable_count = counts.get(
            VerificationOutcome.NOT_APPLICABLE.value, 0
        )
        run.status = "COMPLETED"
        run.finished_at = datetime.now(UTC)

        await session.flush()
        return run

    # ------------------------------------------------------------ strategies

    async def _evaluate_claim(
        self,
        session: AsyncSession,
        claim: Claim,
        policies: list[VerificationPolicy],
        project_root: str,
        commit_sha: str | None,
    ) -> list[ClaimVerdict]:
        """Run every applicable policy against a claim."""
        applicable = resolve_policies_for_claim(claim, policies)
        verdicts: list[ClaimVerdict] = []

        for policy in applicable:
            handler = getattr(self, f"_strategy_{policy.strategy}", None)
            if handler is None:
                verdicts.append(
                    ClaimVerdict(
                        claim_id=claim.id,
                        policy_name=policy.name,
                        policy_version=policy.version,
                        policy_id=policy.id,
                        outcome=VerificationOutcome.UNKNOWN.value,
                        reason_code=ReasonCode.BEHAVIOUR_UNVERIFIED.value,
                        reason=(
                            f"No verifier is registered for strategy '{policy.strategy}', "
                            "so this claim cannot be decided."
                        ),
                    )
                )
                continue
            verdicts.append(
                await handler(session, claim, policy, project_root, commit_sha)
            )

        if not verdicts:
            verdicts.append(
                ClaimVerdict(
                    claim_id=claim.id,
                    policy_name="none",
                    policy_version=0,
                    outcome=VerificationOutcome.UNKNOWN.value,
                    reason_code=ReasonCode.NO_EVIDENCE.value,
                    reason="No verification policy applies to this claim.",
                )
            )
        return verdicts

    def _supporting_links(
        self, claim: Claim, evidence_type: str | None = None
    ) -> list[ClaimEvidence]:
        links = [
            link
            for link in (claim.evidence_links or [])
            if (link.relation or EvidenceRelation.SUPPORTS.value).upper()
            == EvidenceRelation.SUPPORTS.value
        ]
        if evidence_type:
            links = [
                link
                for link in links
                if (link.evidence_type or "").upper() == evidence_type.upper()
            ]
        return links

    async def _strategy_ungrounded(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """A claim without usable evidence is UNKNOWN -- never assumed either way."""
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.UNKNOWN.value,
            reason_code=ReasonCode.NO_EVIDENCE.value,
            reason=(
                "No code, test or configuration evidence is attached to this claim, "
                "so its truth cannot be established or refuted from the repository."
            ),
        )

    async def _strategy_declared(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """Decisions are true by authority; code agreement is a separate question."""
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.NOT_APPLICABLE.value,
            reason_code=None,
            reason=(
                f"This is a declared {claim.epistemic_state} at authority "
                f"{claim.authority}; it holds because it was decided. Divergence "
                "between the decision and the code is reported as an architecture "
                "violation, not as a false claim."
            ),
        )

    async def _strategy_symbol_ast_identity(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """Confirm the grounding symbol still exists with an unchanged fingerprint."""
        links = self._supporting_links(claim, "SYMBOL")
        checked: list[dict[str, Any]] = []
        missing: list[str] = []
        changed: list[str] = []
        intact: list[str] = []

        for link in links:
            record = {
                "evidence_id": link.id,
                "file": link.file_path,
                "symbol": link.qualified_name,
                "expected_fingerprint": link.ast_fingerprint,
            }
            abs_path = self._resolve(project_root, link.file_path)
            if abs_path is None or not os.path.exists(abs_path):
                link.state = "MISSING"
                record["result"] = "file_missing"
                checked.append(record)
                missing.append(link.file_path or "?")
                continue

            symbol = self._find_symbol(
                abs_path, link.file_path or "", link.qualified_name
            )
            if symbol is None:
                link.state = "MISSING"
                record["result"] = "symbol_absent"
                checked.append(record)
                missing.append(link.qualified_name or link.file_path or "?")
                continue

            record["actual_fingerprint"] = symbol.content_hash
            if link.ast_fingerprint and symbol.content_hash != link.ast_fingerprint:
                link.state = "MODIFIED"
                record["result"] = "fingerprint_changed"
                changed.append(link.qualified_name or "?")
            else:
                link.state = "INTACT"
                record["result"] = "intact"
                intact.append(link.qualified_name or "?")
                # Re-anchor line numbers: the symbol is the same, it simply moved
                # within the file. This is maintenance of grounding, not a change
                # of belief, so it happens without a lifecycle transition.
                link.line_start = symbol.start_line
                link.line_end = symbol.end_line
            link.checked_at = datetime.now(UTC)
            checked.append(record)

        if missing:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.FAILED.value,
                reason_code=ReasonCode.SYMBOL_REMOVED.value,
                reason=f"Grounding symbol(s) no longer present: {', '.join(missing[:5])}.",
                evidence_checked=checked,
            )
        if changed:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.PARTIALLY_VERIFIED.value,
                reason_code=ReasonCode.BODY_CHANGED.value,
                reason=(
                    f"Symbol(s) {', '.join(changed[:5])} still exist but their "
                    "implementation changed. Their existence is confirmed; the "
                    "behaviour this claim describes is no longer proven by it."
                ),
                evidence_checked=checked,
            )
        if intact:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.VERIFIED.value,
                reason_code=ReasonCode.EVIDENCE_INTACT.value,
                reason=f"All {len(intact)} grounding symbol(s) present with unchanged AST fingerprint.",
                evidence_checked=checked,
            )
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.UNKNOWN.value,
            reason_code=ReasonCode.NO_EVIDENCE.value,
            reason="No symbol evidence was available to check.",
            evidence_checked=checked,
        )

    async def _strategy_code_snippet_integrity(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """Compare the grounding line range against its recorded content hash."""
        links = self._supporting_links(claim, "CODE")
        checked: list[dict[str, Any]] = []
        missing = 0
        modified = 0
        intact = 0
        unknown = 0

        for link in links:
            record: dict[str, Any] = {
                "evidence_id": link.id,
                "file": link.file_path,
                "lines": [link.line_start, link.line_end],
            }
            abs_path = self._resolve(project_root, link.file_path)
            if abs_path is None or not os.path.exists(abs_path):
                link.state = "MISSING"
                record["result"] = "file_missing"
                missing += 1
                checked.append(record)
                continue

            if not link.content_hash:
                link.state = "UNCHECKED"
                record["result"] = "no_recorded_hash"
                unknown += 1
                checked.append(record)
                continue

            actual = self._hash_range(abs_path, link.line_start, link.line_end)
            record["actual_hash"] = actual
            record["expected_hash"] = link.content_hash

            if not actual:
                # The anchor points at lines that do not exist or contain nothing.
                # This is a missing referent, not a match.
                link.state = "MISSING"
                record["result"] = "range_does_not_resolve"
                missing += 1
                link.checked_at = datetime.now(UTC)
                checked.append(record)
                continue

            if actual == link.content_hash:
                link.state = "INTACT"
                record["result"] = "intact"
                intact += 1
            else:
                link.state = "MODIFIED"
                record["result"] = "content_changed"
                modified += 1
            link.checked_at = datetime.now(UTC)
            checked.append(record)

        if missing:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.FAILED.value,
                reason_code=ReasonCode.EVIDENCE_MISSING.value,
                reason=f"{missing} grounding file(s) no longer exist.",
                evidence_checked=checked,
            )
        if modified:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.PARTIALLY_VERIFIED.value,
                reason_code=ReasonCode.BODY_CHANGED.value,
                reason=(
                    f"{modified} grounding snippet(s) changed since the claim was "
                    "recorded; the location still exists but no longer matches."
                ),
                evidence_checked=checked,
            )
        if intact:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.VERIFIED.value,
                reason_code=ReasonCode.EVIDENCE_INTACT.value,
                reason=f"{intact} grounding snippet(s) unchanged (sha256 match).",
                evidence_checked=checked,
            )
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.UNKNOWN.value,
            reason_code=ReasonCode.NO_EVIDENCE.value,
            reason=(
                f"{unknown} code evidence item(s) carry no recorded hash, so nothing "
                "could be compared."
            ),
            evidence_checked=checked,
        )

    async def _strategy_file_presence(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """Confirm a configuration or contract file still exists and mentions its key."""
        links = self._supporting_links(claim, "CONFIG")
        checked: list[dict[str, Any]] = []
        missing: list[str] = []
        key_absent: list[str] = []
        present = 0

        for link in links:
            record: dict[str, Any] = {"evidence_id": link.id, "file": link.file_path}
            abs_path = self._resolve(project_root, link.file_path)
            if abs_path is None or not os.path.exists(abs_path):
                link.state = "MISSING"
                record["result"] = "file_missing"
                missing.append(link.file_path or "?")
                checked.append(record)
                continue

            key = (link.detail or {}).get("key")
            if key:
                try:
                    with open(abs_path, encoding="utf-8", errors="ignore") as handle:
                        content = handle.read()
                except OSError:
                    content = ""
                if key not in content:
                    link.state = "MODIFIED"
                    record["result"] = "key_absent"
                    key_absent.append(f"{key} in {link.file_path}")
                    checked.append(record)
                    continue

            link.state = "INTACT"
            link.checked_at = datetime.now(UTC)
            record["result"] = "present"
            present += 1
            checked.append(record)

        if missing or key_absent:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.FAILED.value,
                reason_code=ReasonCode.CONFIG_CHANGED.value,
                reason=(
                    "Configuration grounding no longer holds: "
                    + "; ".join(
                        [*(f"missing {m}" for m in missing[:3]), *key_absent[:3]]
                    )
                ),
                evidence_checked=checked,
            )
        if present:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.VERIFIED.value,
                reason_code=ReasonCode.EVIDENCE_INTACT.value,
                reason=f"{present} configuration grounding(s) still present.",
                evidence_checked=checked,
            )
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.UNKNOWN.value,
            reason_code=ReasonCode.NO_EVIDENCE.value,
            reason="No configuration evidence was available to check.",
            evidence_checked=checked,
        )

    async def _strategy_test_outcome(
        self,
        session: AsyncSession,
        claim: Claim,
        policy: VerificationPolicy,
        project_root: str,
        commit_sha: str | None,
    ) -> ClaimVerdict:
        """Decide a claim from recorded test results, discounting flaky tests.

        A test that fails often for reasons unrelated to the current change is weak
        evidence that the claim is wrong (section 15). Rather than treating every
        red test as a refutation, a test whose historical failure rate exceeds the
        policy's threshold produces UNKNOWN and says why.
        """
        links = self._supporting_links(claim, "TEST")
        test_names = [
            link.detail.get("test_name")
            for link in links
            if isinstance(link.detail, dict) and link.detail.get("test_name")
        ]
        if not test_names:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.UNKNOWN.value,
                reason_code=ReasonCode.NO_EVIDENCE.value,
                reason="Test evidence is attached but names no specific test case.",
            )

        flaky_ratio = float((policy.parameters or {}).get("flaky_failure_ratio", 0.34))
        checked: list[dict[str, Any]] = []
        failing: list[str] = []
        passing: list[str] = []
        inconclusive: list[str] = []

        for name in test_names:
            history = await session.execute(
                select(TestCaseResult)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(
                    TestRun.project_id == claim.project_id,
                    TestCaseResult.test_name == name,
                )
                .order_by(TestCaseResult.created_at.desc())
                .limit(20)
            )
            results = list(history.scalars().all())
            if not results:
                inconclusive.append(f"{name} (never run)")
                checked.append({"test": name, "result": "no_history"})
                continue

            latest = results[0]
            failures = sum(1 for r in results if r.status == "FAILED")
            ratio = failures / len(results)
            record = {
                "test": name,
                "latest": latest.status,
                "runs": len(results),
                "failure_ratio": round(ratio, 3),
            }

            if latest.status == "FAILED":
                if len(results) >= 3 and ratio >= flaky_ratio:
                    record["result"] = "discounted_as_flaky"
                    inconclusive.append(
                        f"{name} (fails {ratio:.0%} of runs; treated as flaky)"
                    )
                else:
                    record["result"] = "refutes"
                    failing.append(name)
            elif latest.status == "PASSED":
                record["result"] = "supports"
                passing.append(name)
            else:
                record["result"] = "inconclusive"
                inconclusive.append(f"{name} ({latest.status})")
            checked.append(record)

        if failing:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.FAILED.value,
                reason_code=ReasonCode.TEST_CONTRADICTION.value,
                reason=f"Test(s) contradicting this claim are failing: {', '.join(failing[:5])}.",
                evidence_checked=checked,
            )
        if passing and not inconclusive:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.VERIFIED.value,
                reason_code=ReasonCode.TEST_SUPPORT.value,
                reason=f"Supported by passing test(s): {', '.join(passing[:5])}.",
                evidence_checked=checked,
            )
        if passing:
            return ClaimVerdict(
                claim_id=claim.id,
                policy_name=policy.name,
                policy_version=policy.version,
                policy_id=policy.id,
                outcome=VerificationOutcome.PARTIALLY_VERIFIED.value,
                reason_code=ReasonCode.TEST_SUPPORT.value,
                reason=(
                    f"Supported by {len(passing)} passing test(s), with "
                    f"{len(inconclusive)} inconclusive: {'; '.join(inconclusive[:3])}."
                ),
                evidence_checked=checked,
            )
        return ClaimVerdict(
            claim_id=claim.id,
            policy_name=policy.name,
            policy_version=policy.version,
            policy_id=policy.id,
            outcome=VerificationOutcome.UNKNOWN.value,
            reason_code=ReasonCode.BEHAVIOUR_UNVERIFIED.value,
            reason=f"No conclusive test signal: {'; '.join(inconclusive[:5])}.",
            evidence_checked=checked,
        )

    # -------------------------------------------------------------- combining

    def _combine_verdicts(self, verdicts: list[ClaimVerdict]) -> ClaimVerdict:
        """Reduce several policy verdicts to the claim's overall outcome.

        A claim confirmed by one policy and refuted by another is ``CONFLICTED``,
        not "mostly verified": disagreement between checks is information, and
        averaging it away is how a memory layer starts lying confidently.
        """
        if len(verdicts) == 1:
            return verdicts[0]

        outcomes = {v.outcome for v in verdicts}
        if (
            outcomes & POSITIVE_OUTCOMES
            and VerificationOutcome.FAILED.value in outcomes
        ):
            supporting = [v for v in verdicts if v.outcome in POSITIVE_OUTCOMES]
            refuting = [
                v for v in verdicts if v.outcome == VerificationOutcome.FAILED.value
            ]
            return ClaimVerdict(
                claim_id=verdicts[0].claim_id,
                policy_name="combined",
                policy_version=1,
                outcome=VerificationOutcome.CONFLICTED.value,
                reason_code=ReasonCode.TEST_CONTRADICTION.value,
                reason=(
                    f"Policies disagree: {supporting[0].policy_name} supports this claim "
                    f"({supporting[0].reason}) while {refuting[0].policy_name} refutes it "
                    f"({refuting[0].reason})."
                ),
                evidence_checked=[e for v in verdicts for e in v.evidence_checked],
            )

        return max(verdicts, key=lambda v: _OUTCOME_PRECEDENCE.get(v.outcome, 0))

    async def _apply_verdict_to_claim(
        self,
        session: AsyncSession,
        claim: Claim,
        verdict: ClaimVerdict,
        commit_sha: str | None,
    ) -> None:
        """Update a claim's status and *recompute* -- never increment -- confidence."""
        claim.status = _OUTCOME_TO_CLAIM_STATUS.get(
            verdict.outcome, ClaimStatus.UNKNOWN.value
        )
        claim.last_outcome = verdict.outcome
        claim.last_verified_at = datetime.now(UTC)
        if verdict.outcome in POSITIVE_OUTCOMES and commit_sha:
            claim.valid_from_commit = claim.valid_from_commit or commit_sha

        summary = EvidenceSummary.from_items(claim.evidence_links)
        scored = ConfidenceScorer.score(
            authority=claim.authority,
            evidence=summary,
            last_outcome=verdict.outcome,
            last_verified_at=claim.last_verified_at,
            status="ACTIVE",
        )
        claim.confidence = scored.score
        claim.confidence_components = {
            **scored.components,
            "explanation": scored.explanation,
            "outcome": verdict.outcome,
            "reason_code": verdict.reason_code,
        }
        claim.updated_at = datetime.now(UTC)

    # ---------------------------------------------------------------- helpers

    def _resolve(self, project_root: str, rel_path: str | None) -> str | None:
        """Resolve a repository-relative path, refusing anything outside the root.

        Evidence paths can originate from repository content, which is untrusted
        input (section 41), so containment is enforced here rather than trusted.
        """
        if not rel_path:
            return None
        try:
            return PathSecurity.safe_resolve(project_root, rel_path)
        except PathSecurityError:
            logger.warning("Refusing evidence path outside project root: %s", rel_path)
            return None

    def _find_symbol(
        self, abs_path: str, rel_path: str, qualified_name: str | None
    ) -> Any:
        """Locate a symbol in the current file by qualified name, then by name."""
        if not qualified_name or not self.parser.can_parse(rel_path):
            return None
        try:
            with open(abs_path, "rb") as handle:
                content = handle.read()
            parsed = self.parser.parse_source(rel_path, content)
        except (OSError, ValueError) as exc:
            logger.debug("Could not parse %s during verification: %s", rel_path, exc)
            return None

        for symbol in parsed.symbols:
            if symbol.qualified_name == qualified_name:
                return symbol
        short_name = qualified_name.rsplit(".", 1)[-1]
        for symbol in parsed.symbols:
            if symbol.name == short_name and symbol.entity_type != "file":
                return symbol
        return None

    @staticmethod
    def _hash_range(abs_path: str, line_start: int | None, line_end: int | None) -> str:
        """sha256 of a line range, or ``""`` when the range does not resolve.

        The empty-range case matters more than it looks. Slicing a 6-line file at
        lines 200-210 yields an empty string, whose sha256 is a fixed well-known
        value -- so a claim anchored to lines that do not exist would hash to that
        constant at capture time, hash to the same constant at verification time,
        and be reported as VERIFIED. A claim about code that was never there would
        come back true.

        Returning the empty string instead lets the caller distinguish "this
        anchor no longer resolves" from "this anchor still matches".
        """
        try:
            with open(abs_path, encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except OSError:
            return ""

        if line_start is None:
            snippet = "".join(lines)
        else:
            if line_start > len(lines):
                return ""
            start = max(0, line_start - 1)
            end = line_end if line_end is not None else line_start
            snippet = "".join(lines[start:end])

        if not snippet.strip():
            return ""
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()


async def latest_results_for_claims(
    session: AsyncSession, claim_ids: list[str]
) -> dict[str, VerificationResult]:
    """Most recent verification result per claim, for display and reconciliation."""
    if not claim_ids:
        return {}
    res = await session.execute(
        select(VerificationResult)
        .where(VerificationResult.claim_id.in_(claim_ids))
        .order_by(VerificationResult.created_at.desc())
    )
    latest: dict[str, VerificationResult] = {}
    for row in res.scalars().all():
        latest.setdefault(row.claim_id, row)
    return latest


async def memory_claim_health(session: AsyncSession, memory: Memory) -> dict[str, Any]:
    """Aggregate claim outcomes for a memory.

    A memory is only as sound as its worst claim: one refuted proposition makes the
    memory unsafe to present as current truth even if its other sentences still hold.
    """
    res = await session.execute(
        select(Claim).where(
            Claim.memory_id == memory.id, Claim.status != ClaimStatus.RETIRED.value
        )
    )
    claims = list(res.scalars().all())
    if not claims:
        return {"claims": 0, "worst": None, "statuses": {}}

    statuses: dict[str, int] = {}
    for claim in claims:
        statuses[claim.status] = statuses.get(claim.status, 0) + 1

    severity = {
        ClaimStatus.REFUTED.value: 5,
        ClaimStatus.CONFLICTED.value: 4,
        ClaimStatus.STALE.value: 3,
        ClaimStatus.UNKNOWN.value: 2,
        ClaimStatus.PROPOSED.value: 2,
        ClaimStatus.UNVERIFIED.value: 1,
        ClaimStatus.PARTIALLY_VERIFIED.value: 1,
        ClaimStatus.VERIFIED.value: 0,
    }
    worst = max(claims, key=lambda c: severity.get(c.status, 0))
    return {
        "claims": len(claims),
        "worst": worst.status,
        "worst_claim_id": worst.id,
        "statuses": statuses,
        "mean_confidence": round(sum(c.confidence for c in claims) / len(claims), 4),
    }

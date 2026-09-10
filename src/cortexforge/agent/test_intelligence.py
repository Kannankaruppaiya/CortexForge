"""Test causal attribution (specification section 15).

Recording that a test failed is easy. Knowing what the failure *means* is the
part that matters, and it is the part that decides whether a memory should be
disbelieved:

* a test that passed on the previous commit and fails on this one was probably
  broken by this change (``INTRODUCED_BY``);
* a test that was failing and now passes was probably fixed by it (``FIXED_BY``);
* a test that has been flipping for weeks is telling you about itself, not about
  your change (``FLAKY``);
* a test whose failure touches nothing this change went near is most likely
  ``UNRELATED``.

The specification is explicit about why this distinction earns its keep: *"a test
that fails frequently for unrelated reasons must not automatically be interpreted
as evidence that the current change is wrong."* Without attribution, every red
test is equally strong evidence against every claim, which makes a flaky suite
capable of demolishing a project's accumulated knowledge.

Attribution is computed from recorded history, never guessed. When history is
too thin to support a conclusion the answer is ``UNKNOWN``, which is a different
statement from ``UNRELATED`` and is reported as such.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.cognition.epistemics import TestAttribution
from cortexforge.core.models import (
    ChangeSet,
    SymbolChange,
    TestCaseResult,
    TestRun,
)

logger = logging.getLogger(__name__)

# A test failing at least this often across its recent history is telling you
# about its own instability rather than about the change under test.
FLAKY_FAILURE_RATIO = 0.34

# Below this many recorded runs, a failure ratio is not evidence of anything.
MIN_RUNS_FOR_FLAKINESS = 3

# How many historical results are considered when judging a test's stability.
HISTORY_WINDOW = 20


@dataclass
class TestVerdict:
    """What one test result says about the change that preceded it."""

    test_name: str
    status: str
    attribution: str
    reason: str
    failure_ratio: float | None = None
    runs_considered: int = 0
    previous_status: str | None = None
    related_symbols: list[str] = field(default_factory=list)

    @property
    def is_evidence_against_the_change(self) -> bool:
        """Whether this result should count against claims about the change.

        ``FLAKY`` and ``UNRELATED`` are excluded, and only those. Both are real
        failures -- the test did fail -- but neither is evidence that *this*
        change broke something, and treating them as such is how a noisy suite
        erodes a project's knowledge one red build at a time.

        ``UNKNOWN`` deliberately counts. A test failing for the first time with
        no history is exactly the situation where discarding the signal would be
        expensive: we cannot yet prove the change caused it, but a novel failure
        is worth recording and looking at. Not knowing is a reason to investigate,
        not a reason to ignore.
        """
        return self.attribution not in (
            TestAttribution.FLAKY.value,
            TestAttribution.UNRELATED.value,
            TestAttribution.FIXED_BY.value,
        )


@dataclass
class TestIntelligenceReport:
    """Attribution for every test in a run."""

    test_run_id: str
    commit_sha: str | None
    verdicts: list[TestVerdict] = field(default_factory=list)

    def by_attribution(self, attribution: str) -> list[TestVerdict]:
        return [v for v in self.verdicts if v.attribution == attribution]

    @property
    def blames_this_change(self) -> list[TestVerdict]:
        """Failures that are genuinely attributable to the change under test."""
        return [v for v in self.verdicts if v.is_evidence_against_the_change]

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for verdict in self.verdicts:
            counts[verdict.attribution] = counts.get(verdict.attribution, 0) + 1
        return counts


class TestIntelligenceEngine:
    """Attributes test outcomes to the changes that plausibly caused them."""

    async def attribute_run(
        self,
        session: AsyncSession,
        test_run_id: str,
        change_set_id: str | None = None,
    ) -> TestIntelligenceReport:
        """Explain every result in a test run against the project's test history."""
        run = await session.get(TestRun, test_run_id)
        if run is None:
            raise ValueError(f"Test run {test_run_id} does not exist.")

        results = list(
            (
                await session.execute(
                    select(TestCaseResult).where(TestCaseResult.test_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )

        changed_symbols = await self._changed_symbols(session, change_set_id)
        report = TestIntelligenceReport(test_run_id=run.id, commit_sha=run.commit_sha)

        for result in results:
            history = await self._history(
                session, run.project_id, result.test_name, run.id
            )
            report.verdicts.append(self._attribute(result, history, changed_symbols))

        return report

    # ------------------------------------------------------------- internals

    async def _changed_symbols(
        self, session: AsyncSession, change_set_id: str | None
    ) -> set[str]:
        """Symbols and files the change under test actually touched."""
        if not change_set_id:
            return set()

        changes = list(
            (
                await session.execute(
                    select(SymbolChange).where(
                        SymbolChange.change_set_id == change_set_id
                    )
                )
            )
            .scalars()
            .all()
        )
        touched: set[str] = set()
        for change in changes:
            touched.add(change.qualified_name)
            touched.add(change.symbol_name)
            touched.add(change.file_path)
        return touched

    async def _history(
        self,
        session: AsyncSession,
        project_id: str,
        test_name: str,
        exclude_run_id: str,
    ) -> list[TestCaseResult]:
        """This test's recent results, newest first, excluding the current run."""
        result = await session.execute(
            select(TestCaseResult)
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCaseResult.test_name == test_name,
                TestCaseResult.test_run_id != exclude_run_id,
            )
            .order_by(TestCaseResult.created_at.desc())
            .limit(HISTORY_WINDOW)
        )
        return list(result.scalars().all())

    def _attribute(
        self,
        result: TestCaseResult,
        history: list[TestCaseResult],
        changed_symbols: set[str],
    ) -> TestVerdict:
        """Decide what one result says, from history and from what changed."""
        previous = history[0].status if history else None
        failures = sum(1 for entry in history if entry.status == "FAILED")
        ratio = failures / len(history) if history else None

        base = TestVerdict(
            test_name=result.test_name,
            status=result.status,
            attribution=TestAttribution.UNKNOWN.value,
            reason="",
            failure_ratio=round(ratio, 3) if ratio is not None else None,
            runs_considered=len(history),
            previous_status=previous,
            related_symbols=sorted(self._overlap(result, changed_symbols)),
        )

        if result.status == "PASSED":
            if previous == "FAILED":
                base.attribution = TestAttribution.FIXED_BY.value
                base.reason = (
                    "This test was failing on the previous recorded run and passes now, "
                    "so the intervening change most plausibly fixed it."
                )
            else:
                base.attribution = TestAttribution.UNRELATED.value
                base.reason = (
                    "The test passed and was already passing; nothing to attribute."
                )
            return base

        if result.status != "FAILED":
            base.attribution = TestAttribution.UNKNOWN.value
            base.reason = (
                f"The test reported {result.status}, which is neither a pass nor a "
                "failure, so no causal conclusion follows."
            )
            return base

        # --- the test failed -------------------------------------------------

        if not history:
            base.attribution = TestAttribution.UNKNOWN.value
            base.reason = (
                "This test has no recorded history, so there is no basis for deciding "
                "whether this change broke it or it was already broken. Reported as "
                "unknown rather than blamed on the change."
            )
            return base

        if (
            len(history) >= MIN_RUNS_FOR_FLAKINESS
            and ratio is not None
            and ratio >= FLAKY_FAILURE_RATIO
            and any(entry.status == "PASSED" for entry in history)
        ):
            # It fails often *and* passes sometimes: the test is unstable. A test
            # that fails every time is not flaky, it is broken.
            base.attribution = TestAttribution.FLAKY.value
            base.reason = (
                f"This test has failed in {failures} of its last {len(history)} runs "
                f"({ratio:.0%}) while also passing in that window. Its failure is weak "
                "evidence about this change specifically."
            )
            return base

        if previous == "FAILED":
            base.attribution = TestAttribution.UNRELATED.value
            base.reason = (
                "This test was already failing before the change, so the change did "
                "not break it."
            )
            return base

        # It was passing, and now it fails. Whether the change is to blame depends
        # on whether the change went anywhere near it.
        if base.related_symbols:
            base.attribution = TestAttribution.INTRODUCED_BY.value
            base.reason = (
                "This test passed on the previous run, fails now, and touches code "
                f"the change modified ({', '.join(base.related_symbols[:3])})."
            )
            return base

        if changed_symbols:
            base.attribution = TestAttribution.REGRESSED_BY.value
            base.reason = (
                "This test passed on the previous run and fails now. It does not "
                "reference anything the change touched directly, so the cause is "
                "likely indirect -- an interaction rather than an edit."
            )
            return base

        base.attribution = TestAttribution.UNKNOWN.value
        base.reason = (
            "This test passed on the previous run and fails now, but no changeset or "
            "causal evidence was supplied. In the absence of evidence, causality cannot be inferred."
        )
        return base

    @staticmethod
    def _overlap(result: TestCaseResult, changed_symbols: set[str]) -> set[str]:
        """Which changed symbols or files this test result references."""
        if not changed_symbols:
            return set()

        referenced: set[str] = set()
        for value in list(result.affected_files or []) + list(
            result.affected_symbols or []
        ):
            referenced.add(str(value))

        # The stack trace names the code the failure travelled through, which is
        # the strongest available signal for relatedness.
        trace = f"{result.stack_trace or ''}\n{result.error_message or ''}"
        matched = {symbol for symbol in changed_symbols if symbol and symbol in trace}
        matched |= referenced & changed_symbols
        return matched


async def latest_change_set(
    session: AsyncSession, project_id: str, commit_sha: str | None = None
) -> ChangeSet | None:
    """The most recent change set for a project, optionally at a commit.

    Used to give attribution something to compare a failure against when the
    caller does not supply one explicitly.
    """
    stmt = (
        select(ChangeSet)
        .where(ChangeSet.project_id == project_id)
        .order_by(ChangeSet.created_at.desc())
        .limit(1)
    )
    if commit_sha:
        stmt = stmt.where(ChangeSet.target_commit_sha == commit_sha)
    return (await session.execute(stmt)).scalars().first()


def attribution_summary(report: TestIntelligenceReport) -> dict[str, Any]:
    """A compact, honest summary for API and MCP display."""
    return {
        "test_run_id": report.test_run_id,
        "commit_sha": report.commit_sha,
        "counts": report.summary,
        "blames_this_change": [
            {
                "test": verdict.test_name,
                "attribution": verdict.attribution,
                "reason": verdict.reason,
            }
            for verdict in report.blames_this_change
        ],
        "discounted": [
            {
                "test": verdict.test_name,
                "attribution": verdict.attribution,
                "reason": verdict.reason,
                "failure_ratio": verdict.failure_ratio,
            }
            for verdict in report.verdicts
            if verdict.status == "FAILED" and not verdict.is_evidence_against_the_change
        ],
    }

"""Test causal attribution (specification section 15).

The property under test is the one the specification calls out directly: a test
that fails frequently for unrelated reasons must not automatically be read as
evidence that the current change is wrong. Without that distinction a flaky suite
can demolish a project's accumulated knowledge one red build at a time.

Each test here constructs a specific history and asserts the attribution it
should produce, so a regression says which causal judgement broke.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.test_intelligence import TestIntelligenceEngine
from cortexforge.cognition.epistemics import TestAttribution
from cortexforge.core.models import (
    ChangeSet,
    Project,
    SymbolChange,
    TestCaseResult,
    TestRun,
)


@pytest_asyncio.fixture
async def project(test_session: AsyncSession, tmp_path):
    project = Project(name="TestIntelRepo", local_path=str(tmp_path), status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)
    return project


async def _record_run(
    session: AsyncSession,
    project: Project,
    outcomes: dict[str, str],
    minutes_ago: int = 0,
    commit_sha: str = "commit",
    stack_trace: str | None = None,
) -> TestRun:
    """Record one test run with a controlled timestamp, so history has an order."""
    created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    run = TestRun(
        project_id=project.id,
        commit_sha=commit_sha,
        status="FAILED" if "FAILED" in outcomes.values() else "PASSED",
        total_tests=len(outcomes),
        passed_count=sum(1 for status in outcomes.values() if status == "PASSED"),
        failed_count=sum(1 for status in outcomes.values() if status == "FAILED"),
        created_at=created,
    )
    session.add(run)
    await session.flush()

    for name, status in outcomes.items():
        session.add(
            TestCaseResult(
                test_run_id=run.id,
                test_name=name,
                status=status,
                error_message="AssertionError: boom" if status == "FAILED" else None,
                stack_trace=stack_trace if status == "FAILED" else None,
                created_at=created,
            )
        )
    await session.commit()
    return run


async def _change_set(
    session: AsyncSession, project: Project, qualified_name: str, file_path: str
) -> ChangeSet:
    """A change set touching one named symbol."""
    change_set = ChangeSet(
        project_id=project.id,
        base_commit_sha="base",
        target_commit_sha="target",
        idempotency_key=f"key-{qualified_name}",
    )
    session.add(change_set)
    await session.flush()
    session.add(
        SymbolChange(
            change_set_id=change_set.id,
            file_path=file_path,
            symbol_name=qualified_name.rsplit(".", 1)[-1],
            qualified_name=qualified_name,
            entity_type="function",
            change_type="BODY_CHANGED",
        )
    )
    await session.commit()
    return change_set


@pytest.mark.asyncio
async def test_a_newly_failing_test_touching_changed_code_blames_the_change(
    test_session: AsyncSession, project
):
    """Passing before, failing now, and it exercises what changed: INTRODUCED_BY."""
    await _record_run(test_session, project, {"test_checkout": "PASSED"}, minutes_ago=10)
    change_set = await _change_set(
        test_session, project, "services.checkout.calculate_total", "services/checkout.py"
    )
    run = await _record_run(
        test_session,
        project,
        {"test_checkout": "FAILED"},
        stack_trace='File "services/checkout.py", line 12, in calculate_total',
    )

    report = await TestIntelligenceEngine().attribute_run(
        test_session, run.id, change_set_id=change_set.id
    )
    verdict = report.verdicts[0]

    assert verdict.attribution == TestAttribution.INTRODUCED_BY.value
    assert verdict.is_evidence_against_the_change
    assert verdict.related_symbols


@pytest.mark.asyncio
async def test_a_test_that_was_already_failing_does_not_blame_the_change(
    test_session: AsyncSession, project
):
    """Failing before and failing now: the change did not break it."""
    await _record_run(test_session, project, {"test_legacy": "FAILED"}, minutes_ago=10)
    run = await _record_run(test_session, project, {"test_legacy": "FAILED"})

    report = await TestIntelligenceEngine().attribute_run(test_session, run.id)
    verdict = report.verdicts[0]

    assert verdict.attribution == TestAttribution.UNRELATED.value
    assert not verdict.is_evidence_against_the_change
    assert "already failing" in verdict.reason


@pytest.mark.asyncio
async def test_a_flaky_test_is_discounted_rather_than_believed(
    test_session: AsyncSession, project
):
    """A test that flips constantly is evidence about itself, not about the change.

    This is the property section 15 exists for. Without it, one unstable test
    could invalidate memory on every run it happened to fail.
    """
    for index, status in enumerate(
        ["FAILED", "PASSED", "FAILED", "PASSED", "FAILED", "PASSED"]
    ):
        await _record_run(
            test_session, project, {"test_flaky_network": status}, minutes_ago=60 - index * 5
        )

    run = await _record_run(test_session, project, {"test_flaky_network": "FAILED"})
    report = await TestIntelligenceEngine().attribute_run(test_session, run.id)
    verdict = report.verdicts[0]

    assert verdict.attribution == TestAttribution.FLAKY.value
    assert not verdict.is_evidence_against_the_change, (
        "a flaky failure must not count as evidence against the change"
    )
    assert verdict.failure_ratio is not None and verdict.failure_ratio >= 0.34
    assert report.blames_this_change == []


@pytest.mark.asyncio
async def test_a_consistently_failing_test_is_not_called_flaky(
    test_session: AsyncSession, project
):
    """A test that fails every single time is broken, not unstable.

    The distinction matters: flakiness is a reason to discount evidence, and
    applying it to a genuinely broken test would suppress a real signal.
    """
    for index in range(5):
        await _record_run(
            test_session, project, {"test_always_broken": "FAILED"}, minutes_ago=60 - index * 5
        )

    run = await _record_run(test_session, project, {"test_always_broken": "FAILED"})
    report = await TestIntelligenceEngine().attribute_run(test_session, run.id)

    assert report.verdicts[0].attribution != TestAttribution.FLAKY.value
    assert report.verdicts[0].attribution == TestAttribution.UNRELATED.value


@pytest.mark.asyncio
async def test_a_newly_passing_test_credits_the_change(
    test_session: AsyncSession, project
):
    """Failing before, passing now: FIXED_BY."""
    await _record_run(test_session, project, {"test_regression": "FAILED"}, minutes_ago=10)
    run = await _record_run(test_session, project, {"test_regression": "PASSED"})

    report = await TestIntelligenceEngine().attribute_run(test_session, run.id)
    assert report.verdicts[0].attribution == TestAttribution.FIXED_BY.value


@pytest.mark.asyncio
async def test_a_failure_with_no_history_is_unknown_not_blamed(
    test_session: AsyncSession, project
):
    """With no history there is no basis for a causal claim.

    UNKNOWN and UNRELATED are different statements: one says we cannot tell, the
    other says we checked and it was not this change. Collapsing them would make
    the system sound more certain than it is.
    """
    run = await _record_run(test_session, project, {"test_brand_new": "FAILED"})
    report = await TestIntelligenceEngine().attribute_run(test_session, run.id)
    verdict = report.verdicts[0]

    assert verdict.attribution == TestAttribution.UNKNOWN.value
    assert "no recorded history" in verdict.reason
    # A novel failure still counts. Not being able to prove the change caused it
    # is a reason to investigate, not a reason to discard the signal -- only
    # FLAKY and UNRELATED are discounted.
    assert verdict.is_evidence_against_the_change


@pytest.mark.asyncio
async def test_an_indirect_failure_is_reported_as_a_regression(
    test_session: AsyncSession, project
):
    """Newly failing, but nowhere near what changed: REGRESSED_BY, not INTRODUCED_BY.

    Both count against the change; the difference is what a person reading the
    report is told about where to look.
    """
    await _record_run(test_session, project, {"test_reporting": "PASSED"}, minutes_ago=10)
    change_set = await _change_set(
        test_session, project, "services.auth.verify", "services/auth.py"
    )
    run = await _record_run(
        test_session,
        project,
        {"test_reporting": "FAILED"},
        stack_trace='File "services/reporting.py", line 40, in build_report',
    )

    report = await TestIntelligenceEngine().attribute_run(
        test_session, run.id, change_set_id=change_set.id
    )
    verdict = report.verdicts[0]

    assert verdict.attribution == TestAttribution.REGRESSED_BY.value
    assert verdict.is_evidence_against_the_change
    assert "indirect" in verdict.reason


@pytest.mark.asyncio
async def test_a_run_separates_real_signal_from_noise(
    test_session: AsyncSession, project
):
    """One run, several failures, only some of which are about this change."""
    for index, status in enumerate(["FAILED", "PASSED", "FAILED", "PASSED"]):
        await _record_run(
            test_session, project, {"test_flaky": status}, minutes_ago=60 - index * 5
        )
    await _record_run(
        test_session,
        project,
        {"test_stable": "PASSED", "test_old_break": "FAILED"},
        minutes_ago=5,
    )

    change_set = await _change_set(
        test_session, project, "services.billing.charge", "services/billing.py"
    )
    run = await _record_run(
        test_session,
        project,
        {"test_flaky": "FAILED", "test_stable": "FAILED", "test_old_break": "FAILED"},
        stack_trace='File "services/billing.py", line 8, in charge',
    )

    report = await TestIntelligenceEngine().attribute_run(
        test_session, run.id, change_set_id=change_set.id
    )
    attributions = {verdict.test_name: verdict.attribution for verdict in report.verdicts}

    assert attributions["test_flaky"] == TestAttribution.FLAKY.value
    assert attributions["test_stable"] == TestAttribution.INTRODUCED_BY.value
    assert attributions["test_old_break"] == TestAttribution.UNRELATED.value

    # Three tests failed; exactly one is evidence against this change.
    blamed = {verdict.test_name for verdict in report.blames_this_change}
    assert blamed == {"test_stable"}

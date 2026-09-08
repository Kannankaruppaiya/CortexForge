"""Integration tests for CortexForge Evaluation and Benchmark Suite."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Project
from cortexforge.evaluation.runner import EvaluationRunner


@pytest.mark.asyncio
async def test_evaluation_runner_suite(sample_repo, test_session: AsyncSession):
    """Verify that the 4-way evaluation harness generates empirical scorecards."""
    project = Project(
        name="BenchmarkRepo",
        local_path=sample_repo,
        status="ACTIVE",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    runner = EvaluationRunner()
    scorecards = await runner.run_benchmark(test_session, project.id)

    assert len(scorecards) >= 2

    for card in scorecards:
        assert card.task_id.startswith("BENCH-")
        # Verify all 7 modes are computed
        for mode_key in [
            "A_NoMemory",
            "B_NaiveVectorRAG",
            "C_FlatConversational",
            "D_CortexRetrievalOnly",
            "E_CortexWithProvenance",
            "F_CortexWithChangePropagation",
            "G_FullCortexForge",
        ]:
            assert mode_key in card.results

        base_res = card.results["A_NoMemory"]
        cortex_res = card.results["G_FullCortexForge"]

        # Full CortexForge must explore fewer files than Baseline
        assert cortex_res.files_inspected < base_res.files_inspected
        assert card.exploration_reduction_pct > 50.0

        # Full CortexForge must consume fewer input tokens than Baseline
        assert cortex_res.input_tokens < base_res.input_tokens
        assert card.token_reduction_pct > 40.0

        # Full CortexForge must have 0 repeated failures
        assert cortex_res.repeated_failures == 0


@pytest.mark.asyncio
async def test_evaluation_ablation_study(sample_repo, test_session: AsyncSession):
    """Verify that ablation mode correctly adjusts component telemetry."""
    from cortexforge.evaluation.runner import AblationType

    project = Project(
        name="AblationRepo",
        local_path=sample_repo,
        status="ACTIVE",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    runner = EvaluationRunner()
    scorecards = await runner.run_benchmark(
        test_session, project.id, ablation=AblationType.WITHOUT_FAILURES
    )

    card = scorecards[0]
    # Without failure memory, repeated failure rate increases
    cortex_res = card.results["G_FullCortexForge"]
    assert cortex_res.repeated_failures == 1


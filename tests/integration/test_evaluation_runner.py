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
        assert "Baseline" in card.results
        assert "NaiveRAG" in card.results
        assert "FlatMemory" in card.results
        assert "CortexForge" in card.results

        base_res = card.results["Baseline"]
        cortex_res = card.results["CortexForge"]

        # CortexForge must explore fewer files than Baseline (H1)
        assert cortex_res.files_explored < base_res.files_explored
        assert card.exploration_reduction_pct > 50.0

        # CortexForge must consume fewer input tokens than Baseline (H2)
        assert cortex_res.input_tokens < base_res.input_tokens
        assert card.token_reduction_pct > 40.0

        # CortexForge must have 0 repeated failures (H3)
        assert cortex_res.repeated_failures == 0

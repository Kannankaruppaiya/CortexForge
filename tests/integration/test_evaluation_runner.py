"""Integration tests for the retrieval benchmark harness.

These tests deliberately do not assert specific metric values. Asserting that
`token_reduction_pct > 40` only tests that a constant is still the constant it was
-- which is how the previous suite passed while six of the seven benchmark modes
returned literal numbers. What is asserted here is that the harness measures real
behaviour, distinguishes measured from unmeasured, and records the conditions
every number was taken under.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.evaluation.runner import AblationType, EvaluationRunner
from cortexforge.memory.service import MemoryService

ALL_MODES = [
    "A_NoMemory",
    "B_NaiveVectorRAG",
    "C_FlatConversational",
    "D_CortexRetrievalOnly",
    "E_CortexWithProvenance",
    "F_CortexWithChangePropagation",
    "G_FullCortexForge",
]


async def _prepared_project(sample_repo, session: AsyncSession, name: str) -> Project:
    """A scanned project with grounded memories, so the benchmark has real input."""
    project = Project(name=name, local_path=sample_repo, status="ACTIVE")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    await RepositoryScanner().scan_project(session, project, incremental=False)

    memory_service = MemoryService()
    await memory_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            layer="L3",
            title="Stateless JWT token verification",
            content=(
                "Authentication uses stateless JWT verification; tokens must remain "
                "revocable through the blacklist check in AuthService."
            ),
            summary="JWT tokens must stay revocable",
            importance=0.9,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    source_type="code",
                    line_start=2,
                    line_end=4,
                )
            ],
        ),
    )
    await memory_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            layer="L3",
            title="Payment webhook idempotency",
            content="Payment webhooks require an idempotency check before recording.",
            summary="Webhook idempotency required",
            importance=0.8,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/payment.py",
                    source_type="code",
                    line_start=1,
                    line_end=6,
                )
            ],
        ),
    )
    return project


@pytest.mark.asyncio
async def test_benchmark_measures_every_mode(sample_repo, test_session: AsyncSession):
    """Every configuration is executed and produces measurements from real data."""
    project = await _prepared_project(sample_repo, test_session, "BenchmarkRepo")

    scorecards = await EvaluationRunner().run_benchmark(
        test_session, project.id, save_results=False
    )
    assert len(scorecards) >= 2

    for card in scorecards:
        assert card.task_id.startswith("BENCH-")
        for mode in ALL_MODES:
            assert mode in card.results

        baseline = card.results["A_NoMemory"]
        full = card.results["G_FullCortexForge"]

        # The baseline reads the repository, so its context is measured from the
        # files on disk and must be non-trivial.
        assert baseline.input_tokens > 0
        assert baseline.files_referenced > 0

        # Retrieval returns a bounded selection rather than the whole repository.
        assert full.context_items > 0
        assert full.input_tokens < baseline.input_tokens

        # Latency is measured, not asserted.
        assert full.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_unmeasured_fields_are_null_not_invented(
    sample_repo, test_session: AsyncSession
):
    """Anything this harness cannot measure must be null and explained.

    This is the regression guard for the removed fabricated constants. A number
    like `tests_passed=3` appearing here again would mean the benchmark had gone
    back to reporting an expectation as a measurement.
    """
    project = await _prepared_project(sample_repo, test_session, "HonestyRepo")

    card = (
        await EvaluationRunner().run_benchmark(
            test_session, project.id, save_results=False
        )
    )[0]

    for mode, result in card.results.items():
        assert result.task_success is None, (
            f"{mode} claims to know whether the task succeeded"
        )
        assert result.tests_passed is None, f"{mode} claims to know a test count"
        assert result.repeated_failures is None, (
            f"{mode} claims to know a failure count"
        )
        assert result.output_tokens is None, (
            f"{mode} claims to know generated token count"
        )
        assert result.estimated_cost_usd is None, (
            f"{mode} claims to know generation cost"
        )
        assert result.unmeasured_reason

    assert card.tool_calls_saved is None
    assert any("not measured" in note for note in card.measurement_notes)


@pytest.mark.asyncio
async def test_modes_differ_because_they_run_different_code(
    sample_repo, test_session: AsyncSession
):
    """Differences between modes must come from behaviour, not applied deltas.

    Naive vector RAG and the full pipeline are given the same project and the
    same task; any difference in what they return has to originate in the
    retrieval path each one actually executes.
    """
    project = await _prepared_project(sample_repo, test_session, "ModeDeltaRepo")

    card = (
        await EvaluationRunner().run_benchmark(
            test_session, project.id, save_results=False
        )
    )[0]

    naive = card.results["B_NaiveVectorRAG"]
    provenance = card.results["E_CortexWithProvenance"]

    # Provenance mode includes evidence anchors in the context it builds, so for
    # the same underlying memories it carries strictly more grounding information.
    assert provenance.provenance_coverage >= naive.provenance_coverage

    # Both report the basis on which relevance was judged, so a precision figure
    # can never be read without knowing what it was measured against.
    for result in card.results.values():
        assert result.relevance_basis


@pytest.mark.asyncio
async def test_run_metadata_makes_numbers_traceable(
    sample_repo, test_session: AsyncSession, tmp_path
):
    """A persisted run must record the conditions its numbers were taken under."""
    project = await _prepared_project(sample_repo, test_session, "ArtifactRepo")

    runner = EvaluationRunner(results_dir=str(tmp_path / "results"))
    card = (await runner.run_benchmark(test_session, project.id, save_results=True))[0]

    assert card.raw_log_path
    artifact = json.loads(Path(card.raw_log_path).read_text(encoding="utf-8"))

    metadata = artifact["metadata"]
    assert metadata["benchmark_suite_version"]
    assert metadata["embedding_model"]
    # The deterministic local embedding must not be presented as a semantic model.
    assert metadata["embedding_quality_class"] in (
        "LOCAL_DETERMINISTIC_HASH",
        "REAL_SEMANTIC_EMBEDDING",
    )
    assert metadata["retrieval_config"]["ablation"] == "none"
    assert metadata["project_memory_count"] >= 2
    assert artifact["measurement_notes"]


@pytest.mark.asyncio
async def test_ablation_changes_the_pipeline_not_the_numbers(
    sample_repo, test_session: AsyncSession
):
    """An ablation must disable a real signal, not overwrite a reported metric.

    The previous implementation "applied" an ablation by assigning a worse value
    to the result object. Here the ablation is recorded in the run configuration
    and takes effect by changing which retrieval signals run, so its effect is
    whatever actually happens.
    """
    project = await _prepared_project(sample_repo, test_session, "AblationRepo")
    runner = EvaluationRunner()

    baseline_card = (
        await runner.run_benchmark(test_session, project.id, save_results=False)
    )[0]
    ablated_card = (
        await runner.run_benchmark(
            test_session,
            project.id,
            ablation=AblationType.WITHOUT_PROVENANCE,
            save_results=False,
        )
    )[0]

    assert baseline_card.metadata.retrieval_config["ablation"] == "none"
    assert ablated_card.metadata.retrieval_config["ablation"] == "without_provenance"

    # With provenance disabled the context carries no evidence anchors, so the
    # measured context is no larger than the run that included them.
    assert (
        ablated_card.results["E_CortexWithProvenance"].input_tokens
        <= baseline_card.results["E_CortexWithProvenance"].input_tokens
    )

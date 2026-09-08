"""REST API routes for First-Class Project Cognition: Invariants, Provenance, Snapshots, Tests & Failures."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.core.db import get_db_session
from cortexforge.core.models import (
    ArchitectureRule,
    FailureEpisode,
    Project,
    TestRun,
)
from cortexforge.core.schemas import (
    ArchitectureRuleCreate,
    ArchitectureRuleRead,
    CognitiveSnapshotRead,
    FailureEpisodeRead,
    ProvenanceTraceRead,
    RuleViolationRead,
    TestRunRead,
)
from cortexforge.evaluation.mutations import MutationBenchmarkHarness
from cortexforge.graph.service import GraphService
from cortexforge.memory.provenance import ProvenanceEngine
from cortexforge.memory.snapshots import CognitiveSnapshotEngine

router = APIRouter(tags=["cognition"])

invariant_engine = ArchitectureInvariantEngine()
provenance_engine = ProvenanceEngine()
snapshot_engine = CognitiveSnapshotEngine()
graph_service = GraphService()


# ==================== 1. ARCHITECTURE INVARIANTS & VIOLATIONS ====================

@router.get("/projects/{project_id}/architecture/rules", response_model=list[ArchitectureRuleRead])
async def list_architecture_rules(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[ArchitectureRuleRead]:
    """List defined architectural boundary rules for a project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    stmt = select(ArchitectureRule).where(ArchitectureRule.project_id == project_id)
    res = await session.execute(stmt)
    return [ArchitectureRuleRead.model_validate(r) for r in res.scalars().all()]


@router.post("/projects/{project_id}/architecture/rules", response_model=ArchitectureRuleRead, status_code=status.HTTP_201_CREATED)
async def create_architecture_rule(
    project_id: str,
    payload: ArchitectureRuleCreate,
    session: AsyncSession = Depends(get_db_session),
) -> ArchitectureRuleRead:
    """Create a new architectural invariant boundary rule."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    rule = ArchitectureRule(
        project_id=project_id,
        rule_name=payload.rule_name,
        description=payload.description,
        scope=payload.scope,
        severity=payload.severity,
        forbidden_source_pattern=payload.forbidden_source_pattern,
        forbidden_target_pattern=payload.forbidden_target_pattern,
        enforcement_status=payload.enforcement_status,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return ArchitectureRuleRead.model_validate(rule)



@router.get("/projects/{project_id}/architecture/violations", response_model=list[RuleViolationRead])
async def check_architecture_violations(
    project_id: str,
    commit_sha: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[RuleViolationRead]:
    """Check and return all active architecture boundary rule violations."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    violations = await invariant_engine.check_project_invariants(
        session, project_id=project_id, commit_sha=commit_sha
    )
    return [RuleViolationRead.model_validate(v) for v in violations]


# ==================== 2. PROVENANCE GRAPH ====================

@router.get("/memories/{memory_id}/provenance", response_model=ProvenanceTraceRead)
async def get_memory_provenance(
    memory_id: str, session: AsyncSession = Depends(get_db_session)
) -> ProvenanceTraceRead:
    """Answer 'Why does CortexForge believe this?' by tracing full causal provenance."""
    trace = await provenance_engine.trace_memory(session, memory_id=memory_id)
    if not trace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return ProvenanceTraceRead.model_validate(trace)


# ==================== 3. COGNITIVE SNAPSHOTS & DETERMINISTIC REPLAY ====================

@router.get("/projects/{project_id}/snapshots", response_model=list[CognitiveSnapshotRead])
async def list_snapshots(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[CognitiveSnapshotRead]:
    """List cognitive snapshots for a project across commit generations."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    snapshots = await snapshot_engine.list_snapshots(session, project_id)
    return [CognitiveSnapshotRead.model_validate(s) for s in snapshots]


@router.post("/projects/{project_id}/snapshots", response_model=CognitiveSnapshotRead, status_code=status.HTTP_201_CREATED)
async def take_snapshot(
    project_id: str,
    commit_sha: str,
    session: AsyncSession = Depends(get_db_session),
) -> CognitiveSnapshotRead:
    """Capture a deterministic cognitive snapshot of project state at a commit."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    snapshot = await snapshot_engine.take_snapshot(session, project_id, commit_sha)
    return CognitiveSnapshotRead.model_validate(snapshot)


@router.get("/projects/{project_id}/snapshots/{commit_sha}", response_model=CognitiveSnapshotRead)
async def get_snapshot_at_commit(
    project_id: str,
    commit_sha: str,
    session: AsyncSession = Depends(get_db_session),
) -> CognitiveSnapshotRead:
    """Retrieve cognitive snapshot metadata for a specific commit."""
    snapshot = await snapshot_engine.get_snapshot_at_commit(session, project_id, commit_sha)
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found for commit")
    return CognitiveSnapshotRead.model_validate(snapshot)


@router.post("/projects/{project_id}/snapshots/{commit_sha}/replay")
async def replay_state_at_commit(
    project_id: str,
    commit_sha: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Reconstruct exact cognitive and architectural state at a given commit."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    state = await snapshot_engine.replay_state_at_commit(session, project_id, commit_sha)
    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Could not replay state at commit")
    return state


# ==================== 4. TEST INTELLIGENCE & FAILURE EPISODES ====================

@router.get("/projects/{project_id}/tests", response_model=list[TestRunRead])
async def list_test_runs(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[TestRunRead]:
    """List historical test runs and test case results for the project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    stmt = (
        select(TestRun)
        .where(TestRun.project_id == project_id)
        .options(selectinload(TestRun.results))
        .order_by(TestRun.created_at.desc())
        .limit(50)
    )

    res = await session.execute(stmt)
    return [TestRunRead.model_validate(r) for r in res.scalars().all()]


@router.get("/projects/{project_id}/failures", response_model=list[FailureEpisodeRead])
async def list_failure_episodes(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[FailureEpisodeRead]:
    """List recorded failure episodes, root causes, and fix attempts."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    stmt = (
        select(FailureEpisode)
        .where(FailureEpisode.project_id == project_id)
        .options(selectinload(FailureEpisode.fix_attempts))
        .order_by(FailureEpisode.created_at.desc())
        .limit(50)
    )
    res = await session.execute(stmt)
    return [FailureEpisodeRead.model_validate(f) for f in res.scalars().all()]


# ==================== 5. MUTATION BENCHMARK ====================

@router.post("/projects/{project_id}/mutations/benchmark")
async def run_mutation_benchmark(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """Run deterministic repository mutation benchmark evaluating cognitive update accuracy."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    harness = MutationBenchmarkHarness()
    results = await harness.run_suite(session, project_id)
    return {
        "project_id": project_id,
        "results": [
            {
                "test_id": r.test_id,
                "mutation_type": r.mutation_type,
                "passed": r.passed,
                "actual_status": r.actual_status,
                "expected_status": r.expected_status,
                "reanchored_as_expected": r.reanchored_as_expected,
                "invalidated_as_expected": r.invalidated_as_expected,
                "details": r.details,
            }
            for r in results
        ],
        "all_passed": all(r.passed for r in results),
    }


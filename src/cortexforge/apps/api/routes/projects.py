"""REST API routes for Projects and Repository Scanning."""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import get_db_session
from cortexforge.core.models import CodeEntity, Memory, Project
from cortexforge.core.schemas import (
    ArchitectureResponse,
    ProjectCreate,
    ProjectRead,
    ScanRequest,
    ScanResponse,
)
from cortexforge.evaluation.runner import EvaluationRunner
from cortexforge.graph.service import GraphService
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine

router = APIRouter(prefix="/projects", tags=["projects"])
scanner = RepositoryScanner()
graph_service = GraphService()
retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
context_composer = ContextComposer(retrieval_engine=retrieval_engine, graph_service=graph_service)
evaluation_runner = EvaluationRunner(
    retrieval_engine=retrieval_engine, context_composer=context_composer, scanner=scanner
)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: AsyncSession = Depends(get_db_session)
) -> ProjectRead:
    """Register a new repository with CortexForge."""
    canonical_path = os.path.realpath(payload.local_path)
    if not os.path.exists(canonical_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Local path does not exist: {payload.local_path}",
        )

    # Check if project already registered at this path
    existing_stmt = select(Project).where(Project.local_path == canonical_path)
    existing_res = await session.execute(existing_stmt)
    if existing_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project already registered at {canonical_path}",
        )

    project = Project(
        name=payload.name,
        repository_url=payload.repository_url,
        local_path=canonical_path,
        default_branch=payload.default_branch,
        language=payload.language,
        status="INITIALIZING",
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    session: AsyncSession = Depends(get_db_session),
) -> list[ProjectRead]:
    """List all registered projects."""
    stmt = select(Project).order_by(Project.created_at.desc())
    res = await session.execute(stmt)
    projects = res.scalars().all()
    results: list[ProjectRead] = []

    for p in projects:
        # Count entities & memories
        entity_count = await session.scalar(
            select(func.count(CodeEntity.id)).where(CodeEntity.project_id == p.id)
        )
        memory_count = await session.scalar(
            select(func.count(Memory.id)).where(Memory.project_id == p.id)
        )
        read_obj = ProjectRead.model_validate(p)
        read_obj.entity_count = entity_count or 0
        read_obj.memory_count = memory_count or 0
        results.append(read_obj)

    return results


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> ProjectRead:
    """Retrieve details for a registered project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    entity_count = await session.scalar(
        select(func.count(CodeEntity.id)).where(CodeEntity.project_id == project.id)
    )
    memory_count = await session.scalar(
        select(func.count(Memory.id)).where(Memory.project_id == project.id)
    )
    read_obj = ProjectRead.model_validate(project)
    read_obj.entity_count = entity_count or 0
    read_obj.memory_count = memory_count or 0
    return read_obj


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> None:
    """Unregister and remove a project and all associated entities."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await session.delete(project)
    await session.commit()


@router.post("/{project_id}/scan", response_model=ScanResponse)
async def scan_project(
    project_id: str,
    payload: ScanRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ScanResponse:
    """Trigger AST scan of the project repository."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    incremental = payload.incremental if payload else True
    max_files = payload.max_files if payload else None

    return await scanner.scan_project(
        session, project, incremental=incremental, max_files=max_files
    )


@router.get("/{project_id}/architecture", response_model=ArchitectureResponse)
async def get_project_architecture(
    project_id: str,
    depth: int = 2,
    session: AsyncSession = Depends(get_db_session),
) -> ArchitectureResponse:
    """Retrieve synthesized structural architecture of the project."""
    arch = await graph_service.get_project_architecture(session, project_id, depth=depth)
    if not arch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return arch


@router.post("/{project_id}/benchmark")
async def run_project_benchmark(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    """Run real empirical benchmark suite on project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    scorecards = await evaluation_runner.run_benchmark(session, project.id)
    out = []
    for sc in scorecards:
        results_dict = {}
        for mode, res in sc.results.items():
            results_dict[mode] = {
                "mode": res.mode,
                "files_explored": res.files_explored,
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
                "tool_calls": res.tool_calls,
                "duration_ms": round(res.duration_ms, 2),
                "repeated_failures": res.repeated_failures,
                "success": res.success,
            }
        out.append({
            "task_id": sc.task_id,
            "task_name": sc.task_name,
            "results": results_dict,
            "token_reduction_pct": sc.token_reduction_pct,
            "exploration_reduction_pct": sc.exploration_reduction_pct,
            "tool_calls_saved": sc.tool_calls_saved,
        })
    return out

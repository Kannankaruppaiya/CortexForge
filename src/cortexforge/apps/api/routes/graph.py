"""REST API routes for Graph Traversal and Impact Analysis."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project
from cortexforge.core.schemas import ChangeImpactRequest, ChangeImpactResponse
from cortexforge.graph.service import GraphService

router = APIRouter(prefix="/projects", tags=["graph"])
graph_service = GraphService()
change_propagator = SemanticChangePropagator(graph_service=graph_service)


@router.get("/{project_id}/graph/dependencies")
async def get_dependencies(
    project_id: str,
    entity: str,
    depth: int = 2,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, str]]:
    """Retrieve downstream dependencies for a given entity symbol or ID."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    return await graph_service.get_dependencies(
        session, project_id=project_id, entity_name_or_id=entity, depth=depth
    )


@router.get("/{project_id}/graph/dependents")
async def get_dependents(
    project_id: str,
    entity: str,
    depth: int = 2,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, str]]:
    """Retrieve upstream callers and dependents for an entity to evaluate blast radius."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    return await graph_service.get_dependents(
        session, project_id=project_id, entity_name_or_id=entity, depth=depth
    )


@router.post("/{project_id}/impact", response_model=ChangeImpactResponse)
async def check_change_impact(
    project_id: str,
    payload: ChangeImpactRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeImpactResponse:
    """Analyze blast radius of proposed file changes across the code graph and memory layer."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    report = await change_propagator.propagate_changes(
        session,
        project_id=project_id,
        modified_files=payload.modified_files,
        mark_stale=payload.mark_stale,
    )
    return ChangeImpactResponse(
        project_id=project_id,
        modified_files=report.modified_files,
        directly_changed_entities=report.directly_changed_entities,
        affected_dependents=report.affected_dependents,
        memories_flagged_stale=report.memories_flagged_stale,
        critical_constraints=report.critical_constraints,
        warnings=report.warnings,
    )

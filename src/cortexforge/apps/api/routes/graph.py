"""REST API routes for Graph Traversal and Impact Analysis."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project
from cortexforge.graph.service import GraphService

router = APIRouter(prefix="/projects", tags=["graph"])
graph_service = GraphService()


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await graph_service.get_dependents(
        session, project_id=project_id, entity_name_or_id=entity, depth=depth
    )

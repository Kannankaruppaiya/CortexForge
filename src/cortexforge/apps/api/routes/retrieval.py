"""REST API routes for Hybrid Retrieval, Context Composition, and Impact Pre-check."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import (
    ChangeImpactReport,
    SemanticChangePropagator,
)
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine, ScoredItem

router = APIRouter(prefix="/projects", tags=["retrieval"])
retrieval_engine = HybridRetrievalEngine()
context_composer = ContextComposer(retrieval_engine=retrieval_engine)
change_propagator = SemanticChangePropagator()


class RetrieveRequest(BaseModel):
    query: str
    task_type: str | None = None
    target_files: list[str] | None = None
    limit: int = Field(10, ge=1, le=50)


class ContextRequest(BaseModel):
    task_text: str
    profile: str = Field("medium", description="small, medium, or large")
    target_files: list[str] | None = None
    max_tokens: int | None = None


class ImpactRequest(BaseModel):
    modified_files: list[str]
    mark_stale: bool = False


@router.post("/{project_id}/retrieve", response_model=list[ScoredItem])
async def retrieve_memories(
    project_id: str,
    payload: RetrieveRequest,
    session: AsyncSession = Depends(get_db_session),
) -> list[ScoredItem]:
    """Execute multi-signal hybrid retrieval over memories and code entities."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    return await retrieval_engine.retrieve(
        session,
        project_id=project_id,
        query=payload.query,
        task_type=payload.task_type,
        target_files=payload.target_files,
        limit=payload.limit,
    )


@router.post("/{project_id}/context")
async def compose_context(
    project_id: str,
    payload: ContextRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Build structured, token-budget-aware context block for AI agent prompt injection."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    context_markdown = await context_composer.build_context(
        session,
        project_id=project_id,
        task_text=payload.task_text,
        profile=payload.profile,
        target_files=payload.target_files,
        max_tokens=payload.max_tokens,
    )
    return {
        "project_id": project_id,
        "profile": payload.profile,
        "token_estimate": len(context_markdown.split()) * 1.3,
        "context": context_markdown,
    }


@router.post("/{project_id}/impact")
async def analyze_impact(
    project_id: str,
    payload: ImpactRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Pre-action blast radius check for proposed file modifications."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    report: ChangeImpactReport = await change_propagator.propagate_changes(
        session,
        project_id=project_id,
        modified_files=payload.modified_files,
        mark_stale=payload.mark_stale,
    )
    return {
        "project_id": project_id,
        "modified_files": report.modified_files,
        "directly_changed_entities": report.directly_changed_entities,
        "affected_dependents": report.affected_dependents,
        "memories_flagged_stale": report.memories_flagged_stale,
        "critical_constraints": report.critical_constraints,
        "warnings": report.warnings,
    }

"""REST API routes for Layered Memory Management and Verification."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryRead
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import ConcurrentModificationError, MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.security.auth import (
    Principal,
    RequireProjectAccess,
    get_current_principal,
)

router = APIRouter(tags=["memories"])
memory_service = MemoryService()
verification_engine = MemoryVerificationEngine()
consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)


class MemoryUpdatePayload(BaseModel):
    content: str
    change_reason: str
    title: str | None = None
    summary: str | None = None
    expected_version: int | None = None


class MemoryDeprecatePayload(BaseModel):
    reason: str = "Explicitly deprecated by developer or agent"
    superseded_by_id: str | None = None


@router.post(
    "/projects/{project_id}/memories",
    response_model=MemoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory(
    project_id: str,
    payload: MemoryCreate,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(RequireProjectAccess("project_id")),
) -> MemoryRead:
    """Create a durable, evidence-grounded project memory."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    memory = await memory_service.create_memory(session, project_id, payload)
    return MemoryRead.model_validate(memory)


@router.get("/projects/{project_id}/memories", response_model=list[MemoryRead])
async def list_memories(
    project_id: str,
    type: str | None = None,
    status: str | None = None,
    min_importance: float = 0.0,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(RequireProjectAccess("project_id")),
) -> list[MemoryRead]:
    """List project memories with multi-attribute filtering."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    memories = await memory_service.list_memories(
        session,
        project_id=project_id,
        memory_type=type,
        status=status,
        min_importance=min_importance,
        limit=limit,
        offset=offset,
    )
    return [MemoryRead.model_validate(m) for m in memories]


@router.get("/memories/{memory_id}", response_model=MemoryRead)
async def get_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> MemoryRead:
    """Retrieve full memory record with evidences and version history."""
    memory = await memory_service.get_memory(session, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    if not principal.can_access_project(memory.project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to project '{memory.project_id}' for this memory.",
        )
    return MemoryRead.model_validate(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: str,
    payload: MemoryUpdatePayload,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> MemoryRead:
    """Update memory content with audit-trailed version increment and optimistic locking."""
    target_mem = await memory_service.get_memory(session, memory_id)
    if not target_mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    if not principal.can_access_project(target_mem.project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to project '{target_mem.project_id}'.",
        )

    try:
        updated = await memory_service.update_memory(
            session,
            memory_id=memory_id,
            content=payload.content,
            change_reason=payload.change_reason,
            title=payload.title,
            summary=payload.summary,
            expected_version=payload.expected_version,
        )
    except ConcurrentModificationError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    return MemoryRead.model_validate(updated)


@router.post("/memories/{memory_id}/verify")
async def verify_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Trigger active verification of a memory against current source code."""
    memory = await memory_service.get_memory(session, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    if not principal.can_access_project(memory.project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to project '{memory.project_id}'.",
        )

    project = await session.get(Project, memory.project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    status_result = await verification_engine.verify_single_memory(
        session, memory, project.local_path
    )
    await session.commit()
    return {
        "memory_id": memory.id,
        "status": status_result,
        "verified_at": memory.last_verified_at,
    }


@router.post("/memories/{memory_id}/deprecate", response_model=MemoryRead)
async def deprecate_memory(
    memory_id: str,
    payload: MemoryDeprecatePayload,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> MemoryRead:
    """Deprecate a memory with explicit supersession or invalidation reason."""
    target_mem = await memory_service.get_memory(session, memory_id)
    if not target_mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    if not principal.can_access_project(target_mem.project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to project '{target_mem.project_id}'.",
        )

    deprecated = await memory_service.deprecate_memory(
        session,
        memory_id=memory_id,
        superseded_by_id=payload.superseded_by_id,
        reason=payload.reason,
    )
    if not deprecated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    return MemoryRead.model_validate(deprecated)


@router.post("/projects/{project_id}/consolidate")
async def consolidate_memories(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(RequireProjectAccess("project_id")),
) -> dict[str, Any]:
    """Trigger memory consolidation loop."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    return await consolidation_engine.consolidate_project(session, project_id)

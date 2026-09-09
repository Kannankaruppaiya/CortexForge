"""REST API routes for background asynchronous jobs backed by DurableJobStore."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status

from cortexforge.core.db import session_scope
from cortexforge.core.models import Job
from cortexforge.jobs.durable import DurableJobStore
from cortexforge.jobs.runner import JobRunner
from cortexforge.jobs.tasks import (
    benchmark_project_task,
    consolidate_project_task,
    rebuild_project_task,
    scan_project_task,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])
job_store = DurableJobStore()

_runner = JobRunner(store=job_store)
_runner.register("REBUILD", rebuild_project_task)
_runner.register("SCAN", scan_project_task)
_runner.register("CONSOLIDATE", consolidate_project_task)
_runner.register("BENCHMARK", benchmark_project_task)


def _serialize_job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "project_id": job.project_id,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "parameters": job.parameters,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _dispatch_runner_background() -> None:
    """Trigger an async task to process claimable jobs."""
    asyncio.create_task(_runner.run_once())


@router.get("", response_model=list[dict[str, Any]])
async def list_jobs(project_id: str | None = None) -> list[dict[str, Any]]:
    """List background jobs optionally filtered by project_id."""
    async with session_scope() as session:
        jobs = await job_store.list_jobs(session, project_id=project_id)
        return [_serialize_job(j) for j in jobs]


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """Get status and result for a specific background job."""
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )
        return _serialize_job(job)


@router.post("/projects/{project_id}/rebuild")
async def trigger_rebuild_job(project_id: str) -> dict[str, Any]:
    """Trigger background clean rebuild and recovery of project cognitive model."""
    async with session_scope() as session:
        job, _ = await job_store.submit(
            session,
            job_type="REBUILD",
            project_id=project_id,
        )
        data = _serialize_job(job)
    _dispatch_runner_background()
    return data


@router.post("/projects/{project_id}/scan")
async def trigger_scan_job(project_id: str, incremental: bool = True) -> dict[str, Any]:
    """Trigger background AST scanner job."""
    async with session_scope() as session:
        job, _ = await job_store.submit(
            session,
            job_type="SCAN",
            project_id=project_id,
            parameters={"incremental": incremental},
        )
        data = _serialize_job(job)
    _dispatch_runner_background()
    return data


@router.post("/projects/{project_id}/consolidate")
async def trigger_consolidation_job(project_id: str) -> dict[str, Any]:
    """Trigger background memory consolidation job."""
    async with session_scope() as session:
        job, _ = await job_store.submit(
            session,
            job_type="CONSOLIDATE",
            project_id=project_id,
        )
        data = _serialize_job(job)
    _dispatch_runner_background()
    return data

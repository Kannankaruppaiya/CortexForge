"""REST API routes for background asynchronous jobs."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from cortexforge.jobs.manager import JobManager, JobRecord
from cortexforge.jobs.tasks import (
    consolidate_project_task,
    rebuild_project_task,
    scan_project_task,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])
job_manager = JobManager.get_instance()


def _serialize_job(job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "project_id": job.project_id,
        "status": job.status.value,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("", response_model=list[dict[str, Any]])
async def list_jobs(project_id: str | None = None) -> list[dict[str, Any]]:
    """List background jobs optionally filtered by project_id."""
    jobs = job_manager.list_jobs(project_id=project_id)
    return [_serialize_job(j) for j in jobs]


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """Get status and result for a specific background job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")
    return _serialize_job(job)


@router.post("/projects/{project_id}/rebuild")
async def trigger_rebuild_job(project_id: str) -> dict[str, Any]:
    """Trigger background clean rebuild and recovery of project cognitive model."""
    job = job_manager.submit_job(
        job_type="REBUILD",
        project_id=project_id,
        coro_func=rebuild_project_task,
    )
    return _serialize_job(job)


@router.post("/projects/{project_id}/scan")
async def trigger_scan_job(project_id: str, incremental: bool = True) -> dict[str, Any]:
    """Trigger background AST scanner job."""
    job = job_manager.submit_job(
        job_type="SCAN",
        project_id=project_id,
        coro_func=scan_project_task,
        metadata={"incremental": incremental},
    )
    return _serialize_job(job)


@router.post("/projects/{project_id}/consolidate")
async def trigger_consolidation_job(project_id: str) -> dict[str, Any]:
    """Trigger background memory consolidation job."""
    job = job_manager.submit_job(
        job_type="CONSOLIDATE",
        project_id=project_id,
        coro_func=consolidate_project_task,
    )
    return _serialize_job(job)

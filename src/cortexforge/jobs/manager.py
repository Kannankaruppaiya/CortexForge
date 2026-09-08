"""Background job runner, async task queue, and caching engine for CortexForge."""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Execution status for background jobs."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class JobRecord:
    """State record for an asynchronous job."""

    id: str
    job_type: str
    project_id: str
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JobManager:
    """Asynchronous background job manager with in-memory execution and optional Redis support."""

    _instance: "JobManager | None" = None

    def __init__(self, redis_url: str | None = None) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.redis_url = redis_url or os.getenv("CORTEX_REDIS_URL")
        self._redis_client: Any = None

    @classmethod
    def get_instance(cls) -> "JobManager":
        """Singleton accessor for background job manager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_job(self, job_id: str) -> JobRecord | None:
        """Retrieve current job record by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self, project_id: str | None = None) -> list[JobRecord]:
        """List jobs optionally filtered by project_id."""
        jobs = list(self._jobs.values())
        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def submit_job(
        self,
        job_type: str,
        project_id: str,
        coro_func: Callable[[JobRecord], Coroutine[Any, Any, dict[str, Any]]],
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        """Submit a background job for asynchronous execution."""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        record = JobRecord(
            id=job_id,
            job_type=job_type,
            project_id=project_id,
            metadata=metadata or {},
        )
        self._jobs[job_id] = record

        async def _run_wrapper() -> None:
            record.status = JobStatus.RUNNING
            record.started_at = datetime.now(UTC)
            try:
                result = await coro_func(record)
                record.result = result
                record.status = JobStatus.COMPLETED
                record.progress = 1.0
            except asyncio.CancelledError:
                record.status = JobStatus.CANCELLED
                record.error = "Job was cancelled."
            except Exception as exc:
                logger.exception("Background job %s failed", job_id)
                record.status = JobStatus.FAILED
                record.error = str(exc)

            finally:
                record.completed_at = datetime.now(UTC)

        task = asyncio.create_task(_run_wrapper())
        self._tasks[job_id] = task
        return record

    def cancel_job(self, job_id: str) -> bool:
        """Attempt to cancel an active job."""
        task = self._tasks.get(job_id)
        record = self._jobs.get(job_id)
        if task and not task.done():
            task.cancel()
            if record:
                record.status = JobStatus.CANCELLED
            return True
        return False


class CacheManager:
    """Lightweight in-memory TTL caching engine with optional Redis backing."""

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self.default_ttl = default_ttl_seconds

    def get(self, key: str) -> Any | None:
        """Get cached value if present and not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, val = entry
        if time.time() > expires_at:
            del self._cache[key]
            return None
        return val

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store value with TTL in seconds."""
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = time.time() + ttl
        self._cache[key] = (expires_at, value)

    def delete(self, key: str) -> None:
        """Invalidate single key."""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate all keys matching prefix."""
        to_del = [k for k in self._cache if k.startswith(prefix)]
        for k in to_del:
            del self._cache[k]
        return len(to_del)

    def clear(self) -> None:
        """Clear entire cache."""
        self._cache.clear()

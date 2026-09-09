"""Durable job execution engine and registry (specification sections 38 and 39).

Executes background jobs persisted in ``DurableJobStore``:
- Dispatches claimed jobs to registered handlers using ``JobContext``.
- Maintains lease ownership via a background heartbeat loop.
- Records stage checkpoints so interrupted jobs resume rather than restarting.
- Commits job completion and failure in dedicated sessions so task-level rollback
  never discards the failure record.
- Flags unknown job types as non-retriable failures.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import session_scope
from cortexforge.core.models import Job
from cortexforge.jobs.context import JobContext
from cortexforge.jobs.durable import ClaimedJob, DurableJobStore

logger = logging.getLogger(__name__)

JobHandler = Callable[[JobContext], Awaitable[dict[str, Any]]]


class JobRunner:
    """Executes durable background jobs with leases, heartbeats, and checkpointing."""

    def __init__(
        self,
        store: DurableJobStore | None = None,
        worker_id: str | None = None,
        heartbeat_interval_seconds: float | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store or DurableJobStore()
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:10]}"
        self.heartbeat_interval = heartbeat_interval_seconds or max(
            5.0, self.store.lease_seconds / 3.0
        )
        self.session_factory = session_factory
        self._registry: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        """Register a handler routine for a specific job type."""
        self._registry[job_type.upper()] = handler

    def has_handler(self, job_type: str) -> bool:
        """Check if a handler is registered for the specified job type."""
        return job_type.upper() in self._registry

    @asynccontextmanager
    async def _scoped_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self.session_factory is not None:
            async with self.session_factory() as session:
                yield session
                await session.commit()
        else:
            async with session_scope() as session:
                yield session

    async def _heartbeat_loop(
        self, job_id: str, stop_event: asyncio.Event
    ) -> None:
        """Periodically renew lease ownership while the job execution is active."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_interval)
                break
            except TimeoutError:
                pass

            try:
                async with self._scoped_session() as session:
                    renewed = await self.store.renew_lease(
                        session, job_id, self.worker_id
                    )
                    if not renewed:
                        logger.warning(
                            "Failed to renew lease for job %s under worker %s; "
                            "lease may have expired or been taken over.",
                            job_id,
                            self.worker_id,
                        )
                        break
            except Exception:
                logger.exception("Unexpected error renewing lease for job %s", job_id)

    async def execute_job(self, claimed: ClaimedJob) -> Job:
        """Execute a claimed job with heartbeat renewal and checkpoint tracking."""
        job_id = claimed.job.id
        job_type = claimed.job.job_type.upper()
        handler = self._registry.get(job_type)

        if handler is None:
            err_msg = f"No handler registered for job type '{job_type}'."
            logger.error("Job %s failed: %s", job_id, err_msg)
            async with self._scoped_session() as session:
                failed_job = await self.store.fail(
                    session,
                    job_id,
                    self.worker_id,
                    error=err_msg,
                    retry=False,
                )
                if failed_job is None:
                    raise RuntimeError(f"Could not record failure for job {job_id}")
                return failed_job

        async def _checkpoint_writer(
            state: dict[str, Any], progress: float
        ) -> None:
            async with self._scoped_session() as session:
                await self.store.checkpoint(
                    session,
                    job_id,
                    self.worker_id,
                    checkpoint=state,
                    progress=progress,
                )

        context = JobContext(
            job_id=job_id,
            job_type=job_type,
            project_id=claimed.job.project_id or "",
            metadata=dict(claimed.job.parameters or {}),
            resumed_from=dict(claimed.previous_checkpoint or {}),
            attempt=claimed.job.attempt,
            progress=claimed.job.progress,
            writer=_checkpoint_writer,
        )

        stop_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id, stop_event)
        )

        from cortexforge.observability.tracing import start_async_span

        try:
            async with start_async_span(
                "job.execute",
                {
                    "job_id": job_id,
                    "job_type": job_type,
                    "project_id": claimed.job.project_id or "",
                    "attempt": claimed.job.attempt,
                },
            ):
                result = await handler(context)
        except Exception as exc:
            stop_event.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            logger.exception("Handler raised error executing job %s", job_id)
            async with self._scoped_session() as session:
                failed_job = await self.store.fail(
                    session,
                    job_id,
                    self.worker_id,
                    error=str(exc),
                    retry=True,
                )
                if failed_job is None:
                    raise RuntimeError(f"Could not record failure for job {job_id}") from exc
                return failed_job
        else:
            stop_event.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            async with self._scoped_session() as session:
                completed_job = await self.store.complete(
                    session,
                    job_id,
                    self.worker_id,
                    result=result,
                )
                if completed_job is None:
                    raise RuntimeError(f"Could not record completion for job {job_id}")
                return completed_job

    async def run_once(
        self, job_types: list[str] | None = None
    ) -> Job | None:
        """Claim and execute the oldest available job, if any."""
        claimed: ClaimedJob | None = None
        async with self._scoped_session() as session:
            claimed = await self.store.claim_next(
                session, self.worker_id, job_types=job_types
            )

        if claimed is None:
            return None

        return await self.execute_job(claimed)

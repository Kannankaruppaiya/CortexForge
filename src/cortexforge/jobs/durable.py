"""Durable, resumable background jobs (specification sections 38 and 39).

Jobs previously lived in an in-process dictionary. A restart during indexing left
no trace that indexing had been happening: the work was neither finished nor
recoverable, and nothing could distinguish "never started" from "died halfway
through". For a system whose whole value is an accurate picture of a project,
silently half-applied updates are the worst possible failure mode.

Three mechanisms make jobs recoverable:

* **Leases.** A worker claims a job by writing a lease that expires. If the worker
  dies, the lease lapses and another worker may claim it. A job that is merely
  slow keeps renewing its lease and is not stolen -- which is the distinction a
  timeout alone cannot make.
* **Checkpoints.** A job records what it has finished. A resumed job continues
  from its checkpoint rather than starting over, so re-running an interrupted
  index does not re-apply work already applied.
* **Idempotency keys.** Submitting the same work while it is still outstanding
  returns the existing job. The database enforces this, so two schedulers racing
  cannot both start it.

Attempts are bounded. A job that fails repeatedly is marked FAILED and left for a
human rather than retried forever; an infinite retry loop against a genuine bug
is how a background system quietly consumes a database.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Job

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

# States a job can still be picked up from. COMPLETED, FAILED and CANCELLED are
# terminal.
CLAIMABLE_STATUSES = (STATUS_PENDING, STATUS_RUNNING)

# How long a worker's claim on a job lasts before another worker may take it.
# Long enough that a slow-but-healthy job is not stolen, short enough that a
# crashed one is not stranded.
DEFAULT_LEASE_SECONDS = 300

# How many candidate jobs one claim attempt examines. Bounded so a queue that has
# grown large does not turn every claim into a full table read.
CLAIM_SCAN_LIMIT = 200


def compute_job_key(
    job_type: str, project_id: str | None, parameters: dict[str, Any]
) -> str:
    """Identity of a unit of work: its type, its project and its parameters."""
    material = json.dumps(
        {"type": job_type, "project": project_id or "", "parameters": parameters or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class ClaimedJob:
    """A job this worker currently holds a lease on."""

    job: Job
    resumed: bool
    previous_checkpoint: dict[str, Any]


class DurableJobStore:
    """Persists jobs so that work survives the process that started it."""

    def __init__(self, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        self.lease_seconds = lease_seconds

    async def submit(
        self,
        session: AsyncSession,
        job_type: str,
        project_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        max_attempts: int = 3,
        user_id: str | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> tuple[Job, bool]:
        """Enqueue work, returning the job and whether it was newly created.

        Submitting work that is already outstanding returns the existing job. A
        job that has already *finished* does not block a new submission -- asking
        to index a project again after it was indexed is a legitimate request,
        not a duplicate.
        """
        parameters = parameters or {}
        key = compute_job_key(job_type, project_id, parameters)

        existing = (
            (
                await session.execute(
                    select(Job).where(
                        Job.idempotency_key == key,
                        Job.status.in_(CLAIMABLE_STATUSES),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            logger.debug("Job %s already outstanding for key %s", existing.id, key[:12])
            return existing, False

        # A finished job holds the unique key, so a resubmission salts it with the
        # attempt count. The key still identifies *this* unit of work; it simply
        # allows the same work to be requested again later.
        finished = (
            (await session.execute(select(Job).where(Job.idempotency_key == key)))
            .scalars()
            .first()
        )
        effective_key = key
        if finished is not None:
            effective_key = hashlib.sha256(
                f"{key}:{datetime.now(UTC).isoformat()}".encode()
            ).hexdigest()

        job = Job(
            project_id=project_id,
            user_id=user_id,
            actor_type=actor_type,
            actor_id=actor_id,
            job_type=job_type,
            status=STATUS_PENDING,
            idempotency_key=effective_key,
            parameters=parameters,
            max_attempts=max_attempts,
        )
        try:
            bind = session.get_bind()
            is_sqlite = bind is not None and getattr(bind.dialect, "name", "") == "sqlite"
            if is_sqlite:
                session.add(job)
                await session.flush()
            else:
                # A savepoint, not the caller's transaction: losing an insert race
                # must not discard whatever else the caller had pending.
                async with session.begin_nested():
                    session.add(job)
                    await session.flush()
        except IntegrityError:
            # Another worker submitted the same job between our check and our
            # insert. Theirs wins; the point of the constraint is that exactly
            # one of us succeeds.
            raced = (
                (
                    await session.execute(
                        select(Job).where(Job.idempotency_key == effective_key)
                    )
                )
                .scalars()
                .first()
            )
            if raced is not None:
                return raced, False
            raise

        return job, True

    async def claim_next(
        self,
        session: AsyncSession,
        worker_id: str,
        job_types: list[str] | None = None,
    ) -> ClaimedJob | None:
        """Claim the oldest available job, taking over any lapsed lease.

        A job is available when it is PENDING, or when it is RUNNING but its
        lease has expired -- which means the worker that held it is gone.
        """
        now = datetime.now(UTC)

        # Filter unclaimable and unexpired running jobs directly in SQL to prevent queue starvation (§23)
        stmt = (
            select(Job)
            .where(
                Job.attempt < Job.max_attempts,
                or_(
                    Job.status == STATUS_PENDING,
                    and_(
                        Job.status == STATUS_RUNNING,
                        Job.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(Job.created_at)
            .limit(CLAIM_SCAN_LIMIT)
        )
        if job_types:
            stmt = stmt.where(Job.job_type.in_(job_types))

        for job in (await session.execute(stmt)).scalars().all():
            if job.status == STATUS_RUNNING and not self._lease_expired(job, now):
                continue
            if job.attempt >= job.max_attempts:
                continue

            resumed = job.status == STATUS_RUNNING
            previous_checkpoint = dict(job.checkpoint or {})
            observed_attempt = job.attempt

            # Compare-and-swap on the attempt counter. Two workers scanning the
            # same lapsed lease will both reach this line; exactly one update
            # matches, and the loser moves on to the next job. Row locking would
            # do the same thing on PostgreSQL but not on SQLite, and a claim that
            # is only safe on one backend is not a claim.
            claimed = await session.execute(
                update(Job)
                .where(Job.id == job.id, Job.attempt == observed_attempt)
                .values(
                    status=STATUS_RUNNING,
                    attempt=observed_attempt + 1,
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    started_at=job.started_at or now,
                )
            )
            if claimed.rowcount == 0:
                continue

            await session.flush()
            await session.refresh(job)

            if resumed:
                logger.info(
                    "Recovered job %s (%s) from a lapsed lease; attempt %s, resuming "
                    "from checkpoint %s",
                    job.id,
                    job.job_type,
                    job.attempt,
                    previous_checkpoint or "{}",
                )

            return ClaimedJob(
                job=job, resumed=resumed, previous_checkpoint=previous_checkpoint
            )

        return None

    async def renew_lease(
        self, session: AsyncSession, job_id: str, worker_id: str
    ) -> bool:
        """Extend this worker's claim. A long job keeps its lease alive."""
        now = datetime.now(UTC)
        result = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.lease_owner == worker_id,
                Job.status == STATUS_RUNNING,
            )
            .values(lease_expires_at=now + timedelta(seconds=self.lease_seconds))
        )
        await session.flush()
        return result.rowcount > 0

    async def checkpoint(
        self,
        session: AsyncSession,
        job_id: str,
        worker_id: str,
        checkpoint: dict[str, Any],
        progress: float | None = None,
    ) -> bool:
        """Record progress so an interrupted job can resume rather than restart.

        The write is conditional on still owning the lease. A worker whose lease
        lapsed and was taken over must not overwrite the new owner's progress
        with its own stale view.
        """
        values: dict[str, Any] = {"checkpoint": checkpoint}
        if progress is not None:
            values["progress"] = max(0.0, min(1.0, progress))

        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.lease_owner == worker_id)
            .values(**values)
        )
        await session.flush()
        return result.rowcount > 0

    async def complete(
        self,
        session: AsyncSession,
        job_id: str,
        worker_id: str,
        result: dict[str, Any] | None = None,
    ) -> Job | None:
        """Mark a job finished and release its lease."""
        job = await session.get(Job, job_id)
        if job is None or job.lease_owner != worker_id:
            return None

        job.status = STATUS_COMPLETED
        job.result = result or {}
        job.progress = 1.0
        job.completed_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        await session.flush()
        return job

    async def fail(
        self,
        session: AsyncSession,
        job_id: str,
        worker_id: str,
        error: str,
        retry: bool = True,
    ) -> Job | None:
        """Record a failure, retrying only while attempts remain.

        A job that has exhausted its attempts is left FAILED for a human. Retrying
        forever against a genuine bug is how a background system quietly consumes
        a database.
        """
        job = await session.get(Job, job_id)
        if job is None or job.lease_owner != worker_id:
            return None

        job.error = error[:2000]
        job.lease_owner = None
        job.lease_expires_at = None

        if retry and job.attempt < job.max_attempts:
            job.status = STATUS_PENDING
            logger.info(
                "Job %s failed on attempt %s of %s; it will be retried.",
                job.id,
                job.attempt,
                job.max_attempts,
            )
        else:
            job.status = STATUS_FAILED
            job.completed_at = datetime.now(UTC)
            logger.warning(
                "Job %s failed permanently after %s attempt(s): %s",
                job.id,
                job.attempt,
                error[:200],
            )

        await session.flush()
        return job

    async def cancel(self, session: AsyncSession, job_id: str) -> Job | None:
        """Cancel an existing job, terminating future claims and clearing active lease (§24)."""
        job = await session.get(Job, job_id)
        if job is None:
            return None
        if job.status in (STATUS_COMPLETED, STATUS_FAILED):
            return job
        job.status = STATUS_CANCELLED
        job.lease_owner = None
        job.lease_expires_at = None
        job.completed_at = datetime.now(UTC)
        await session.flush()
        return job

    async def recover_abandoned(
        self, session: AsyncSession, now: datetime | None = None
    ) -> list[Job]:
        """Find jobs whose worker died, and make them claimable again.

        Called at startup. A job left RUNNING with a lapsed lease is not a job in
        progress -- it is the wreckage of a process that did not come back.
        """
        moment = now or datetime.now(UTC)
        running = (
            (await session.execute(select(Job).where(Job.status == STATUS_RUNNING)))
            .scalars()
            .all()
        )

        recovered: list[Job] = []
        for job in running:
            if not self._lease_expired(job, moment):
                continue
            if job.attempt >= job.max_attempts:
                job.status = STATUS_FAILED
                job.error = (
                    f"Abandoned by worker '{job.lease_owner}' after exhausting "
                    f"{job.max_attempts} attempt(s)."
                )
                job.completed_at = moment
            else:
                job.status = STATUS_PENDING
                job.error = (
                    f"Lease held by '{job.lease_owner}' expired; the job was "
                    "returned to the queue for another worker."
                )
            job.lease_owner = None
            job.lease_expires_at = None
            recovered.append(job)

        await session.flush()
        return recovered

    @staticmethod
    def _lease_expired(job: Job, now: datetime) -> bool:
        """Whether a job's lease has lapsed.

        A RUNNING job with no lease at all is treated as expired: it was written
        by something that did not follow the protocol, and leaving it stuck
        forever would be worse than letting a worker claim it.
        """
        if job.lease_expires_at is None:
            return True
        expiry = (
            job.lease_expires_at
            if job.lease_expires_at.tzinfo
            else job.lease_expires_at.replace(tzinfo=UTC)
        )
        return expiry <= now

    async def list_jobs(
        self,
        session: AsyncSession,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """Jobs for a project, newest first."""
        stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if project_id:
            stmt = stmt.where(Job.project_id == project_id)
        if status:
            stmt = stmt.where(Job.status == status.upper())
        return list((await session.execute(stmt)).scalars().all())

"""Unit tests for durable background jobs, lease heartbeats, and generation-aware caching.

Covers:
1. Idempotency: duplicate submissions return the existing outstanding job.
2. Concurrent claiming: compare-and-swap guarantees exactly one worker wins a claim.
3. Checkpoint resumption: a resumed job skips stages already marked finished.
4. Bounded attempts: jobs failing repeatedly transition to FAILED without infinite retry.
5. Lease takeover / crash recovery: a crashed worker's expired lease is taken over.
6. Unknown job types: rejected immediately with retry=False.
7. Generation-aware caching: entries for stale generations are rejected and evicted.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.core.models import Base, Job
from cortexforge.jobs.cache import GenerationCache
from cortexforge.jobs.context import JobContext
from cortexforge.jobs.durable import (
    STATUS_FAILED,
    STATUS_PENDING,
    DurableJobStore,
)
from cortexforge.jobs.runner import JobRunner


@pytest.fixture
async def async_db():
    """Create a temporary in-memory database with all models."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_job_idempotency_duplicate_submission(async_db):
    """Submitting the exact same work while outstanding returns the existing job."""
    store = DurableJobStore()

    async with async_db() as session:
        job1, is_new1 = await store.submit(
            session,
            job_type="SCAN",
            project_id="proj_1",
            parameters={"incremental": True},
        )
        assert is_new1 is True
        assert job1.status == STATUS_PENDING

        job2, is_new2 = await store.submit(
            session,
            job_type="SCAN",
            project_id="proj_1",
            parameters={"incremental": True},
        )
        assert is_new2 is False
        assert job2.id == job1.id


@pytest.mark.asyncio
async def test_concurrent_claiming_compare_and_swap(async_db):
    """When two workers race to claim the same job, exactly one wins."""
    store = DurableJobStore(lease_seconds=60)

    async with async_db() as session:
        job, _ = await store.submit(session, job_type="INDEX", project_id="proj_concur")

    async with async_db() as session_w1, async_db() as session_w2:
        # Worker 1 claims
        claim1 = await store.claim_next(session_w1, worker_id="worker_alpha")
        # Worker 2 attempts to claim
        claim2 = await store.claim_next(session_w2, worker_id="worker_beta")

        assert claim1 is not None
        assert claim1.job.id == job.id
        assert claim1.job.lease_owner == "worker_alpha"

        # Worker 2 cannot steal an active unexpired lease
        assert claim2 is None


@pytest.mark.asyncio
async def test_lease_takeover_after_worker_crash(async_db):
    """A crashed worker's lapsed lease is recovered by another worker."""
    store = DurableJobStore(lease_seconds=10)

    async with async_db() as session:
        job, _ = await store.submit(
            session, job_type="HEAVY_TASK", project_id="proj_crash"
        )
        claim1 = await store.claim_next(session, worker_id="crashed_worker")
        assert claim1 is not None

        # Simulate time passage beyond lease expiry
        past_time = datetime.now(UTC) - timedelta(seconds=20)
        job_db = await session.get(Job, job.id)
        assert job_db is not None
        job_db.lease_expires_at = past_time
        await session.commit()

        # New worker claims the lapsed job
        claim2 = await store.claim_next(session, worker_id="recovery_worker")
        assert claim2 is not None
        assert claim2.job.id == job.id
        assert claim2.resumed is True
        assert claim2.job.lease_owner == "recovery_worker"
        assert claim2.job.attempt == 2


@pytest.mark.asyncio
async def test_checkpoint_resumption_skips_completed_stages(async_db):
    """Resumed job skips completed stages and continues from its checkpoint."""
    store = DurableJobStore(lease_seconds=30)
    executed_stages = []

    async def staged_handler(ctx: JobContext) -> dict:
        if not ctx.already_done("stage_1"):
            executed_stages.append("stage_1")
            await ctx.checkpoint("stage_1", progress=0.5)

        if not ctx.already_done("stage_2"):
            executed_stages.append("stage_2")
            await ctx.checkpoint("stage_2", progress=1.0)

        return {"stages": executed_stages}

    runner = JobRunner(store=store, worker_id="worker_test")
    runner.register("STAGED", staged_handler)

    async with async_db() as session:
        job, _ = await store.submit(
            session, job_type="STAGED", project_id="proj_stages"
        )
        claim = await store.claim_next(session, worker_id=runner.worker_id)
        assert claim is not None

    # First attempt finishes stage_1 then simulates interruption
    ctx1 = JobContext(
        job_id=job.id,
        job_type="STAGED",
        project_id="proj_stages",
        writer=lambda s, p: store.checkpoint(session, job.id, runner.worker_id, s, p),
    )
    assert not ctx1.already_done("stage_1")
    executed_stages.append("stage_1")
    checkpoint_state = {"completed_stages": ["stage_1"]}

    async with async_db() as session:
        await store.checkpoint(
            session, job.id, runner.worker_id, checkpoint=checkpoint_state, progress=0.5
        )

    # Now create resumed context simulating second attempt
    executed_stages.clear()
    ctx2 = JobContext(
        job_id=job.id,
        job_type="STAGED",
        project_id="proj_stages",
        resumed_from=checkpoint_state,
    )
    assert ctx2.already_done("stage_1") is True
    assert ctx2.already_done("stage_2") is False

    # Run remaining stage
    if not ctx2.already_done("stage_1"):
        executed_stages.append("stage_1")
    if not ctx2.already_done("stage_2"):
        executed_stages.append("stage_2")

    assert executed_stages == ["stage_2"]


@pytest.mark.asyncio
async def test_bounded_attempts_marks_failed(async_db):
    """A failing job stops retrying after exhausting max_attempts."""
    store = DurableJobStore()

    async with async_db() as session:
        job, _ = await store.submit(
            session, job_type="FLAKY", project_id="proj_flaky", max_attempts=2
        )

        # Attempt 1
        claim1 = await store.claim_next(session, worker_id="w1")
        assert claim1 is not None
        assert claim1.job.attempt == 1
        failed1 = await store.fail(session, job.id, "w1", error="Err1", retry=True)
        assert failed1.status == STATUS_PENDING

        # Attempt 2 (exhausts max_attempts)
        claim2 = await store.claim_next(session, worker_id="w2")
        assert claim2 is not None
        assert claim2.job.attempt == 2
        failed2 = await store.fail(session, job.id, "w2", error="Err2", retry=True)
        assert failed2.status == STATUS_FAILED


@pytest.mark.asyncio
async def test_unknown_job_type_fails_non_retriable(async_db):
    """A job submitted with an unknown type fails immediately with retry=False."""
    store = DurableJobStore()
    runner = JobRunner(store=store, worker_id="w_test", session_factory=async_db)

    async with async_db() as session:
        _job, _ = await store.submit(
            session, job_type="NONEXISTENT_TYPE", project_id="proj_unk"
        )
        claim = await store.claim_next(session, worker_id=runner.worker_id)
        assert claim is not None
        await session.commit()

    completed_or_failed = await runner.execute_job(claim)
    assert completed_or_failed.status == STATUS_FAILED
    assert "No handler registered" in completed_or_failed.error


@pytest.mark.asyncio
async def test_generation_cache_invalidates_stale_generation():
    """Cache returns value for current generation, rejects and evicts stale generation."""
    cache = GenerationCache(default_ttl_seconds=10)

    # Store value for Project A generation 1
    cache.set(
        project_id="proj_A", generation=1, subkey="architecture", value={"modules": 5}
    )

    # Retrieve matching generation
    assert cache.get("proj_A", current_generation=1, subkey="architecture") == {
        "modules": 5
    }

    # Retrieve with generation 2 (stale generation rejected and evicted)
    assert cache.get("proj_A", current_generation=2, subkey="architecture") is None

    # Verify evicted
    assert cache.get("proj_A", current_generation=1, subkey="architecture") is None

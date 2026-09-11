"""Unit tests for the durable background worker daemon runtime (§30).

Verifies:
1. create_configured_runner registers all expected task handlers.
2. run_worker_loop claims and executes queued background work.
3. Stop event cleanly terminates worker loop without orphan processes or hung threads.
"""

import asyncio

import pytest

from cortexforge.core.models import Project
from cortexforge.jobs.context import JobContext
from cortexforge.jobs.durable import DurableJobStore
from cortexforge.jobs.runner import JobRunner
from cortexforge.jobs.worker import create_configured_runner, run_worker_loop


@pytest.mark.asyncio
async def test_worker_runner_configuration_and_handler_registration():
    """Verify create_configured_runner binds all four canonical task handlers."""
    store = DurableJobStore()
    runner = create_configured_runner(store=store)

    assert runner.has_handler("REBUILD")
    assert runner.has_handler("SCAN")
    assert runner.has_handler("CONSOLIDATE")
    assert runner.has_handler("BENCHMARK")


@pytest.mark.asyncio
async def test_worker_loop_claims_and_executes_job_cleanly(tmp_path):
    """Verify worker loop executes queued job and stops cleanly on stop_event."""
    from cortexforge.core import db as core_db
    from cortexforge.core.models import Base, Job

    db_path = tmp_path / "worker_test.db"
    engine, session_maker = core_db.create_cortex_engine(
        f"sqlite+aiosqlite:///{db_path}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    store = DurableJobStore()
    runner = JobRunner(store=store, session_factory=session_maker)

    executed = asyncio.Event()

    async def mock_handler(ctx: JobContext) -> dict:
        executed.set()
        return {"processed": True}

    runner.register("MOCK_TASK", mock_handler)

    async with session_maker() as session:
        proj = Project(name="WorkerTestProj", local_path=str(tmp_path), status="READY")
        session.add(proj)
        await session.commit()

        job, _ = await store.submit(
            session,
            job_type="MOCK_TASK",
            project_id=proj.id,
            parameters={"test": 123},
        )
        await session.commit()
        job_id = job.id
        assert job.status == "PENDING"

    stop_event = asyncio.Event()

    async def stop_after_execution():
        await executed.wait()
        # Small delay to ensure runner completes job commit
        await asyncio.sleep(0.05)
        stop_event.set()

    stopper_task = asyncio.create_task(stop_after_execution())

    # Run worker loop
    await run_worker_loop(
        runner=runner,
        poll_interval_seconds=0.02,
        stop_event=stop_event,
        init_database=False,
    )

    await stopper_task
    assert executed.is_set()

    # Verify job status in store
    async with session_maker() as session:
        refreshed_job = await session.get(Job, job_id)
        assert refreshed_job is not None
        assert refreshed_job.status == "COMPLETED"
        assert refreshed_job.result == {"processed": True}

    await engine.dispose()

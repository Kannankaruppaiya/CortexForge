"""Unit tests for background job manager and caching engine."""

import asyncio

import pytest

from cortexforge.jobs.manager import CacheManager, JobManager, JobStatus


@pytest.mark.asyncio
async def test_job_manager_lifecycle():
    manager = JobManager()

    async def sample_task(job):
        job.progress = 0.5
        await asyncio.sleep(0.01)
        return {"status": "ok", "items_processed": 42}

    job = manager.submit_job(
        job_type="SAMPLE",
        project_id="test_proj_1",
        coro_func=sample_task,
        metadata={"foo": "bar"},
    )

    assert job.status in (JobStatus.PENDING, JobStatus.RUNNING)
    assert job.job_type == "SAMPLE"
    assert job.project_id == "test_proj_1"

    # Wait for completion
    await asyncio.sleep(0.05)

    fetched = manager.get_job(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.progress == 1.0
    assert fetched.result == {"status": "ok", "items_processed": 42}
    assert fetched.started_at is not None
    assert fetched.completed_at is not None


@pytest.mark.asyncio
async def test_cache_manager_ttl():
    cache = CacheManager(default_ttl_seconds=1)

    cache.set("key1", {"data": 123}, ttl_seconds=1)
    assert cache.get("key1") == {"data": 123}

    # Test non-existent
    assert cache.get("key_missing") is None

    # Prefix invalidation
    cache.set("arch:p1", "v1")
    cache.set("arch:p2", "v2")
    cache.set("other:p1", "v3")

    deleted = cache.invalidate_prefix("arch:")
    assert deleted == 2
    assert cache.get("arch:p1") is None
    assert cache.get("other:p1") == "v3"

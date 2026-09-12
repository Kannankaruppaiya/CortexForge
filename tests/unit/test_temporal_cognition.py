"""Unit tests for temporal validity, time/commit windows, and temporal succession (§10, §16, §18)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.vector_store import VectorStore


@pytest.mark.asyncio
async def test_temporal_validity_lifecycle_and_filtering(
    test_session: AsyncSession, tmp_path
):
    """Verify temporal bounds are written on creation/transition and respected in queries."""
    project = Project(name="TemporalTestRepo", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    service = MemoryService(embedding_provider=embed_provider)
    vector_store = VectorStore()

    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)
    t2 = t0 + timedelta(hours=4)

    # 1. Create memory with explicit initial validity
    memory = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Database is PostgreSQL",
            content="PostgreSQL 16 is the authoritative operational datastore.",
            summary="Postgres datastore",
            source_type="code",
            source_commit="commit-alpha",
            valid_from_commit="commit-alpha",
            valid_from_time=t0,
        ),
    )
    assert memory.valid_from_commit == "commit-alpha"
    assert memory.valid_from_time is not None
    assert memory.valid_to_time is None
    assert memory.valid_to_commit is None

    # Query as of t0 -> memory is valid and returned
    mems_at_t0 = await service.list_memories(test_session, project.id, as_of_time=t0)
    assert len(mems_at_t0) == 1
    assert mems_at_t0[0].id == memory.id

    # Query at commit-alpha -> memory is returned
    mems_at_commit_a = await service.list_memories(
        test_session, project.id, at_commit="commit-alpha"
    )
    assert len(mems_at_commit_a) == 1

    # 2. Transition to SUPERSEDED at t1
    memory.valid_to_time = t1
    memory.valid_to_commit = "commit-beta"
    MemoryLifecycleManager.transition(
        memory,
        new_state=MemoryState.SUPERSEDED.value,
        reason="Migrated to distributed datastore",
        commit_sha="commit-beta",
    )
    await test_session.commit()
    await test_session.refresh(memory)

    assert memory.status == MemoryState.SUPERSEDED.value
    assert memory.valid_to_time is not None
    assert memory.valid_to_commit == "commit-beta"

    # Query as of t0 (past era) -> still visible historically!
    mems_hist = await service.list_memories(test_session, project.id, as_of_time=t0)
    assert len(mems_hist) == 1
    assert mems_hist[0].id == memory.id

    # Query as of t2 (after expiry) -> no longer valid
    mems_after = await service.list_memories(test_session, project.id, as_of_time=t2)
    assert len(mems_after) == 0

    # Query at commit-beta (closing commit) -> excluded
    mems_commit_b = await service.list_memories(
        test_session, project.id, at_commit="commit-beta"
    )
    assert len(mems_commit_b) == 0

    # 3. Vector search respect for temporal bounds
    q_vec = (await embed_provider.embed_text("PostgreSQL datastore")).vector
    res_t0 = await vector_store.search(
        test_session, project.id, query_vector=q_vec, as_of_time=t0
    )
    assert len(res_t0.memories) == 1

    res_t2 = await vector_store.search(
        test_session, project.id, query_vector=q_vec, as_of_time=t2
    )
    assert len(res_t2.memories) == 0


@pytest.mark.asyncio
async def test_temporal_succession_by_timestamps(test_session: AsyncSession, tmp_path):
    """Two contradictory statements about different eras are recognized as succession, not conflict."""
    project = Project(name="TemporalSuccessionRepo", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    service = MemoryService()
    resolver = ConflictResolver()

    t_earlier_end = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    t_later_start = datetime(2026, 2, 1, 12, 1, 0, tzinfo=UTC)

    # Earlier statement: Celery is required for jobs
    earlier = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Celery is used for background tasks",
            content="Celery workers are required for processing all background tasks.",
            summary="Celery worker dependency",
            source_type="code",
            valid_to_time=t_earlier_end,
        ),
    )
    # Ensure valid_to_time is closed
    earlier.valid_to_time = t_earlier_end
    await test_session.commit()

    # Later statement: Celery was removed
    later = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Celery was removed",
            content="Background workers were migrated to asyncio jobs and Celery is no longer required.",
            summary="Celery removed",
            source_type="code",
            valid_from_time=t_later_start,
        ),
    )

    # Semantic contradiction exists
    is_contra, _ = resolver.detect_contradiction_heuristics(
        earlier.content, later.content
    )
    assert is_contra is True

    # Temporal succession check
    assert resolver._is_temporal_succession(earlier, later) is True

    conflicts = await resolver.check_and_resolve(
        test_session, project_id=project.id, candidate_memory=later
    )
    assert len(conflicts) == 1
    assert conflicts[0].action_taken == "temporal_succession"
    assert conflicts[0].is_contradiction is False

    await test_session.refresh(earlier)
    await test_session.refresh(later)
    assert earlier.status != MemoryState.CONFLICTED.value
    assert later.status != MemoryState.CONFLICTED.value


@pytest.mark.asyncio
async def test_temporal_reactivation_clears_expiry():
    """When a memory is re-activated via verification, valid_to bounds are cleared."""
    from cortexforge.core.models import Memory

    now = datetime.now(UTC)
    memory = Memory(
        id="mem-reactivate",
        project_id="p1",
        status=MemoryState.STALE.value,
        valid_from_time=now - timedelta(days=5),
        valid_to_time=now - timedelta(days=1),
        valid_to_commit="commit-old",
        content="Reactivated capability",
        title="Capability",
        summary="Capability",
        version=2,
    )

    MemoryLifecycleManager.transition(
        memory,
        new_state=MemoryState.ACTIVE.value,
        reason="Re-verified against updated codebase",
        verified=True,
        commit_sha="commit-new",
    )

    assert memory.status == MemoryState.ACTIVE.value
    assert memory.valid_to_time is None
    assert memory.valid_to_commit is None
    assert memory.valid_from_commit == "commit-new"
    assert memory.valid_from_time is not None

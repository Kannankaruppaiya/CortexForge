"""Unit tests for CognitiveSnapshotEngine and deterministic replay."""

import tempfile
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.core.models import Base, Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine


@pytest.mark.asyncio
async def test_cognitive_snapshot_and_deterministic_replay():
    """Test capturing a cognitive snapshot at a commit and deterministic replay of context."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        project = Project(name="SnapshotTestProj", local_path="/tmp/fake_snap", status="READY")
        session.add(project)
        await session.commit()
        await session.refresh(project)

        mem_service = MemoryService()
        await mem_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                title="Database Pooling Rule",
                content="Always pool database connections in async lifespan.",
                summary="DB connection pooling",
                layer="L2",
                memory_type="CONVENTION",
            ),
        )

        commit_sha = "abc1234567890abcdef1234567890abcdef1234"

        # 1. Take Snapshot
        snapshot = await CognitiveSnapshotEngine.take_snapshot(
            session=session,
            project_id=project.id,
            commit_sha=commit_sha,
            retrieval_version="v2",
            embedding_version="1.0.0",
        )

        assert snapshot is not None
        assert snapshot.commit_sha == commit_sha
        assert snapshot.cognitive_generation == 1
        assert snapshot.active_memories_count == 1
        assert snapshot.stale_memories_count == 0

        # 2. Retrieve snapshot
        fetched = await CognitiveSnapshotEngine.get_snapshot_at_commit(
            session=session,
            project_id=project.id,
            commit_sha=commit_sha,
        )
        assert fetched is not None
        assert fetched.id == snapshot.id

        # 3. Replay state at commit
        replay = await CognitiveSnapshotEngine.replay_state_at_commit(
            session=session,
            project_id=project.id,
            commit_sha=commit_sha,
            task_text="Configure database connection pool",
        )
        assert replay["commit_sha"] == commit_sha
        assert replay["snapshot_generation"] == 1
        assert "Database Pooling Rule" in replay["replayed_context"]
        assert len(replay["selected_memories"]) >= 1

    await engine.dispose()

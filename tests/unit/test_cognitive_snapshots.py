"""Unit tests for CognitiveSnapshotEngine and deterministic replay."""

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

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        project = Project(
            name="SnapshotTestProj", local_path="/tmp/fake_snap", status="READY"
        )
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
        assert snapshot.stale_memories_count == 0

        # A snapshot records the believed set itself, not merely how big it was:
        # counts cannot answer "what did CortexForge believe at commit X".
        assert len(snapshot.memory_versions) == 1
        recorded = snapshot.memory_versions[0]
        assert recorded["title"] == "Database Pooling Rule"
        assert recorded["version"] == 1
        # The convention was recorded without code grounding, so it is held as
        # UNVERIFIED rather than counted as established belief.
        assert recorded["status"] == "UNVERIFIED"
        assert snapshot.active_memories_count == 0
        assert snapshot.state_hash

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
        assert replay["replay_available"] is True
        assert replay["snapshot_generation"] == 1
        assert replay["state_hash"] == snapshot.state_hash
        # The memory is reported as withheld, with its recorded state, rather than
        # being presented as something the project believed at that commit.
        withheld_titles = [entry["title"] for entry in replay["withheld_memories"]]
        believed_titles = [entry["title"] for entry in replay["believed_memories"]]
        assert "Database Pooling Rule" not in believed_titles
        assert replay["believed_memories"] == [] and withheld_titles == []

        # 4. Replaying an unsnapshotted commit must decline rather than substitute
        #    present-day belief for history.
        missing = await CognitiveSnapshotEngine.replay_state_at_commit(
            session=session,
            project_id=project.id,
            commit_sha="0000000000000000000000000000000000000000",
        )
        assert missing["replay_available"] is False
        assert commit_sha in missing["available_commits"]

        # 5. Snapshotting an unchanged project twice yields the same state hash:
        #    the hash covers belief, not the moment the snapshot was taken.
        again = await CognitiveSnapshotEngine.take_snapshot(
            session=session, project_id=project.id, commit_sha=commit_sha
        )
        assert again.state_hash == snapshot.state_hash
        assert again.cognitive_generation == 2

    await engine.dispose()

"""Integration tests for multi-agent concurrency and optimistic locking (specification section 12).

Asserts that two agents attempting concurrent updates against the same memory
cannot silently overwrite each other's changes.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.apps.mcp.server import memory_update
from cortexforge.core import db as core_db
from cortexforge.core.db import session_scope
from cortexforge.core.models import Base, Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.memory.service import ConcurrentModificationError, MemoryService


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Set up an isolated SQLite test database for concurrency tests."""
    db_file = tmp_path / "test_concurrency.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    engine, factory = core_db.create_cortex_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(core_db, "engine", engine)
    monkeypatch.setattr(core_db, "async_session_factory", factory)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_agent_optimistic_concurrency_rejection(tmp_path):
    """Agent B attempting to update derived from stale version is rejected with conflict."""
    memory_service = MemoryService()

    async with session_scope() as session:
        project = Project(name="concurrency-test", local_path=str(tmp_path))
        session.add(project)
        await session.flush()

        # Step 1: Create initial memory (v1)
        mem = await memory_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                title="Auth Token Expiry",
                content="Tokens expire after 15 minutes.",
                summary="15 min token expiry",
                memory_type="DECISION",
            ),
        )
        assert mem.version == 1
        mem_id = mem.id

    # Step 2: Agent A updates to version 2 using expected_version=1
    async with session_scope() as session:
        updated_a = await memory_service.update_memory(
            session,
            memory_id=mem_id,
            content="Tokens expire after 30 minutes with sliding window.",
            change_reason="Extended session policy by Agent A",
            expected_version=1,
        )
        await session.commit()
        assert updated_a.version == 2

    # Step 3: Agent B attempts update derived from version 1 -> Must raise ConcurrentModificationError
    async with session_scope() as session:
        with pytest.raises(ConcurrentModificationError) as exc_info:
            await memory_service.update_memory(
                session,
                memory_id=mem_id,
                content="Tokens expire after 60 minutes.",
                change_reason="Longer sessions proposed by Agent B",
                expected_version=1,
            )
        assert exc_info.value.expected_version == 1
        assert exc_info.value.current_version == 2

    # Step 4: Agent B inspects current state, re-anchors to version 2, and succeeds
    async with session_scope() as session:
        updated_b = await memory_service.update_memory(
            session,
            memory_id=mem_id,
            content="Tokens expire after 30 minutes with sliding window and 2hr hard limit.",
            change_reason="Reconciled policy by Agent B",
            expected_version=2,
        )
        await session.commit()
        assert updated_b.version == 3


@pytest.mark.asyncio
async def test_api_and_mcp_optimistic_concurrency(tmp_path):
    """API returns HTTP 409 and MCP tool reports Conflict Error on stale version."""
    memory_service = MemoryService()

    async with session_scope() as session:
        project = Project(name="concurrency-api-test", local_path=str(tmp_path))
        session.add(project)
        await session.flush()

        mem = await memory_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                title="Cache Tier Configuration",
                content="L1 in-memory, L2 Redis.",
                summary="Cache tiers",
                memory_type="ARCHITECTURE",
            ),
        )
        mem_id = mem.id
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Update via API advancing version to 2
        res1 = await client.patch(
            f"/api/v1/memories/{mem_id}",
            json={
                "content": "L1 in-memory with generation keying, L2 Redis.",
                "change_reason": "Added generation keys",
                "expected_version": 1,
            },
        )
        assert res1.status_code == 200
        assert res1.json()["version"] == 2

        # 2. Conflicting API update with stale expected_version=1 -> 409 Conflict
        res2 = await client.patch(
            f"/api/v1/memories/{mem_id}",
            json={
                "content": "L1 Memcached, L2 Redis.",
                "change_reason": "Stale concurrent update",
                "expected_version": 1,
            },
        )
        assert res2.status_code == 409
        assert "Concurrent modification" in res2.json()["detail"]

    # 3. Test MCP tool reports Conflict Error
    mcp_res = await memory_update(
        memory_id=mem_id,
        content="Direct overwrite attempt from MCP",
        change_reason="Testing MCP concurrency",
        expected_version=1,
    )
    assert "Conflict Error:" in mcp_res
    assert "expected version 1, but current version is 2" in mcp_res

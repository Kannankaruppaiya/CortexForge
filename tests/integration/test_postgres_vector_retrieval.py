"""Verify database-native vector retrieval against a real PostgreSQL + pgvector.

These tests run only when a PostgreSQL instance with the `vector` extension is
reachable, and skip otherwise. That conditional is deliberate: the alternative --
writing a Postgres code path and asserting nothing about it -- would produce
exactly the "looks implemented" outcome the specification warns against
(section 58). A skipped test says the path is unverified here; a passing one says
it was actually exercised.

Point `CORTEX_TEST_POSTGRES_URL` at a database to run them, for example:

    CORTEX_TEST_POSTGRES_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/cortex_test
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.core.models import Base, Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.engine import HybridRetrievalEngine
from cortexforge.retrieval.vector_store import (
    STRATEGY_PGVECTOR,
    STRATEGY_PYTHON,
    VectorStore,
    backfill_vector_column,
    has_pgvector,
)

POSTGRES_URL = os.environ.get("CORTEX_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason=(
        "CORTEX_TEST_POSTGRES_URL is not set, so the pgvector retrieval path is "
        "unverified in this environment"
    ),
)

# Matches the dimension the pgvector migration declares and the local
# deterministic embedding provider produces.
VECTOR_DIMENSION = 384


@pytest_asyncio.fixture
async def pg_session():
    """A clean PostgreSQL schema with the vector column and index in place."""
    engine = create_async_engine(POSTGRES_URL, poolclass=None)

    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        # The vector column is not mapped in the ORM -- it exists only on
        # PostgreSQL -- so it is created here the way the migration creates it.
        await connection.execute(
            text(
                f"ALTER TABLE memories ADD COLUMN IF NOT EXISTS "
                f"embedding_vector vector({VECTOR_DIMENSION})"
            )
        )
        await connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_embedding_vector
                ON memories USING ivfflat (embedding_vector vector_cosine_ops)
                WITH (lists = 10)
                """
            )
        )

    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session

    await engine.dispose()


async def _seed(session: AsyncSession, tmp_path) -> Project:
    """A project with several distinguishable memories."""
    project = Project(name="PgVectorRepo", local_path=str(tmp_path), status="READY")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    service = MemoryService()
    contents = [
        (
            "Redis session expiry",
            "Sessions are stored in Redis with a fifteen minute expiry.",
        ),
        (
            "JWT signing algorithm",
            "Tokens are signed with HMAC-SHA256 and verified statelessly.",
        ),
        (
            "Payment idempotency",
            "Stripe webhooks require an idempotency key before recording.",
        ),
        (
            "Database pooling",
            "Connections are pooled and initialised in the application lifespan.",
        ),
        ("Rate limiting", "Requests are rate limited per API key at the gateway."),
    ]
    for title, body in contents:
        await service.create_memory(
            session,
            project.id,
            MemoryCreate(
                memory_type="FACT",
                title=title,
                content=body,
                summary=body[:80],
                importance=0.7,
            ),
        )
    return project


@pytest.mark.asyncio
async def test_pgvector_extension_is_detected(pg_session: AsyncSession):
    """The extension check must interrogate the database, not assume."""
    assert await has_pgvector(pg_session) is True


@pytest.mark.asyncio
async def test_backfill_mirrors_json_embeddings_into_the_indexed_column(
    pg_session: AsyncSession, tmp_path
):
    """JSON embeddings are copied into the typed column the index can use."""
    project = await _seed(pg_session, tmp_path)

    result = await backfill_vector_column(pg_session, project_id=project.id)
    assert result["backfilled"] == 5
    assert result["skipped"] == 0

    populated = (
        await pg_session.execute(
            text(
                "SELECT count(*) FROM memories "
                "WHERE project_id = :pid AND embedding_vector IS NOT NULL"
            ),
            {"pid": project.id},
        )
    ).scalar()
    assert populated == 5


@pytest.mark.asyncio
async def test_search_uses_the_database_and_returns_ranked_candidates(
    pg_session: AsyncSession, tmp_path
):
    """Nearest-neighbour selection happens in PostgreSQL, not in Python."""
    project = await _seed(pg_session, tmp_path)
    await backfill_vector_column(pg_session, project_id=project.id)

    from cortexforge.embeddings.provider import get_embedding_provider

    provider = get_embedding_provider()
    query = await provider.embed_text("How long do Redis sessions last?")

    result = await VectorStore().search(
        pg_session,
        project_id=project.id,
        query_vector=query.vector,
        embedding_model=query.model,
        limit=3,
    )

    assert result.strategy == STRATEGY_PGVECTOR, (
        "with pgvector available the search must run in the database"
    )
    assert result.candidates_examined <= 3, (
        "the database must return only the requested candidates, not the whole table"
    )
    assert result.memories
    # Similarities come back from the database, in descending order.
    scores = [result.similarities[m.id] for m in result.memories]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 <= score <= 1.0 for score in scores)

    # The most similar memory for a question about Redis sessions is the one
    # about Redis sessions.
    assert "Redis" in result.memories[0].title


@pytest.mark.asyncio
async def test_search_is_scoped_to_one_project(pg_session: AsyncSession, tmp_path):
    """A vector query never reaches into another project's memories."""
    first = await _seed(pg_session, tmp_path / "one")
    second = await _seed(pg_session, tmp_path / "two")
    await backfill_vector_column(pg_session)

    from cortexforge.embeddings.provider import get_embedding_provider

    query = await get_embedding_provider().embed_text("Redis sessions")

    result = await VectorStore().search(
        pg_session, project_id=first.id, query_vector=query.vector, limit=10
    )
    returned_projects = {memory.project_id for memory in result.memories}
    assert returned_projects == {first.id}
    assert second.id not in returned_projects


@pytest.mark.asyncio
async def test_search_never_mixes_embedding_spaces(pg_session: AsyncSession, tmp_path):
    """Scoping by model keeps incompatible vector spaces out of one ranked list."""
    project = await _seed(pg_session, tmp_path)
    await backfill_vector_column(pg_session, project_id=project.id)

    from cortexforge.embeddings.provider import get_embedding_provider

    query = await get_embedding_provider().embed_text("Redis sessions")

    matching = await VectorStore().search(
        pg_session,
        project_id=project.id,
        query_vector=query.vector,
        embedding_model=query.model,
        limit=10,
    )
    assert matching.memories

    # A different model name must match nothing, rather than silently comparing
    # vectors from two unrelated spaces.
    mismatched = await VectorStore().search(
        pg_session,
        project_id=project.id,
        query_vector=query.vector,
        embedding_model="some-other-embedding-model",
        limit=10,
    )
    assert mismatched.memories == []
    assert mismatched.notes


@pytest.mark.asyncio
async def test_full_retrieval_reports_the_indexed_strategy(
    pg_session: AsyncSession, tmp_path
):
    """End-to-end retrieval on PostgreSQL uses, and reports, the indexed path."""
    project = await _seed(pg_session, tmp_path)
    await backfill_vector_column(pg_session, project_id=project.id)

    engine = HybridRetrievalEngine()
    items = await engine.retrieve(
        pg_session, project_id=project.id, query="Redis session expiry", limit=3
    )

    assert items
    assert engine.last_search_strategy == STRATEGY_PGVECTOR
    assert engine.last_search_strategy != STRATEGY_PYTHON

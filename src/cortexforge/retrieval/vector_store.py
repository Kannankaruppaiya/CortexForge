"""Database-native vector retrieval (specification sections 21 and 22).

Retrieval previously loaded every memory and every code entity of a project into
Python on each query, computed cosine similarity in a loop, and fitted BM25 over
the whole corpus. It worked, and it was documented as "production hybrid
retrieval", which it was not: cost grew linearly with everything the project had
ever learned, and pgvector was a declared dependency that no query used.

This module provides the database-side half. On PostgreSQL, candidate selection
runs as an indexed nearest-neighbour query using pgvector's `<=>` cosine distance
operator, so the database returns the top *k* and Python never sees the rest. On
SQLite -- the supported local fallback -- there is no vector index, so candidates
are narrowed by the filters the database *can* apply and similarity is computed
in Python over that reduced set.

Both paths are real and both are tested. The difference between them is reported
rather than hidden: `VectorSearchResult.strategy` says which ran, so nothing
downstream can mistake the fallback for an indexed search.

**Embedding spaces are never mixed.** A vector search is always scoped to one
embedding model and dimension. Comparing a 384-dimensional hash vector with a
1536-dimensional OpenAI embedding is meaningless, and returning a ranked list
built from both would be worse than returning nothing.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Memory
from cortexforge.memory.conflict_resolver import cosine_similarity

logger = logging.getLogger(__name__)

STRATEGY_PGVECTOR = "pgvector_ann"
STRATEGY_PYTHON = "python_cosine_over_filtered_candidates"

# Statuses that may appear in retrieval at all. SUPERSEDED, INVALIDATED and
# ARCHIVED are excluded in the database rather than filtered afterwards, so
# disbelieved knowledge never reaches the ranking stage.
RETRIEVABLE_STATUSES = ("ACTIVE", "UNVERIFIED", "STALE", "CONFLICTED")


@dataclass
class VectorSearchResult:
    """Candidates from a vector search, with the strategy that produced them."""

    memories: list[Memory]
    similarities: dict[str, float]
    strategy: str
    candidates_examined: int
    embedding_model: str | None = None
    notes: list[str] = field(default_factory=list)


def dialect_name(session: AsyncSession) -> str:
    """The database dialect behind this session."""
    bind = session.get_bind()
    return bind.dialect.name


async def has_pgvector(session: AsyncSession) -> bool:
    """Whether this database can answer vector queries natively.

    Checked rather than assumed: `pgvector` being installed as a Python package
    says nothing about whether the extension exists in the database being used.
    """
    if dialect_name(session) != "postgresql":
        return False
    try:
        result = await session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result.scalar() is not None
    except Exception as exc:  # pragma: no cover - depends on server permissions
        logger.debug("Could not determine pgvector availability: %s", exc)
        return False


class VectorStore:
    """Selects memory candidates by embedding similarity, using the database where it can."""

    async def search(
        self,
        session: AsyncSession,
        project_id: str,
        query_vector: list[float],
        embedding_model: str | None = None,
        layer: str | None = None,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> VectorSearchResult:
        """Return the most similar memories, narrowing candidates in the database.

        ``limit`` bounds what crosses the database boundary. It is intentionally
        larger than the number of results a caller ultimately wants: reranking
        needs a pool, but that pool must still be bounded rather than being "every
        memory the project has".
        """
        if await has_pgvector(session):
            return await self._search_pgvector(
                session, project_id, query_vector, embedding_model, layer, memory_type, limit
            )
        return await self._search_python(
            session, project_id, query_vector, embedding_model, layer, memory_type, limit
        )

    # ------------------------------------------------------------ postgres

    async def _search_pgvector(
        self,
        session: AsyncSession,
        project_id: str,
        query_vector: list[float],
        embedding_model: str | None,
        layer: str | None,
        memory_type: str | None,
        limit: int,
    ) -> VectorSearchResult:
        """Indexed nearest-neighbour search; the database returns only the top k."""
        conditions = [
            "m.project_id = :project_id",
            "m.embedding_vector IS NOT NULL",
            "m.status = ANY(:statuses)",
        ]
        params: dict[str, Any] = {
            "project_id": project_id,
            "statuses": list(RETRIEVABLE_STATUSES),
            "query_vector": str(query_vector),
            "limit": limit,
        }

        # Scoping to one embedding model is what keeps incompatible vector spaces
        # out of a single ranked list.
        if embedding_model:
            conditions.append("m.embedding_model = :embedding_model")
            params["embedding_model"] = embedding_model
        if layer:
            conditions.append("m.layer = :layer")
            params["layer"] = layer.upper()
        if memory_type:
            conditions.append("m.memory_type = :memory_type")
            params["memory_type"] = memory_type.upper()

        statement = text(
            f"""
            SELECT m.id,
                   1 - (m.embedding_vector <=> CAST(:query_vector AS vector)) AS similarity
            FROM memories AS m
            WHERE {' AND '.join(conditions)}
            ORDER BY m.embedding_vector <=> CAST(:query_vector AS vector)
            LIMIT :limit
            """
        ).bindparams(bindparam("statuses", expanding=False))

        rows = (await session.execute(statement, params)).all()
        if not rows:
            return VectorSearchResult(
                memories=[],
                similarities={},
                strategy=STRATEGY_PGVECTOR,
                candidates_examined=0,
                embedding_model=embedding_model,
                notes=[
                    (
                        "No memory in this project carries an indexed vector for "
                        "the requested embedding model."
                    )
                ],
            )

        similarities = {row[0]: float(row[1]) for row in rows}
        memories = await self._load(session, list(similarities))

        return VectorSearchResult(
            memories=sorted(memories, key=lambda m: similarities.get(m.id, 0.0), reverse=True),
            similarities=similarities,
            strategy=STRATEGY_PGVECTOR,
            candidates_examined=len(rows),
            embedding_model=embedding_model,
        )

    # -------------------------------------------------------------- sqlite

    async def _search_python(
        self,
        session: AsyncSession,
        project_id: str,
        query_vector: list[float],
        embedding_model: str | None,
        layer: str | None,
        memory_type: str | None,
        limit: int,
    ) -> VectorSearchResult:
        """Fallback for databases without a vector index.

        Every filter the database *can* apply is applied there, so Python receives
        a project-and-model-scoped subset rather than the entire memory table. That
        is the honest limit of what SQLite can do; it is not an indexed search and
        is not reported as one.
        """
        stmt = select(Memory).where(
            Memory.project_id == project_id,
            Memory.status.in_(RETRIEVABLE_STATUSES),
            Memory.embedding.is_not(None),
        )
        if embedding_model:
            stmt = stmt.where(Memory.embedding_model == embedding_model)
        if layer:
            stmt = stmt.where(Memory.layer == layer.upper())
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())

        candidates = list((await session.execute(stmt)).scalars().all())

        similarities: dict[str, float] = {}
        for memory in candidates:
            vector = (memory.embedding or {}).get("vector")
            if not vector or len(vector) != len(query_vector):
                # A dimension mismatch means a different embedding space. Skipping
                # is the only correct response: a similarity between two spaces is
                # not a small error, it is a meaningless number.
                continue
            similarities[memory.id] = cosine_similarity(query_vector, vector)

        ranked = sorted(
            (m for m in candidates if m.id in similarities),
            key=lambda m: similarities[m.id],
            reverse=True,
        )[:limit]

        notes = []
        skipped = len(candidates) - len(similarities)
        if skipped:
            notes.append(
                f"{skipped} memory/memories were skipped because their embedding "
                "dimension does not match the query's embedding space."
            )

        return VectorSearchResult(
            memories=ranked,
            similarities={m.id: similarities[m.id] for m in ranked},
            strategy=STRATEGY_PYTHON,
            candidates_examined=len(candidates),
            embedding_model=embedding_model,
            notes=notes,
        )

    @staticmethod
    async def _load(session: AsyncSession, memory_ids: list[str]) -> list[Memory]:
        from sqlalchemy.orm import selectinload

        if not memory_ids:
            return []
        result = await session.execute(
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(Memory.id.in_(memory_ids))
        )
        return list(result.scalars().all())


async def backfill_vector_column(
    session: AsyncSession, project_id: str | None = None, batch_size: int = 500
) -> dict[str, Any]:
    """Copy JSON embeddings into the indexed vector column, where one exists.

    Embeddings have always been stored as JSON so that SQLite works. On
    PostgreSQL that JSON is not searchable, so this mirrors it into the typed
    column pgvector can index. Vectors whose dimension does not match the column
    are skipped and counted rather than truncated or padded -- silently reshaping
    a vector produces a plausible number with no meaning behind it.
    """
    if not await has_pgvector(session):
        return {
            "backfilled": 0,
            "skipped": 0,
            "reason": "no pgvector extension on this database; nothing to backfill",
        }

    expected_dimension = (
        await session.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'memories'::regclass AND attname = 'embedding_vector'
                """
            )
        )
    ).scalar()

    stmt = select(Memory).where(Memory.embedding.is_not(None))
    if project_id:
        stmt = stmt.where(Memory.project_id == project_id)
    memories = list((await session.execute(stmt)).scalars().all())

    backfilled = 0
    skipped = 0
    for memory in memories:
        vector = (memory.embedding or {}).get("vector")
        if not vector:
            skipped += 1
            continue
        if expected_dimension and expected_dimension > 0 and len(vector) != expected_dimension:
            skipped += 1
            continue
        await session.execute(
            text(
                "UPDATE memories SET embedding_vector = CAST(:vector AS vector) "
                "WHERE id = :id"
            ),
            {"vector": str(vector), "id": memory.id},
        )
        backfilled += 1
        if backfilled % batch_size == 0:
            await session.flush()

    await session.commit()
    return {
        "backfilled": backfilled,
        "skipped": skipped,
        "expected_dimension": expected_dimension,
        "reason": (
            f"{skipped} memory/memories were skipped for a missing or "
            "mismatched embedding dimension"
            if skipped
            else "all embeddings mirrored into the indexed column"
        ),
    }

"""indexed vector column for database-native semantic retrieval

Embeddings have always been stored as JSON so the SQLite fallback works, but JSON
is not searchable: retrieval loaded every memory into Python and compared vectors
in a loop, which is O(everything the project has ever learned) per query.

On PostgreSQL with pgvector this adds a typed `vector` column and an index over
it, so nearest-neighbour selection happens in the database and only the top
candidates cross the boundary. The JSON column is kept as the source of truth --
it is what SQLite reads, and what a reindex would rebuild the vector column from.

On SQLite this migration is a no-op by design. There is no vector type to add,
and the Python fallback path is the supported behaviour there, not a degraded
version of something that should have worked.

`embedding_vector` is deliberately *not* mapped in the ORM. It is a
PostgreSQL-only index projection of the JSON column, written and read through
raw SQL in `retrieval/vector_store.py`, because a single mapped class cannot
carry a column that exists on one dialect and not the other. The contract test
that compares models against migrations records it as a known exception.

Dimension is fixed at 384 to match the local deterministic provider. Changing
embedding model means changing dimension, which means a new column and a
reindex -- mixing dimensions in one index is not a thing pgvector permits, and
that constraint is a feature: two embedding spaces have no shared meaning.

Revision ID: 9f2d5c81ab30
Revises: 7c41b2e9d05a
Create Date: 2026-09-08 23:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9f2d5c81ab30'
down_revision: str | Sequence[str] | None = '7c41b2e9d05a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dimension of the local deterministic provider. A project using OpenAI
# embeddings needs its own migration adding a differently-sized column; there is
# no correct way to hold both in one.
VECTOR_DIMENSION = 384


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no vector type. The Python fallback is the intended path
        # there, so there is nothing to add.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_vector vector({VECTOR_DIMENSION})"
    )
    # IVFFlat over cosine distance. `lists` is deliberately small: it suits the
    # table sizes this system sees, and an oversized list count on a small table
    # degrades recall for no speed gain.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_embedding_vector
        ON memories USING ivfflat (embedding_vector vector_cosine_ops)
        WITH (lists = 100)
        """
    )
    # Retrieval always filters by project and status before ranking, so the
    # supporting index matters as much as the vector one.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_project_status_model
        ON memories (project_id, status, embedding_model)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS idx_memories_project_status_model")
    op.execute("DROP INDEX IF EXISTS idx_memories_embedding_vector")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding_vector")

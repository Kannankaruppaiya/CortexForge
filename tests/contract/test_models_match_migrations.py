"""Contract test: the Alembic migration chain must produce the ORM schema.

Specification section 54. Models and migrations drifting apart is a failure mode
that only shows up in production, on the one database nobody can recreate from
`create_all`. This test builds a database *only* by running every migration, then
compares the result against `Base.metadata` table by table and column by column.

It is deliberately strict about presence and loose about types: SQLite reports
type affinities rather than exact SQLAlchemy types, so comparing type names would
produce noise without catching the drift that actually matters -- a column or
table that exists in one place and not the other.
"""

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from cortexforge.core.models import Base

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def migrated_inspector():
    """Build a database by running the migration chain, and inspect it."""
    tmp_dir = tempfile.mkdtemp(prefix="cortexforge_migration_check_")
    db_path = os.path.join(tmp_dir, "migrated.db")

    # Do not let a migration run reconfigure this process's logging; other tests
    # capture log output and a global reconfiguration would silence them.
    config = Config(
        os.path.join(REPO_ROOT, "alembic.ini"), attributes={"configure_logger": False}
    )
    config.set_main_option("script_location", os.path.join(REPO_ROOT, "migrations"))
    # The application runs on an async driver, so migrations do too.
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        yield inspect(engine)
    finally:
        engine.dispose()


def test_every_model_table_exists_in_migrations(migrated_inspector):
    """No model may exist that migrations never create."""
    migrated_tables = set(migrated_inspector.get_table_names())
    model_tables = set(Base.metadata.tables)

    missing = sorted(model_tables - migrated_tables)
    assert not missing, (
        "These tables are declared in the ORM but no migration creates them: "
        f"{missing}. A fresh production database would be missing them entirely."
    )


def test_no_orphan_tables_in_migrations(migrated_inspector):
    """No migrated table may lack a model, apart from Alembic's own bookkeeping."""
    migrated_tables = set(migrated_inspector.get_table_names()) - {"alembic_version"}
    orphans = sorted(migrated_tables - set(Base.metadata.tables))
    assert not orphans, (
        f"These tables exist in the migrated schema but no model maps them: {orphans}. "
        "Either the model was deleted without a migration, or the migration is stale."
    )


def test_every_model_column_exists_in_migrations(migrated_inspector):
    """Every ORM column must exist in the migrated schema.

    This is the check that catches the specific failure that motivated it: a
    column added to a model without a matching migration works perfectly against
    a `create_all` test database and fails on the first real deployment.
    """
    drift: dict[str, list[str]] = {}

    for table_name, table in Base.metadata.tables.items():
        if table_name not in set(migrated_inspector.get_table_names()):
            continue  # reported by the table-level test
        migrated_columns = {c["name"] for c in migrated_inspector.get_columns(table_name)}
        missing = sorted({c.name for c in table.columns} - migrated_columns)
        if missing:
            drift[table_name] = missing

    assert not drift, (
        "Columns declared in the ORM are absent from the migrated schema: "
        f"{drift}. Add a migration for them."
    )


# Columns that exist in the database but deliberately are not mapped in the ORM.
# `memories.embedding_vector` is a PostgreSQL-only pgvector projection of the JSON
# `embedding` column: a single mapped class cannot carry a column that exists on
# one dialect and not the other, so it is written and read through raw SQL in
# `retrieval/vector_store.py`. It is listed here rather than tolerated silently,
# so any *other* unmapped column still fails the test.
INTENTIONALLY_UNMAPPED: dict[str, set[str]] = {
    "memories": {"embedding_vector"},
}


def test_no_orphan_columns_in_migrations(migrated_inspector):
    """Every migrated column must still be mapped by a model, or be a known exception."""
    drift: dict[str, list[str]] = {}

    for table_name, table in Base.metadata.tables.items():
        if table_name not in set(migrated_inspector.get_table_names()):
            continue
        model_columns = {c.name for c in table.columns}
        model_columns |= INTENTIONALLY_UNMAPPED.get(table_name, set())
        migrated_columns = {c["name"] for c in migrated_inspector.get_columns(table_name)}
        orphans = sorted(migrated_columns - model_columns)
        if orphans:
            drift[table_name] = orphans

    assert not drift, (
        "The migrated schema has columns no model maps: "
        f"{drift}. Either the model lost them or the migration added them by mistake."
    )


def test_unique_constraints_that_enforce_idempotency_are_migrated(migrated_inspector):
    """The idempotency keys must be enforced by the database, not just by code.

    Application-level "check before insert" loses a race; a unique index does not.
    These are the constraints that make re-delivering an event or re-running a
    pipeline safe (specification sections 37 and 47), so their presence in the
    real schema is asserted explicitly rather than assumed.
    """
    required = {
        "claims": "uq_claim_logical_identity",
        "claim_evidences": "uq_claim_evidence_fingerprint",
        "verification_runs": "uq_verification_run_idempotency",
        "memory_decisions": "uq_memory_decision_idempotency",
        "success_episodes": "uq_success_episode_signature",
        "webhook_deliveries": "uq_webhook_delivery",
        "change_sets": "uq_changeset_idempotency",
    }

    for table_name, constraint_name in required.items():
        names = {c["name"] for c in migrated_inspector.get_unique_constraints(table_name)}
        names |= {i["name"] for i in migrated_inspector.get_indexes(table_name) if i["unique"]}
        assert constraint_name in names, (
            f"{table_name} is missing the {constraint_name} constraint in the migrated "
            "schema, so duplicate rows would be accepted by the database."
        )

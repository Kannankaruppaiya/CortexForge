"""Add project_id to memory_versions and agent_events, and epistemic verification fields to failure episodes and fix attempts.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-10 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. agent_events: project_id and agent_id
    if "agent_events" in inspector.get_table_names():
        ae_cols = {c["name"] for c in inspector.get_columns("agent_events")}
        with op.batch_alter_table("agent_events") as batch_op:
            if "project_id" not in ae_cols:
                batch_op.add_column(
                    sa.Column(
                        "project_id",
                        sa.String(length=36),
                        sa.ForeignKey(
                            "projects.id",
                            ondelete="CASCADE",
                            name="fk_agent_events_project_id",
                        ),
                        nullable=True,
                    )
                )
                batch_op.create_index("idx_agent_event_project", ["project_id"])
            if "agent_id" not in ae_cols:
                batch_op.add_column(
                    sa.Column("agent_id", sa.String(length=100), nullable=True)
                )
                batch_op.create_index("idx_agent_event_agent", ["agent_id"])

    # 2. memory_versions: project_id
    if "memory_versions" in inspector.get_table_names():
        mv_cols = {c["name"] for c in inspector.get_columns("memory_versions")}
        if "project_id" not in mv_cols:
            with op.batch_alter_table("memory_versions") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "project_id",
                        sa.String(length=36),
                        sa.ForeignKey(
                            "projects.id",
                            ondelete="CASCADE",
                            name="fk_memver_project_id",
                        ),
                        nullable=True,
                    )
                )
                batch_op.create_index("idx_memver_project", ["project_id"])

    # 3. failure_episodes: candidate_cause and verified_cause
    if "failure_episodes" in inspector.get_table_names():
        fe_cols = {c["name"] for c in inspector.get_columns("failure_episodes")}
        with op.batch_alter_table("failure_episodes") as batch_op:
            if "candidate_cause" not in fe_cols:
                batch_op.add_column(
                    sa.Column("candidate_cause", sa.Text(), nullable=True),
                )
            if "verified_cause" not in fe_cols:
                batch_op.add_column(
                    sa.Column("verified_cause", sa.Text(), nullable=True),
                )

    # 4. fix_attempts: agent_claim, verified_effect, verified_by_test_run_id
    if "fix_attempts" in inspector.get_table_names():
        fa_cols = {c["name"] for c in inspector.get_columns("fix_attempts")}
        with op.batch_alter_table("fix_attempts") as batch_op:
            if "agent_claim" not in fa_cols:
                batch_op.add_column(
                    sa.Column("agent_claim", sa.Text(), nullable=True),
                )
            if "verified_effect" not in fa_cols:
                batch_op.add_column(
                    sa.Column("verified_effect", sa.Text(), nullable=True),
                )
            if "verified_by_test_run_id" not in fa_cols:
                batch_op.add_column(
                    sa.Column(
                        "verified_by_test_run_id",
                        sa.String(length=36),
                        sa.ForeignKey(
                            "test_runs.id",
                            ondelete="SET NULL",
                            name="fk_fix_attempts_test_run",
                        ),
                        nullable=True,
                    ),
                )


def downgrade() -> None:
    # Downgrades drop added columns
    import logging

    log = logging.getLogger("alembic.migration")
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "fix_attempts" in tables:
        try:
            with op.batch_alter_table("fix_attempts") as batch_op:
                batch_op.drop_column("verified_by_test_run_id")
                batch_op.drop_column("verified_effect")
                batch_op.drop_column("agent_claim")
        except Exception as exc:
            log.warning("Could not drop columns from fix_attempts: %s", exc)

    if "failure_episodes" in tables:
        try:
            with op.batch_alter_table("failure_episodes") as batch_op:
                batch_op.drop_column("verified_cause")
                batch_op.drop_column("candidate_cause")
        except Exception as exc:
            log.warning("Could not drop columns from failure_episodes: %s", exc)

    if "memory_versions" in tables:
        try:
            with op.batch_alter_table("memory_versions") as batch_op:
                batch_op.drop_index("idx_memver_project")
                batch_op.drop_column("project_id")
        except Exception as exc:
            log.warning("Could not drop column/index from memory_versions: %s", exc)

    if "agent_events" in tables:
        try:
            with op.batch_alter_table("agent_events") as batch_op:
                batch_op.drop_index("idx_agent_event_agent")
                batch_op.drop_index("idx_agent_event_project")
                batch_op.drop_column("agent_id")
                batch_op.drop_column("project_id")
        except Exception as exc:
            log.warning("Could not drop columns/indexes from agent_events: %s", exc)

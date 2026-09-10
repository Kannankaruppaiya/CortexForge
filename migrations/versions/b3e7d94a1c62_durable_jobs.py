"""durable background jobs

Adds the ``jobs`` table (specification sections 38 and 39).

Jobs previously lived in an in-process dictionary, so a restart during indexing
left no trace that indexing had been happening: the work was neither finished nor
recoverable, and nothing distinguished "never started" from "died halfway
through".

The columns that make recovery possible are ``lease_owner`` / ``lease_expires_at``
(a claim that lapses when the worker holding it dies), ``checkpoint`` (what the
job had finished, so a resumed attempt continues rather than restarts) and
``idempotency_key`` with a unique constraint (so two schedulers submitting the
same work cannot both start it -- enforced by the database, not by a check that
races).

Revision ID: b3e7d94a1c62
Revises: 9f2d5c81ab30
Create Date: 2026-09-09 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e7d94a1c62"
down_revision: str | Sequence[str] | None = "9f2d5c81ab30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column(
            "status", sa.String(length=50), server_default="PENDING", nullable=False
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress", sa.Float(), server_default="0", nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_job_idempotency"),
    )
    op.create_index("idx_job_status_type", "jobs", ["status", "job_type"])
    op.create_index("idx_job_project", "jobs", ["project_id"])
    # Recovery scans for RUNNING jobs whose lease has lapsed; this is the index
    # that query uses.
    op.create_index("idx_job_lease", "jobs", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("idx_job_lease", table_name="jobs")
    op.drop_index("idx_job_project", table_name="jobs")
    op.drop_index("idx_job_status_type", table_name="jobs")
    op.drop_table("jobs")

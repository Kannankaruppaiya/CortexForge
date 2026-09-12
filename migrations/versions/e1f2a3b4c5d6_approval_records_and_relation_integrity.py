"""human approval records and memory relation project backfill

Revision ID: e1f2a3b4c5d6
Revises: d5e9a2b3c4f8
Create Date: 2026-09-09 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d5e9a2b3c4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create human_approval_records table
    op.create_table(
        "human_approval_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("token", sa.String(length=64), unique=True, nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_approval_project", "human_approval_records", ["project_id"])
    op.create_index("idx_approval_token", "human_approval_records", ["token"])
    op.create_index("idx_approval_digest", "human_approval_records", ["payload_digest"])

    # 2. Backfill existing NULL project_id in memory_relations from target memory
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE memory_relations
            SET project_id = (
                SELECT project_id FROM memories WHERE memories.id = memory_relations.target_memory_id
            )
            WHERE project_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("idx_approval_digest", table_name="human_approval_records")
    op.drop_index("idx_approval_token", table_name="human_approval_records")
    op.drop_index("idx_approval_project", table_name="human_approval_records")
    op.drop_table("human_approval_records")

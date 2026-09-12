"""schema integrity hardening: non-null relation and evidence project_id, violation audit

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-09-10 12:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    evidence_columns = [
        col["name"] for col in inspector.get_columns("memory_evidences")
    ]

    if "project_id" not in evidence_columns:
        with op.batch_alter_table("memory_evidences") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "project_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )

        # Backfill from parent memories table
        conn.execute(
            sa.text(
                """
                UPDATE memory_evidences
                SET project_id = (
                    SELECT project_id FROM memories WHERE memories.id = memory_evidences.memory_id
                )
                WHERE project_id IS NULL
                """
            )
        )

        with op.batch_alter_table("memory_evidences") as batch_op:
            batch_op.alter_column("project_id", nullable=False)
            batch_op.create_foreign_key(
                "fk_memory_evidences_project_id",
                "projects",
                ["project_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_index("idx_ev_project", ["project_id"])

    # 2. Make memory_relations.project_id NOT NULL
    with op.batch_alter_table("memory_relations") as batch_op:
        batch_op.alter_column("project_id", nullable=False)

    # 3. Add resolved_at to rule_violations if not present
    violation_columns = [
        col["name"] for col in inspector.get_columns("rule_violations")
    ]
    if "resolved_at" not in violation_columns:
        with op.batch_alter_table("rule_violations") as batch_op:
            batch_op.add_column(
                sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch_op.create_index("idx_violation_active", ["rule_id", "resolved_at"])


def downgrade() -> None:
    with op.batch_alter_table("rule_violations") as batch_op:
        batch_op.drop_index("idx_violation_active")
        batch_op.drop_column("resolved_at")

    with op.batch_alter_table("memory_relations") as batch_op:
        batch_op.alter_column("project_id", nullable=True)

    with op.batch_alter_table("memory_evidences") as batch_op:
        batch_op.drop_index("idx_ev_project")
        batch_op.drop_constraint("fk_memory_evidences_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")

"""initial_cognitive_schema

Revision ID: 043888bcfadd
Revises:
Create Date: 2026-09-09 00:31:55.841223
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "043888bcfadd"
down_revision: str | Sequence[str] | None = "0001a0b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema using batch alter table for cross-database compatibility."""
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "layer", sa.String(length=10), server_default="L1", nullable=False
            )
        )
        batch_op.add_column(
            sa.Column(
                "freshness_score", sa.Float(), server_default="1.0", nullable=False
            )
        )
        batch_op.add_column(
            sa.Column("source_commit", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("supersedes_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("superseded_by_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("conflict_group", sa.String(length=64), nullable=True)
        )
        batch_op.create_index(
            "idx_mem_project_conflict", ["project_id", "conflict_group"], unique=False
        )
        batch_op.create_index(
            "idx_mem_project_layer", ["project_id", "layer"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_mem_superseded_by",
            "memories",
            ["superseded_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_mem_supersedes",
            "memories",
            ["supersedes_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("memory_evidences", schema=None) as batch_op:
        batch_op.add_column(sa.Column("symbol_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("snippet_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ast_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.create_index("idx_ev_file", ["file_path"], unique=False)
        batch_op.create_index("idx_ev_memory", ["memory_id"], unique=False)
        batch_op.create_index("idx_ev_symbol", ["symbol_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_ev_symbol", "code_entities", ["symbol_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("memory_evidences", schema=None) as batch_op:
        batch_op.drop_constraint("fk_ev_symbol", type_="foreignkey")
        batch_op.drop_index("idx_ev_symbol")
        batch_op.drop_index("idx_ev_memory")
        batch_op.drop_index("idx_ev_file")
        batch_op.drop_column("ast_fingerprint")
        batch_op.drop_column("snippet_hash")
        batch_op.drop_column("symbol_id")

    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_constraint("fk_mem_supersedes", type_="foreignkey")
        batch_op.drop_constraint("fk_mem_superseded_by", type_="foreignkey")
        batch_op.drop_index("idx_mem_project_layer")
        batch_op.drop_index("idx_mem_project_conflict")
        batch_op.drop_column("conflict_group")
        batch_op.drop_column("superseded_by_id")
        batch_op.drop_column("supersedes_id")
        batch_op.drop_column("source_commit")
        batch_op.drop_column("freshness_score")
        batch_op.drop_column("layer")
    # ### end Alembic commands ###

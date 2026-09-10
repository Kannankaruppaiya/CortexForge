"""evidence locators, relations project ownership, and structured failure root cause

Revision ID: d5e9a2b3c4f8
Revises: c4f8e1a2b3d5
Create Date: 2026-09-09 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e9a2b3c4f8"
down_revision: str | Sequence[str] | None = "c4f8e1a2b3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Memory evidence universal locators (specification section 5, item 2)
    op.add_column("memory_evidences", sa.Column("uri", sa.Text(), nullable=True))
    op.add_column("memory_evidences", sa.Column("detail", sa.JSON(), nullable=True))

    # Memory relation project ownership (specification section 44, item 12)
    op.add_column(
        "memory_relations", sa.Column("project_id", sa.String(length=36), nullable=True)
    )
    op.create_index("idx_memrel_project", "memory_relations", ["project_id"])

    # Failure episode structured root cause claims (specification section 15, item 15)
    op.add_column(
        "failure_episodes",
        sa.Column("root_cause_claim_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "failure_episodes", sa.Column("root_cause_details", sa.JSON(), nullable=True)
    )
    op.create_index(
        "idx_fail_root_cause_claim", "failure_episodes", ["root_cause_claim_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_fail_root_cause_claim", table_name="failure_episodes")
    op.drop_column("failure_episodes", "root_cause_details")
    op.drop_column("failure_episodes", "root_cause_claim_id")

    op.drop_index("idx_memrel_project", table_name="memory_relations")
    op.drop_column("memory_relations", "project_id")

    op.drop_column("memory_evidences", "detail")
    op.drop_column("memory_evidences", "uri")

"""agent task session provenance

Adds workspace_id, session_id, parent_task_id, provider, model, model_version to agent_tasks table (specification section 11).

Revision ID: c4f8e1a2b3d5
Revises: b3e7d94a1c62
Create Date: 2026-09-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f8e1a2b3d5"
down_revision: str | Sequence[str] | None = "b3e7d94a1c62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks", sa.Column("workspace_id", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("session_id", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("parent_task_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("provider", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("model", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("model_version", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "model_version")
    op.drop_column("agent_tasks", "model")
    op.drop_column("agent_tasks", "provider")
    op.drop_column("agent_tasks", "parent_task_id")
    op.drop_column("agent_tasks", "session_id")
    op.drop_column("agent_tasks", "workspace_id")

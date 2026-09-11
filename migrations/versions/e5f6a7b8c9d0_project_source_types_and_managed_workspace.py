"""Add source_type, clone_url, github_repository_id, github_owner, github_repo, and managed_workspace to projects.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-10 23:45:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.migration")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "projects" in inspector.get_table_names():
        proj_cols = {c["name"] for c in inspector.get_columns("projects")}
        with op.batch_alter_table("projects") as batch_op:
            if "source_type" not in proj_cols:
                batch_op.add_column(
                    sa.Column(
                        "source_type",
                        sa.String(50),
                        server_default="LOCAL",
                        nullable=False,
                    )
                )
                batch_op.create_index("ix_projects_source_type", ["source_type"])
            if "clone_url" not in proj_cols:
                batch_op.add_column(sa.Column("clone_url", sa.Text(), nullable=True))
            if "github_repository_id" not in proj_cols:
                batch_op.add_column(
                    sa.Column("github_repository_id", sa.String(100), nullable=True)
                )
            if "github_owner" not in proj_cols:
                batch_op.add_column(
                    sa.Column("github_owner", sa.String(100), nullable=True)
                )
            if "github_repo" not in proj_cols:
                batch_op.add_column(
                    sa.Column("github_repo", sa.String(100), nullable=True)
                )
            if "managed_workspace" not in proj_cols:
                batch_op.add_column(
                    sa.Column(
                        "managed_workspace",
                        sa.Boolean(),
                        server_default=sa.false(),
                        nullable=False,
                    )
                )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "projects" in inspector.get_table_names():
        proj_cols = {c["name"] for c in inspector.get_columns("projects")}
        try:
            with op.batch_alter_table("projects") as batch_op:
                if "managed_workspace" in proj_cols:
                    batch_op.drop_column("managed_workspace")
                if "github_repo" in proj_cols:
                    batch_op.drop_column("github_repo")
                if "github_owner" in proj_cols:
                    batch_op.drop_column("github_owner")
                if "github_repository_id" in proj_cols:
                    batch_op.drop_column("github_repository_id")
                if "clone_url" in proj_cols:
                    batch_op.drop_column("clone_url")
                if "source_type" in proj_cols:
                    batch_op.drop_index("ix_projects_source_type")
                    batch_op.drop_column("source_type")
        except Exception as exc:
            log.warning("Could not drop columns from projects: %s", exc)

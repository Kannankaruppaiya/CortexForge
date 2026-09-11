"""Add project memberships and agent credentials for rotation.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-10 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. agent_credentials
    if "agent_credentials" not in existing_tables:
        op.create_table(
            "agent_credentials",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "agent_id",
                sa.String(length=36),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("key_id", sa.String(length=32), unique=True, nullable=False),
            sa.Column("key_hash", sa.String(length=64), unique=True, nullable=False),
            sa.Column(
                "name", sa.String(length=100), server_default="default", nullable=False
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_agent_cred_agent", "agent_credentials", ["agent_id"])
        op.create_index(
            "idx_agent_cred_lookup", "agent_credentials", ["key_id", "revoked_at"]
        )
        op.create_index("idx_agent_cred_hash", "agent_credentials", ["key_hash"])

    # 2. project_memberships
    if "project_memberships" not in existing_tables:
        op.create_table(
            "project_memberships",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "role", sa.String(length=50), server_default="MEMBER", nullable=False
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "project_id", name="uq_project_membership"),
        )
        op.create_index("idx_proj_member_user", "project_memberships", ["user_id"])
        op.create_index("idx_proj_member_proj", "project_memberships", ["project_id"])
        op.create_index(
            "idx_proj_member_user_proj",
            "project_memberships",
            ["user_id", "project_id"],
        )


def downgrade() -> None:
    op.drop_table("project_memberships")
    op.drop_table("agent_credentials")

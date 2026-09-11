"""Add github_user_id, github_login, avatar_url to users and create oauth_transactions table.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-10 22:15:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.migration")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Update users table with github fields
    if "users" in inspector.get_table_names():
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch_op:
            if "github_user_id" not in user_cols:
                batch_op.add_column(
                    sa.Column("github_user_id", sa.String(100), nullable=True)
                )
                batch_op.create_index(
                    "ix_users_github_user_id", ["github_user_id"], unique=True
                )
            if "github_login" not in user_cols:
                batch_op.add_column(
                    sa.Column("github_login", sa.String(100), nullable=True)
                )
                batch_op.create_index(
                    "ix_users_github_login", ["github_login"], unique=False
                )
            if "avatar_url" not in user_cols:
                batch_op.add_column(
                    sa.Column("avatar_url", sa.String(512), nullable=True)
                )

    # 2. Create oauth_transactions table
    if "oauth_transactions" not in inspector.get_table_names():
        op.create_table(
            "oauth_transactions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(50), nullable=False, default="github"),
            sa.Column("state", sa.String(128), nullable=False, unique=True),
            sa.Column("code_verifier", sa.String(128), nullable=False),
            sa.Column("redirect_uri", sa.String(512), nullable=True),
            sa.Column("ip_address", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "idx_oauth_tx_state_prov", "oauth_transactions", ["state", "provider"]
        )
        op.create_index(
            "ix_oauth_transactions_state", "oauth_transactions", ["state"], unique=True
        )
        op.create_index(
            "ix_oauth_transactions_expires_at", "oauth_transactions", ["expires_at"]
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "oauth_transactions" in inspector.get_table_names():
        try:
            op.drop_table("oauth_transactions")
        except Exception as exc:
            log.warning("Could not drop oauth_transactions table: %s", exc)

    if "users" in inspector.get_table_names():
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        try:
            with op.batch_alter_table("users") as batch_op:
                if "avatar_url" in user_cols:
                    batch_op.drop_column("avatar_url")
                if "github_login" in user_cols:
                    batch_op.drop_index("ix_users_github_login")
                    batch_op.drop_column("github_login")
                if "github_user_id" in user_cols:
                    batch_op.drop_index("ix_users_github_user_id")
                    batch_op.drop_column("github_user_id")
        except Exception as exc:
            log.warning("Could not drop users columns: %s", exc)

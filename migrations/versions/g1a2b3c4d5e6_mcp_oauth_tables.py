"""Add MCP OAuth clients, authorization codes, and tokens tables.

Revision ID: g1a2b3c4d5e6
Revises: f6a7b8c9d0e1
Create Date: 2026-09-12 12:00:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.migration")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "mcp_oauth_clients" not in tables:
        op.create_table(
            "mcp_oauth_clients",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("client_id", sa.String(255), unique=True, nullable=False),
            sa.Column("client_name", sa.String(255), nullable=True),
            sa.Column("client_secret_hash", sa.String(255), nullable=True),
            sa.Column("redirect_uris", sa.JSON(), nullable=False),
            sa.Column("grant_types", sa.JSON(), nullable=False),
            sa.Column("response_types", sa.JSON(), nullable=False),
            sa.Column("token_endpoint_auth_method", sa.String(64), nullable=False, server_default="none"),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_mcp_oauth_clients_client_id", "mcp_oauth_clients", ["client_id"])

    if "mcp_oauth_authorization_codes" not in tables:
        op.create_table(
            "mcp_oauth_authorization_codes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("code", sa.String(255), unique=True, nullable=False),
            sa.Column("client_id", sa.String(255), sa.ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("redirect_uri", sa.String(1024), nullable=False),
            sa.Column("code_challenge", sa.String(255), nullable=False),
            sa.Column("code_challenge_method", sa.String(32), nullable=False, server_default="S256"),
            sa.Column("scope", sa.String(512), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_mcp_oauth_auth_codes_code", "mcp_oauth_authorization_codes", ["code"])
        op.create_index("ix_mcp_oauth_auth_codes_client_id", "mcp_oauth_authorization_codes", ["client_id"])
        op.create_index("ix_mcp_oauth_auth_codes_user_id", "mcp_oauth_authorization_codes", ["user_id"])

    if "mcp_oauth_tokens" not in tables:
        op.create_table(
            "mcp_oauth_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
            sa.Column("client_id", sa.String(255), sa.ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_type", sa.String(32), nullable=False, server_default="Bearer"),
            sa.Column("scope", sa.String(512), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_mcp_oauth_tokens_token_hash", "mcp_oauth_tokens", ["token_hash"])
        op.create_index("ix_mcp_oauth_tokens_client_id", "mcp_oauth_tokens", ["client_id"])
        op.create_index("ix_mcp_oauth_tokens_user_id", "mcp_oauth_tokens", ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "mcp_oauth_tokens" in tables:
        op.drop_table("mcp_oauth_tokens")
    if "mcp_oauth_authorization_codes" in tables:
        op.drop_table("mcp_oauth_authorization_codes")
    if "mcp_oauth_clients" in tables:
        op.drop_table("mcp_oauth_clients")

"""Individual user authentication, credentials, sessions, and project ownership schema.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-10 16:30:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # 1. users
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("email", sa.String(length=255), unique=True, nullable=False),
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column(
                "status",
                sa.String(length=50),
                server_default="ACTIVE",
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_users_email", "users", ["email"])

    # 2. password_credentials
    if "password_credentials" not in existing_tables:
        op.create_table(
            "password_credentials",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                unique=True,
                nullable=False,
            ),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column(
                "algorithm",
                sa.String(length=50),
                server_default="scrypt",
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # 3. external_identities
    if "external_identities" not in existing_tables:
        op.create_table(
            "external_identities",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("provider_subject", sa.String(length=255), nullable=False),
            sa.Column("provider_email", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "provider", "provider_subject", name="uq_provider_subject"
            ),
        )
        op.create_index("idx_ext_identity_user", "external_identities", ["user_id"])

    # 4. sessions
    if "sessions" not in existing_tables:
        op.create_table(
            "sessions",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "session_token_hash",
                sa.String(length=64),
                unique=True,
                nullable=False,
            ),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("ip_address", sa.String(length=100), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_sessions_user", "sessions", ["user_id"])
        op.create_index("idx_sessions_token", "sessions", ["session_token_hash"])
        op.create_index("idx_sessions_expires", "sessions", ["expires_at"])

    # 5. email_otp_challenges
    if "email_otp_challenges" not in existing_tables:
        op.create_table(
            "email_otp_challenges",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("otp_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "attempts_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
            sa.Column(
                "max_attempts",
                sa.Integer(),
                server_default="5",
                nullable=False,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_otp_email", "email_otp_challenges", ["email"])
        op.create_index("idx_otp_expires", "email_otp_challenges", ["expires_at"])

    # 6. password_reset_tokens
    if "password_reset_tokens" not in existing_tables:
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(length=64), unique=True, nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_pwd_reset_user", "password_reset_tokens", ["user_id"])
        op.create_index(
            "idx_pwd_reset_expires", "password_reset_tokens", ["expires_at"]
        )

    # 7. agents
    if "agents" not in existing_tables:
        op.create_table(
            "agents",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "owner_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column(
                "type", sa.String(length=50), server_default="custom", nullable=False
            ),
            sa.Column(
                "status",
                sa.String(length=50),
                server_default="ACTIVE",
                nullable=False,
            ),
            sa.Column(
                "api_key_hash", sa.String(length=64), unique=True, nullable=False
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_agents_owner", "agents", ["owner_user_id"])
        op.create_index("idx_agents_api_key", "agents", ["api_key_hash"])

    # 8. agent_project_permissions
    if "agent_project_permissions" not in existing_tables:
        op.create_table(
            "agent_project_permissions",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "agent_id",
                sa.String(length=36),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "agent_id", "project_id", name="uq_agent_project_permission"
            ),
        )
        op.create_index("idx_perm_agent", "agent_project_permissions", ["agent_id"])
        op.create_index("idx_perm_project", "agent_project_permissions", ["project_id"])

    # 10. projects.owner_user_id
    project_columns = [col["name"] for col in inspector.get_columns("projects")]
    if "owner_user_id" not in project_columns:
        # Default user for existing data backfill if any projects exist
        default_user_id = "00000000-0000-0000-0000-000000000001"
        now = datetime.now(UTC)
        existing_user = conn.execute(
            sa.text("SELECT 1 FROM users WHERE id = :uid"),
            {"uid": default_user_id},
        ).scalar()
        if not existing_user:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO users (id, email, display_name, status, created_at, updated_at)
                    VALUES (:uid, 'system@cortexforge.local', 'Default System User', 'ACTIVE', :created, :created)
                    """
                ),
                {"uid": default_user_id, "created": now},
            )

        with op.batch_alter_table("projects") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "owner_user_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )

        conn.execute(
            sa.text(
                """
                UPDATE projects
                SET owner_user_id = :uid
                WHERE owner_user_id IS NULL
                """
            ),
            {"uid": default_user_id},
        )

        with op.batch_alter_table("projects") as batch_op:
            batch_op.alter_column("owner_user_id", nullable=False)
            batch_op.create_foreign_key(
                "fk_projects_owner_user_id",
                "users",
                ["owner_user_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_index("idx_projects_owner", ["owner_user_id"])

    # 11. jobs: user_id, actor_type, actor_id
    job_columns = [col["name"] for col in inspector.get_columns("jobs")]
    with op.batch_alter_table("jobs") as batch_op:
        if "user_id" not in job_columns:
            batch_op.add_column(
                sa.Column("user_id", sa.String(length=36), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_jobs_user_id",
                "users",
                ["user_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "actor_type" not in job_columns:
            batch_op.add_column(
                sa.Column(
                    "actor_type",
                    sa.String(length=50),
                    server_default="USER",
                    nullable=False,
                )
            )
        if "actor_id" not in job_columns:
            batch_op.add_column(
                sa.Column("actor_id", sa.String(length=100), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("actor_id")
        batch_op.drop_column("actor_type")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("owner_user_id")

    op.drop_table("agent_project_permissions")
    op.drop_table("agents")
    op.drop_table("password_reset_tokens")
    op.drop_table("email_otp_challenges")
    op.drop_table("sessions")
    op.drop_table("external_identities")
    op.drop_table("password_credentials")
    op.drop_table("users")

"""base schema: the tables every later migration assumed already existed

The migration chain previously began with ``043888bcfadd``, which *alters*
``memories`` and ``memory_evidences``. Nothing created them, so
``alembic upgrade head`` against an empty database failed immediately with
``NoSuchTableError: memories``. The schema was only ever reachable through
``Base.metadata.create_all``, which meant migrations were untested in the one
situation they exist for: a real deployment.

This revision is inserted as the new root and creates those tables in the shape
the following revisions expect -- deliberately *without* the columns that
``043888bcfadd`` and ``512999ccfadd`` go on to add, so that the chain applies
cleanly in order and each later revision still does the work it describes.

Revision ID: 0001a0b2c3d4
Revises:
Create Date: 2026-09-08 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001a0b2c3d4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("repository_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "default_branch",
            sa.String(length=100),
            nullable=False,
            server_default="main",
        ),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("last_indexed_commit", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="READY"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "repository_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("symbol_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dependency_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "code_entities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_entity_project_qualified",
        "code_entities",
        ["project_id", "qualified_name"],
    )
    op.create_index(
        "idx_entity_project_file", "code_entities", ["project_id", "file_path"]
    )
    op.create_index(
        "idx_entity_project_type", "code_entities", ["project_id", "entity_type"]
    )
    op.create_index(
        "idx_entity_project_hash", "code_entities", ["project_id", "content_hash"]
    )

    op.create_table(
        "relationships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_entity_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_entity_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "source", sa.String(length=50), nullable=False, server_default="tree_sitter"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_rel_project_source",
        "relationships",
        ["project_id", "source_entity_id", "relationship_type"],
    )
    op.create_index(
        "idx_rel_project_target",
        "relationships",
        ["project_id", "target_entity_id", "relationship_type"],
    )

    # `memories` is created without layer / freshness_score / source_commit /
    # supersedes_id / superseded_by_id / conflict_group: revision 043888bcfadd
    # adds those, and without this table it could never run.
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "source_type", sa.String(length=50), nullable=False, server_default="code"
        ),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_by", sa.String(length=100), nullable=False, server_default="system"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_mem_project_type", "memories", ["project_id", "memory_type"])
    op.create_index("idx_mem_project_status", "memories", ["project_id", "status"])

    op.create_table(
        "memory_evidences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "memory_relations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "source_memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "memory_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(length=100), nullable=True),
        sa.Column("task_text", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="PENDING"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("token_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    for table in (
        "agent_events",
        "agent_tasks",
        "memory_versions",
        "memory_relations",
        "memory_evidences",
        "memories",
        "relationships",
        "code_entities",
        "repository_snapshots",
        "projects",
    ):
        op.drop_table(table)

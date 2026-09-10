"""first_class_cognitive_models

Revision ID: 512999ccfadd
Revises: 043888bcfadd
Create Date: 2026-09-09 01:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "512999ccfadd"
down_revision: str | Sequence[str] | None = "043888bcfadd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update memories
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope", sa.String(length=50), server_default="PROJECT", nullable=False
            )
        )
        batch_op.add_column(
            sa.Column("valid_from_commit", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("valid_to_commit", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("valid_from_time", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("valid_to_time", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "idx_mem_project_scope", ["project_id", "scope"], unique=False
        )

    # 2. Update memory_versions
    with op.batch_alter_table("memory_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("old_state", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("new_state", sa.String(length=50), nullable=True))
        batch_op.add_column(
            sa.Column(
                "actor", sa.String(length=100), server_default="system", nullable=False
            )
        )
        batch_op.add_column(
            sa.Column("commit_sha", sa.String(length=64), nullable=True)
        )

    # 3. Create commits
    op.create_table(
        "commits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("parent_sha", sa.String(length=64), nullable=True),
        sa.Column(
            "branch", sa.String(length=100), server_default="main", nullable=False
        ),
        sa.Column(
            "author", sa.String(length=255), server_default="unknown", nullable=False
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_commit_project_sha", "commits", ["project_id", "commit_sha"], unique=True
    )

    # 4. Create change_sets
    op.create_table(
        "change_sets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("target_commit_sha", sa.String(length=64), nullable=False),
        # `sa.false()` rather than `sa.text('0')`: SQLite accepts the integer
        # literal, PostgreSQL rejects it as a type mismatch against a boolean
        # column. This migration had never been run against PostgreSQL, which is
        # the declared production database.
        sa.Column(
            "is_working_tree", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_changeset_project_commits",
        "change_sets",
        ["project_id", "base_commit_sha", "target_commit_sha"],
        unique=False,
    )

    # 5. Create file_changes
    op.create_table(
        "file_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "change_set_id",
            sa.String(length=36),
            sa.ForeignKey("change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("old_path", sa.Text(), nullable=True),
        sa.Column("change_type", sa.String(length=50), nullable=False),
        sa.Column("lines_added", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lines_deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_filechange_set_path",
        "file_changes",
        ["change_set_id", "file_path"],
        unique=False,
    )

    # 6. Create symbol_changes
    op.create_table(
        "symbol_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "change_set_id",
            sa.String(length=36),
            sa.ForeignKey("change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("symbol_name", sa.String(length=255), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("change_type", sa.String(length=50), nullable=False),
        sa.Column("old_signature", sa.Text(), nullable=True),
        sa.Column("new_signature", sa.Text(), nullable=True),
        sa.Column(
            "old_symbol_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "new_symbol_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_symbolchange_set_qualified",
        "symbol_changes",
        ["change_set_id", "qualified_name"],
        unique=False,
    )

    # 7. Create test_runs
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column(
            "framework", sa.String(length=50), server_default="pytest", nullable=False
        ),
        sa.Column(
            "status", sa.String(length=50), server_default="PASSED", nullable=False
        ),
        sa.Column("total_tests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("passed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_testrun_project_commit",
        "test_runs",
        ["project_id", "commit_sha"],
        unique=False,
    )

    # 8. Create test_case_results
    op.create_table(
        "test_case_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "test_run_id",
            sa.String(length=36),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_name", sa.String(length=255), nullable=False),
        sa.Column("suite", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("duration_ms", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("failure_signature", sa.String(length=64), nullable=True),
        sa.Column("affected_files", sa.JSON(), nullable=True),
        sa.Column("affected_symbols", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_testcase_run_status",
        "test_case_results",
        ["test_run_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_testcase_signature",
        "test_case_results",
        ["failure_signature"],
        unique=False,
    )

    # 9. Create failure_episodes
    op.create_table(
        "failure_episodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "test_case_result_id",
            sa.String(length=36),
            sa.ForeignKey("test_case_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("failure_signature", sa.String(length=64), nullable=False),
        sa.Column(
            "error_class", sa.String(length=100), server_default="Error", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("normalized_trace", sa.Text(), nullable=True),
        sa.Column("attempted_approach", sa.Text(), nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("command_or_tool", sa.String(length=100), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("affected_files", sa.JSON(), nullable=True),
        sa.Column("affected_symbols", sa.JSON(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_fail_project_sig",
        "failure_episodes",
        ["project_id", "failure_signature"],
        unique=False,
    )

    # 10. Create fix_attempts
    op.create_table(
        "fix_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "failure_episode_id",
            sa.String(length=36),
            sa.ForeignKey("failure_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("attempted_fix", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("why_worked_or_failed", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 11. Create architecture_rules
    op.create_table(
        "architecture_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "scope", sa.String(length=50), server_default="PROJECT", nullable=False
        ),
        sa.Column(
            "severity", sa.String(length=50), server_default="ERROR", nullable=False
        ),
        sa.Column("forbidden_source_pattern", sa.Text(), nullable=False),
        sa.Column("forbidden_target_pattern", sa.Text(), nullable=False),
        sa.Column(
            "enforcement_status",
            sa.String(length=50),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 12. Create rule_violations
    op.create_table(
        "rule_violations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(length=36),
            sa.ForeignKey("architecture_rules.id", ondelete="CASCADE"),
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
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("violation_details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 13. Create code_reviews
    op.create_table(
        "code_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "reviewer",
            sa.String(length=100),
            server_default="cortex-reviewer",
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=50), server_default="APPROVED", nullable=False
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("comments", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 14. Create cognitive_snapshots
    op.create_table(
        "cognitive_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "cognitive_generation", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column(
            "memory_generation", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("graph_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("index_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "active_memories_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "stale_memories_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "conflicted_memories_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "retrieval_version",
            sa.String(length=50),
            server_default="v2",
            nullable=False,
        ),
        sa.Column(
            "embedding_version",
            sa.String(length=50),
            server_default="1.0.0",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_cogsnap_project_commit",
        "cognitive_snapshots",
        ["project_id", "commit_sha"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("cognitive_snapshots")
    op.drop_table("code_reviews")
    op.drop_table("rule_violations")
    op.drop_table("architecture_rules")
    op.drop_table("fix_attempts")
    op.drop_table("failure_episodes")
    op.drop_table("test_case_results")
    op.drop_table("test_runs")
    op.drop_table("symbol_changes")
    op.drop_table("file_changes")
    op.drop_table("change_sets")
    op.drop_table("commits")

    with op.batch_alter_table("memory_versions", schema=None) as batch_op:
        batch_op.drop_column("commit_sha")
        batch_op.drop_column("actor")
        batch_op.drop_column("new_state")
        batch_op.drop_column("old_state")

    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_index("idx_mem_project_scope")
        batch_op.drop_column("valid_to_time")
        batch_op.drop_column("valid_from_time")
        batch_op.drop_column("valid_to_commit")
        batch_op.drop_column("valid_from_commit")
        batch_op.drop_column("scope")

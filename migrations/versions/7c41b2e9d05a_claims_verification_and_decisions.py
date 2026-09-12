"""claims, verification, reconciliation decisions and learning records

Adds the proposition layer the cognitive model is built on:

* ``claims`` / ``claim_evidences`` -- individually evaluable statements and the
  evidence for *and against* them.
* ``verification_policies`` / ``verification_runs`` / ``verification_results`` --
  what was checked, under which versioned policy, with what outcome and reason.
* ``memory_decisions`` -- persisted reconciliation decisions, so the reasoning
  behind every keep / reanchor / revise / invalidate is auditable and replayable.
* ``success_episodes``, ``retrieval_events``, ``audit_logs`` -- learning from what
  worked, from what retrieval was actually useful, and a trail of who changed what.
* ``webhook_deliveries``, ``symbol_lineages`` -- idempotent event ingestion and
  durable symbol identity across renames.

Existing tables gain authority, epistemic state, branch/workspace and persisted
confidence components, plus range and idempotency constraints at the database
level rather than in application code alone.

Revision ID: 7c41b2e9d05a
Revises: 512999ccfadd
Create Date: 2026-09-08 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c41b2e9d05a"
down_revision: str | Sequence[str] | None = "512999ccfadd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ claims
    op.create_table(
        "claims",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("claim_key", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("predicate", sa.Text(), nullable=True),
        sa.Column(
            "epistemic_state",
            sa.String(length=50),
            server_default="OBSERVATION",
            nullable=False,
        ),
        sa.Column(
            "authority",
            sa.String(length=50),
            server_default="AGENT_OBSERVED",
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=50), server_default="PROPOSED", nullable=False
        ),
        sa.Column(
            "scope", sa.String(length=50), server_default="PROJECT", nullable=False
        ),
        sa.Column("scope_ref", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("confidence_components", sa.JSON(), nullable=False),
        sa.Column("valid_from_commit", sa.String(length=64), nullable=True),
        sa.Column("valid_to_commit", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("workspace", sa.String(length=200), nullable=True),
        sa.Column("last_outcome", sa.String(length=50), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "superseded_by_id",
            sa.String(length=36),
            sa.ForeignKey("claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "claim_key",
            "branch",
            "valid_to_commit",
            name="uq_claim_logical_identity",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_claim_confidence_range"
        ),
    )
    op.create_index("idx_claim_project_status", "claims", ["project_id", "status"])
    op.create_index("idx_claim_memory", "claims", ["memory_id"])
    op.create_index("idx_claim_key", "claims", ["project_id", "claim_key"])

    op.create_table(
        "claim_evidences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "claim_id",
            sa.String(length=36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column(
            "relation", sa.String(length=50), server_default="SUPPORTS", nullable=False
        ),
        sa.Column(
            "authority",
            sa.String(length=50),
            server_default="AGENT_OBSERVED",
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("workspace", sa.String(length=200), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column(
            "symbol_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("qualified_name", sa.Text(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("ast_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state", sa.String(length=50), server_default="UNCHECKED", nullable=False
        ),
        sa.Column("independence_group", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "claim_id", "evidence_hash", name="uq_claim_evidence_fingerprint"
        ),
    )
    op.create_index(
        "idx_claimev_claim_relation", "claim_evidences", ["claim_id", "relation"]
    )
    op.create_index("idx_claimev_file", "claim_evidences", ["file_path"])
    op.create_index("idx_claimev_symbol", "claim_evidences", ["symbol_id"])

    # ----------------------------------------------------------- verification
    op.create_table(
        "verification_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(length=100), nullable=False),
        sa.Column("applies_to_evidence_type", sa.String(length=50), nullable=True),
        sa.Column("applies_to_epistemic_state", sa.String(length=50), nullable=True),
        sa.Column(
            "minimum_authority",
            sa.String(length=50),
            server_default="AGENT_OBSERVED",
            nullable=False,
        ),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "name", "version", name="uq_verification_policy_version"
        ),
    )

    op.create_table(
        "verification_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("workspace", sa.String(length=200), nullable=True),
        sa.Column(
            "verifier", sa.String(length=100), nullable=False, server_default="system"
        ),
        sa.Column(
            "trigger", sa.String(length=50), nullable=False, server_default="manual"
        ),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="RUNNING"
        ),
        sa.Column("claims_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "partially_verified_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflicted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "not_applicable_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_verification_run_idempotency"
        ),
    )
    op.create_index(
        "idx_verrun_project_commit", "verification_runs", ["project_id", "commit_sha"]
    )

    op.create_table(
        "verification_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("verification_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(length=36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.String(length=36),
            sa.ForeignKey("verification_policies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "policy_name",
            sa.String(length=150),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outcome", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=60), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("evidence_checked", sa.JSON(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("workspace", sa.String(length=200), nullable=True),
        sa.Column(
            "verifier", sa.String(length=100), nullable=False, server_default="system"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "claim_id", "policy_name", name="uq_verification_result_unique"
        ),
    )
    op.create_index(
        "idx_verres_claim_created", "verification_results", ["claim_id", "created_at"]
    )
    op.create_index("idx_verres_outcome", "verification_results", ["outcome"])

    # ------------------------------------------------------ memory decisions
    op.create_table(
        "memory_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(length=36),
            sa.ForeignKey("claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "change_set_id",
            sa.String(length=36),
            sa.ForeignKey("change_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "verification_run_id",
            sa.String(length=36),
            sa.ForeignKey("verification_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=60), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("previous_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=True),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("new_version", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("workspace", sa.String(length=200), nullable=True),
        sa.Column(
            "actor",
            sa.String(length=100),
            nullable=False,
            server_default="reconciliation",
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_memory_decision_idempotency"
        ),
    )
    op.create_index(
        "idx_memdec_project_created", "memory_decisions", ["project_id", "created_at"]
    )
    op.create_index("idx_memdec_memory", "memory_decisions", ["memory_id"])
    op.create_index("idx_memdec_changeset", "memory_decisions", ["change_set_id"])

    # ------------------------------------------------------------- learning
    op.create_table(
        "success_episodes",
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
            "failure_episode_id",
            sa.String(length=36),
            sa.ForeignKey("failure_episodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("task_context", sa.Text(), nullable=False),
        sa.Column("approach", sa.Text(), nullable=False),
        sa.Column("why_it_worked", sa.Text(), nullable=True),
        sa.Column("affected_files", sa.JSON(), nullable=False),
        sa.Column("affected_symbols", sa.JSON(), nullable=False),
        sa.Column(
            "test_run_id",
            sa.String(length=36),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tests_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "signature", name="uq_success_episode_signature"
        ),
    )
    op.create_index(
        "idx_success_project_created", "success_episodes", ["project_id", "created_at"]
    )

    op.create_table(
        "retrieval_events",
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
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("returned_memory_ids", sa.JSON(), nullable=False),
        sa.Column("selected_memory_ids", sa.JSON(), nullable=False),
        sa.Column("used_memory_ids", sa.JSON(), nullable=False),
        sa.Column("excluded_reasons", sa.JSON(), nullable=False),
        sa.Column(
            "stale_returned_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "conflicted_returned_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("task_outcome", sa.String(length=50), nullable=True),
        sa.Column("tests_passed", sa.Integer(), nullable=True),
        sa.Column("tests_failed", sa.Integer(), nullable=True),
        sa.Column("context_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "retrieval_version",
            sa.String(length=50),
            nullable=False,
            server_default="v2",
        ),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_version", sa.String(length=50), nullable=True),
        sa.Column("memory_generation", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_retrievalev_project_created",
        "retrieval_events",
        ["project_id", "created_at"],
    )
    op.create_index("idx_retrievalev_task", "retrieval_events", ["task_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "actor", sa.String(length=150), nullable=False, server_default="system"
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_audit_project_created", "audit_logs", ["project_id", "created_at"]
    )
    op.create_index(
        "idx_audit_resource", "audit_logs", ["resource_type", "resource_id"]
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "provider", sa.String(length=50), nullable=False, server_default="github"
        ),
        sa.Column("delivery_id", sa.String(length=150), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery"),
    )

    op.create_table(
        "symbol_lineages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_id", sa.String(length=36), nullable=False),
        sa.Column(
            "entity_id",
            sa.String(length=36),
            sa.ForeignKey("code_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "change_kind",
            sa.String(length=50),
            nullable=False,
            server_default="OBSERVED",
        ),
        sa.Column(
            "predecessor_id",
            sa.String(length=36),
            sa.ForeignKey("symbol_lineages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("valid_from_commit", sa.String(length=64), nullable=True),
        sa.Column("valid_to_commit", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "logical_id",
            "qualified_name",
            "file_path",
            "valid_from_commit",
            name="uq_symbol_lineage_step",
        ),
    )
    op.create_index(
        "idx_lineage_project_logical", "symbol_lineages", ["project_id", "logical_id"]
    )
    op.create_index(
        "idx_lineage_project_qualified",
        "symbol_lineages",
        ["project_id", "qualified_name"],
    )

    # ------------------------------------------------- existing table changes
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "confidence_components", sa.JSON(), nullable=False, server_default="{}"
            )
        )
        batch_op.add_column(
            sa.Column(
                "authority",
                sa.String(length=50),
                nullable=False,
                server_default="AGENT_OBSERVED",
            )
        )
        batch_op.add_column(
            sa.Column(
                "epistemic_state",
                sa.String(length=50),
                nullable=False,
                server_default="OBSERVATION",
            )
        )
        batch_op.add_column(sa.Column("branch", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("workspace", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "is_working_tree",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_check_constraint(
            "ck_memory_confidence_range", "confidence >= 0.0 AND confidence <= 1.0"
        )
        batch_op.create_check_constraint(
            "ck_memory_importance_range", "importance >= 0.0 AND importance <= 1.0"
        )

    with op.batch_alter_table("memory_evidences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "evidence_type",
                sa.String(length=50),
                nullable=False,
                server_default="CODE",
            )
        )
        batch_op.add_column(
            sa.Column(
                "relation",
                sa.String(length=50),
                nullable=False,
                server_default="SUPPORTS",
            )
        )
        batch_op.add_column(
            sa.Column(
                "authority",
                sa.String(length=50),
                nullable=False,
                server_default="CODE_VERIFIED",
            )
        )
        batch_op.add_column(sa.Column("branch", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("workspace", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "state",
                sa.String(length=50),
                nullable=False,
                server_default="UNCHECKED",
            )
        )

    with op.batch_alter_table("change_sets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("branch", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("workspace", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_changeset_idempotency", ["project_id", "idempotency_key"]
        )

    with op.batch_alter_table("architecture_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "modality",
                sa.String(length=20),
                nullable=False,
                server_default="MUST_NOT",
            )
        )
        batch_op.add_column(
            sa.Column(
                "authority",
                sa.String(length=50),
                nullable=False,
                server_default="USER_CONFIRMED",
            )
        )
        batch_op.add_column(
            sa.Column(
                "source", sa.String(length=100), nullable=False, server_default="user"
            )
        )
        batch_op.add_column(
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )

    with op.batch_alter_table("cognitive_snapshots", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("embedding_model", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(sa.Column("branch", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("workspace", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("memory_versions", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("claim_states", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column(
                "architecture_rule_versions",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )
        batch_op.add_column(
            sa.Column(
                "verification_state", sa.JSON(), nullable=False, server_default="{}"
            )
        )
        batch_op.add_column(
            sa.Column("policy_versions", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("state_hash", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("cognitive_snapshots", schema=None) as batch_op:
        for column in (
            "state_hash",
            "policy_versions",
            "verification_state",
            "architecture_rule_versions",
            "claim_states",
            "memory_versions",
            "workspace",
            "branch",
            "embedding_model",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("architecture_rules", schema=None) as batch_op:
        for column in ("version", "evidence", "source", "authority", "modality"):
            batch_op.drop_column(column)

    with op.batch_alter_table("change_sets", schema=None) as batch_op:
        batch_op.drop_constraint("uq_changeset_idempotency", type_="unique")
        for column in ("idempotency_key", "workspace", "branch"):
            batch_op.drop_column(column)

    with op.batch_alter_table("memory_evidences", schema=None) as batch_op:
        for column in (
            "state",
            "workspace",
            "branch",
            "authority",
            "relation",
            "evidence_type",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_constraint("ck_memory_importance_range", type_="check")
        batch_op.drop_constraint("ck_memory_confidence_range", type_="check")
        for column in (
            "is_working_tree",
            "workspace",
            "branch",
            "epistemic_state",
            "authority",
            "confidence_components",
        ):
            batch_op.drop_column(column)

    for table in (
        "symbol_lineages",
        "webhook_deliveries",
        "audit_logs",
        "retrieval_events",
        "success_episodes",
        "memory_decisions",
        "verification_results",
        "verification_runs",
        "verification_policies",
        "claim_evidences",
        "claims",
    ):
        op.drop_table(table)

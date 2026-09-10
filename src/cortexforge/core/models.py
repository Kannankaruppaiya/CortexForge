"""SQLAlchemy 2.0 Declarative Models for CortexForge."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(
        String(100), default="main", nullable=False
    )
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_indexed_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="READY", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    snapshots: Mapped[list["RepositorySnapshot"]] = relationship(
        "RepositorySnapshot", back_populates="project", cascade="all, delete-orphan"
    )
    code_entities: Mapped[list["CodeEntity"]] = relationship(
        "CodeEntity", back_populates="project", cascade="all, delete-orphan"
    )
    relationships: Mapped[list["Relationship"]] = relationship(
        "Relationship", back_populates="project", cascade="all, delete-orphan"
    )
    memories: Mapped[list["Memory"]] = relationship(
        "Memory", back_populates="project", cascade="all, delete-orphan"
    )
    agent_tasks: Mapped[list["AgentTask"]] = relationship(
        "AgentTask", back_populates="project", cascade="all, delete-orphan"
    )
    commits: Mapped[list["Commit"]] = relationship(
        "Commit",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    change_sets: Mapped[list["ChangeSet"]] = relationship(
        "ChangeSet",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    test_runs: Mapped[list["TestRun"]] = relationship(
        "TestRun",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    failure_episodes: Mapped[list["FailureEpisode"]] = relationship(
        "FailureEpisode",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    architecture_rules: Mapped[list["ArchitectureRule"]] = relationship(
        "ArchitectureRule",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    cognitive_snapshots: Mapped[list["CognitiveSnapshot"]] = relationship(
        "CognitiveSnapshot",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    code_reviews: Mapped[list["CodeReview"]] = relationship(
        "CodeReview",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RepositorySnapshot(Base):
    __tablename__ = "repository_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dependency_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="snapshots")


class CodeEntity(Base):
    __tablename__ = "code_entities"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # file, module, class, interface, function, method, variable, api, model, test
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="code_entities")

    __table_args__ = (
        Index("idx_entity_project_qualified", "project_id", "qualified_name"),
        Index("idx_entity_project_file", "project_id", "file_path"),
        Index("idx_entity_project_type", "project_id", "entity_type"),
        Index("idx_entity_project_hash", "project_id", "content_hash"),
    )


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # imports, calls, inherits, implements, depends_on, tests, routes_to, uses, contains
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(
        String(50), default="tree_sitter", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="relationships")
    source_entity: Mapped["CodeEntity"] = relationship(
        "CodeEntity", foreign_keys=[source_entity_id]
    )
    target_entity: Mapped["CodeEntity"] = relationship(
        "CodeEntity", foreign_keys=[target_entity_id]
    )

    __table_args__ = (
        Index(
            "idx_rel_project_source",
            "project_id",
            "source_entity_id",
            "relationship_type",
        ),
        Index(
            "idx_rel_project_target",
            "project_id",
            "target_entity_id",
            "relationship_type",
        ),
    )


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(
        String(10), default="L1", server_default="L1", nullable=False
    )  # L0, L1, L2, L3, L4, L5, L6 (discrete from memory_type)
    memory_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # FACT, DECISION, CONSTRAINT, EPISODE, FAILURE, FIX, ARCHITECTURE, CONVENTION, GOAL, LESSON, WARNING, TASK_STATE, SKILL
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="ACTIVE", server_default="ACTIVE", nullable=False
    )  # CANDIDATE, UNVERIFIED, ACTIVE, STALE, CONFLICTED, SUPERSEDED, INVALIDATED, ARCHIVED
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # Persisted explanation of how `confidence` was derived (section 8). Confidence
    # is always recomputed from these components; it is never incremented in place,
    # so repeatedly re-checking unchanged evidence cannot inflate it.
    confidence_components: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    freshness_score: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False
    )
    # Where this statement's entitlement to be believed comes from (section 6).
    authority: Mapped[str] = mapped_column(
        String(50),
        default="AGENT_OBSERVED",
        server_default="AGENT_OBSERVED",
        nullable=False,
    )
    # What kind of knowledge this is (section 3). Uncertain information stays
    # OBSERVATION / INFERENCE / HYPOTHESIS rather than being coerced into FACT.
    epistemic_state: Mapped[str] = mapped_column(
        String(50), default="OBSERVATION", server_default="OBSERVATION", nullable=False
    )
    # Branch / worktree cognition (section 19). `is_working_tree` marks knowledge
    # observed from an uncommitted tree, which must not become durable project truth.
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_working_tree: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(50), default="code", nullable=False
    )  # code, git, test, agent_observation, doc, user
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(100), default="system", nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    conflict_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str] = mapped_column(
        String(50), default="PROJECT", server_default="PROJECT", nullable=False
    )  # PROJECT, MODULE, FILE, SYMBOL, FEATURE, TASK, BRANCH, ENVIRONMENT
    valid_from_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_to_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_from_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    project: Mapped["Project"] = relationship("Project", back_populates="memories")
    evidences: Mapped[list["MemoryEvidence"]] = relationship(
        "MemoryEvidence",
        back_populates="memory",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    versions: Mapped[list["MemoryVersion"]] = relationship(
        "MemoryVersion",
        back_populates="memory",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    claims: Mapped[list["Claim"]] = relationship(
        "Claim", back_populates="memory", cascade="all, delete-orphan", lazy="selectin"
    )
    supersedes: Mapped["Memory | None"] = relationship(
        "Memory", foreign_keys=[supersedes_id], remote_side=[id], post_update=True
    )
    superseded_by: Mapped["Memory | None"] = relationship(
        "Memory", foreign_keys=[superseded_by_id], remote_side=[id], post_update=True
    )

    __table_args__ = (
        # Range invariants enforced by the database, not only by application code
        # (section 47) -- a direct SQL write cannot introduce an impossible score.
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_memory_confidence_range"
        ),
        CheckConstraint(
            "importance >= 0.0 AND importance <= 1.0", name="ck_memory_importance_range"
        ),
        Index("idx_mem_project_layer", "project_id", "layer"),
        Index("idx_mem_project_type", "project_id", "memory_type"),
        Index("idx_mem_project_status", "project_id", "status"),
        Index("idx_mem_project_scope", "project_id", "scope"),
        Index("idx_mem_project_conflict", "project_id", "conflict_group"),
    )


class MemoryEvidence(Base):
    __tablename__ = "memory_evidences"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Richer evidence taxonomy (section 5): evidence is more than file existence.
    evidence_type: Mapped[str] = mapped_column(
        String(50), default="CODE", server_default="CODE", nullable=False
    )
    # Negative evidence is retained, not discarded (section 5).
    relation: Mapped[str] = mapped_column(
        String(50), default="SUPPORTS", server_default="SUPPORTS", nullable=False
    )
    authority: Mapped[str] = mapped_column(
        String(50),
        default="CODE_VERIFIED",
        server_default="CODE_VERIFIED",
        nullable=False,
    )
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str] = mapped_column(
        String(50), default="UNCHECKED", server_default="UNCHECKED", nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    symbol_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snippet_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ast_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    memory: Mapped["Memory"] = relationship("Memory", back_populates="evidences")
    symbol: Mapped["CodeEntity | None"] = relationship(
        "CodeEntity", foreign_keys=[symbol_id]
    )

    __table_args__ = (
        Index("idx_ev_project", "project_id"),
        Index("idx_ev_memory", "memory_id"),
        Index("idx_ev_symbol", "symbol_id"),
        Index("idx_ev_file", "file_path"),
    )


class MemoryRelation(Base):
    __tablename__ = "memory_relations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    target_memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # supports, contradicts, supersedes, derived_from, related_to, invalidates
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_memrel_project", "project_id"),
        Index("idx_memrel_source", "source_memory_id"),
        Index("idx_memrel_target", "target_memory_id"),
    )


class MemoryVersion(Base):
    __tablename__ = "memory_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="system", nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    memory: Mapped["Memory"] = relationship("Memory", back_populates="versions")


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    token_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="agent_tasks")
    events: Mapped[list["AgentEvent"]] = relationship(
        "AgentEvent",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    test_runs: Mapped[list["TestRun"]] = relationship(
        "TestRun", back_populates="task", cascade="all, delete-orphan"
    )
    failure_episodes: Mapped[list["FailureEpisode"]] = relationship(
        "FailureEpisode", back_populates="task", cascade="all, delete-orphan"
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # observation, tool_call, code_change, test_result, failure, decision, fix, commit, review
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)

    task: Mapped["AgentTask"] = relationship("AgentTask", back_populates="events")


class Commit(Base):
    __tablename__ = "commits"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str] = mapped_column(String(100), default="main", nullable=False)
    author: Mapped[str] = mapped_column(String(255), default="unknown", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="commits")

    __table_args__ = (
        Index("idx_commit_project_sha", "project_id", "commit_sha", unique=True),
    )


class ChangeSet(Base):
    __tablename__ = "change_sets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    is_working_tree: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # sha256 over (project, base, target, working-tree flag, sorted file set).
    # Analysing the same change twice resolves to one logical ChangeSet (section 37).
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="change_sets")
    file_changes: Mapped[list["FileChange"]] = relationship(
        "FileChange",
        back_populates="change_set",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    symbol_changes: Mapped[list["SymbolChange"]] = relationship(
        "SymbolChange",
        back_populates="change_set",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_changeset_idempotency"
        ),
        Index(
            "idx_changeset_project_commits",
            "project_id",
            "base_commit_sha",
            "target_commit_sha",
        ),
    )


class FileChange(Base):
    __tablename__ = "file_changes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    change_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    old_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # ADDED, DELETED, MODIFIED, RENAMED, MOVED
    lines_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lines_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    change_set: Mapped["ChangeSet"] = relationship(
        "ChangeSet", back_populates="file_changes"
    )

    __table_args__ = (Index("idx_filechange_set_path", "change_set_id", "file_path"),)


class SymbolChange(Base):
    __tablename__ = "symbol_changes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    change_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # SYMBOL_ADDED, SYMBOL_REMOVED, SIGNATURE_CHANGED, BODY_CHANGED, DEPENDENCY_CHANGED, BEHAVIOUR_CHANGED, RENAMED, MOVED
    old_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_symbol_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    new_symbol_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    change_set: Mapped["ChangeSet"] = relationship(
        "ChangeSet", back_populates="symbol_changes"
    )
    old_symbol: Mapped["CodeEntity | None"] = relationship(
        "CodeEntity", foreign_keys=[old_symbol_id]
    )
    new_symbol: Mapped["CodeEntity | None"] = relationship(
        "CodeEntity", foreign_keys=[new_symbol_id]
    )

    __table_args__ = (
        Index("idx_symbolchange_set_qualified", "change_set_id", "qualified_name"),
    )


class TestRun(Base):
    __test__ = False
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    framework: Mapped[str] = mapped_column(String(50), default="pytest", nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="PASSED", nullable=False
    )  # PASSED, FAILED, ERROR
    total_tests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    environment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="test_runs")
    task: Mapped["AgentTask | None"] = relationship(
        "AgentTask", back_populates="test_runs"
    )
    results: Mapped[list["TestCaseResult"]] = relationship(
        "TestCaseResult",
        back_populates="test_run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("idx_testrun_project_commit", "project_id", "commit_sha"),)


class TestCaseResult(Base):
    __test__ = False
    __tablename__ = "test_case_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    test_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    suite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # PASSED, FAILED, SKIPPED, FLAKY
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_files: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=list, nullable=True
    )
    affected_symbols: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=list, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    test_run: Mapped["TestRun"] = relationship("TestRun", back_populates="results")
    failure_episodes: Mapped[list["FailureEpisode"]] = relationship(
        "FailureEpisode", back_populates="test_case_result"
    )

    __table_args__ = (
        Index("idx_testcase_run_status", "test_run_id", "status"),
        Index("idx_testcase_signature", "failure_signature"),
    )


class FailureEpisode(Base):
    __tablename__ = "failure_episodes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    test_case_result_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("test_case_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    error_class: Mapped[str] = mapped_column(
        String(100), default="Error", nullable=False
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_approach: Mapped[str] = mapped_column(Text, nullable=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_or_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_claim_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    root_cause_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    affected_files: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=list, nullable=True
    )
    affected_symbols: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=list, nullable=True
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship(
        "Project", back_populates="failure_episodes"
    )
    task: Mapped["AgentTask | None"] = relationship(
        "AgentTask", back_populates="failure_episodes"
    )
    test_case_result: Mapped["TestCaseResult | None"] = relationship(
        "TestCaseResult", back_populates="failure_episodes"
    )
    fix_attempts: Mapped[list["FixAttempt"]] = relationship(
        "FixAttempt",
        back_populates="failure_episode",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_fail_project_sig", "project_id", "failure_signature"),
        Index("idx_fail_root_cause_claim", "root_cause_claim_id"),
    )


class FixAttempt(Base):
    __tablename__ = "fix_attempts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    failure_episode_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("failure_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempted_fix: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    why_worked_or_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    failure_episode: Mapped["FailureEpisode"] = relationship(
        "FailureEpisode", back_populates="fix_attempts"
    )


class ArchitectureRule(Base):
    __tablename__ = "architecture_rules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Rule modality (section 13): MUST_NOT is the enforceable default, but MUST /
    # SHOULD / ONLY_IF / REQUIRES are representable and carried through evaluation.
    modality: Mapped[str] = mapped_column(
        String(20), default="MUST_NOT", server_default="MUST_NOT", nullable=False
    )
    # An LLM guess must not silently become a hard architectural rule (section 13).
    authority: Mapped[str] = mapped_column(
        String(50),
        default="USER_CONFIRMED",
        server_default="USER_CONFIRMED",
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(100), default="user", server_default="user", nullable=False
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    scope: Mapped[str] = mapped_column(String(50), default="PROJECT", nullable=False)
    severity: Mapped[str] = mapped_column(String(50), default="ERROR", nullable=False)
    forbidden_source_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    forbidden_target_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    enforcement_status: Mapped[str] = mapped_column(
        String(50), default="ACTIVE", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship(
        "Project", back_populates="architecture_rules"
    )
    violations: Mapped[list["RuleViolation"]] = relationship(
        "RuleViolation",
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RuleViolation(Base):
    __tablename__ = "rule_violations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("architecture_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    violation_details: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    rule: Mapped["ArchitectureRule"] = relationship(
        "ArchitectureRule", back_populates="violations"
    )
    source_entity: Mapped["CodeEntity"] = relationship(
        "CodeEntity", foreign_keys=[source_entity_id]
    )
    target_entity: Mapped["CodeEntity"] = relationship(
        "CodeEntity", foreign_keys=[target_entity_id]
    )

    __table_args__ = (
        Index("idx_violation_rule", "rule_id"),
        Index("idx_violation_active", "rule_id", "resolved_at"),
    )


class CodeReview(Base):
    __tablename__ = "code_reviews"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(
        String(100), default="cortex-reviewer", nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="APPROVED", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    comments: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="code_reviews")


class CognitiveSnapshot(Base):
    __tablename__ = "cognitive_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    cognitive_generation: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    memory_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    graph_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    index_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_memories_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    stale_memories_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    conflicted_memories_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    retrieval_version: Mapped[str] = mapped_column(
        String(50), default="v2", nullable=False
    )
    embedding_version: Mapped[str] = mapped_column(
        String(50), default="1.0.0", nullable=False
    )
    # Counts alone are not a cognitive state (section 35). These columns record the
    # actual believed set, so "what did CortexForge believe at commit X" is answered
    # from the snapshot rather than re-derived from present-day tables.
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # [{memory_id, version, status, confidence, layer, memory_type}, ...]
    memory_versions: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # [{claim_id, status, confidence, last_outcome}, ...]
    claim_states: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    architecture_rule_versions: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    verification_state: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    policy_versions: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    # sha256 over the believed set; two snapshots of an unchanged state match.
    state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship(
        "Project", back_populates="cognitive_snapshots"
    )

    __table_args__ = (Index("idx_cogsnap_project_commit", "project_id", "commit_sha"),)


# ---------------------------------------------------------------------------
# Claim / proposition layer (specification sections 4-7)
#
# A memory is prose. A claim is the individually evaluable proposition inside it.
# Verification, conflict detection, temporal validity and reconciliation all operate
# on claims, so that a memory containing one true and one falsified statement has a
# representable state instead of being atomically "active" or "stale".
# ---------------------------------------------------------------------------


class Claim(Base):
    """An independently evaluable proposition extracted from a memory."""

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=True
    )
    # Human-readable proposition, normalized to one assertion.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Canonical form used for logical deduplication (section 25): lowercased,
    # stop-worded, synonym-folded, sorted token signature.
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256(project_id + scope + scope_ref + canonical_text). Two differently
    # phrased memories asserting the same thing collapse onto one claim_key.
    claim_key: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    predicate: Mapped[str | None] = mapped_column(Text, nullable=True)
    epistemic_state: Mapped[str] = mapped_column(
        String(50), default="OBSERVATION", server_default="OBSERVATION", nullable=False
    )
    authority: Mapped[str] = mapped_column(
        String(50),
        default="AGENT_OBSERVED",
        server_default="AGENT_OBSERVED",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="PROPOSED", server_default="PROPOSED", nullable=False
    )
    scope: Mapped[str] = mapped_column(
        String(50), default="PROJECT", server_default="PROJECT", nullable=False
    )
    scope_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence_components: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    # Temporal validity (section 18): a claim true at commit A and false at commit B
    # is historical evolution, not a contradiction.
    valid_from_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_to_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    memory: Mapped["Memory | None"] = relationship("Memory", back_populates="claims")
    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(
        "ClaimEvidence",
        back_populates="claim",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # One live claim per logical proposition per branch/validity window.
        # Enforced at the database level so deduplication cannot be bypassed by a
        # caller that forgets to look first (section 47).
        UniqueConstraint(
            "project_id",
            "claim_key",
            "branch",
            "valid_to_commit",
            name="uq_claim_logical_identity",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_claim_confidence_range"
        ),
        Index("idx_claim_project_status", "project_id", "status"),
        Index("idx_claim_memory", "memory_id"),
        Index("idx_claim_key", "project_id", "claim_key"),
    )


class ClaimEvidence(Base):
    """A single piece of evidence bearing on a claim, positive or negative."""

    __tablename__ = "claim_evidences"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    relation: Mapped[str] = mapped_column(
        String(50), default="SUPPORTS", server_default="SUPPORTS", nullable=False
    )
    authority: Mapped[str] = mapped_column(
        String(50),
        default="AGENT_OBSERVED",
        server_default="AGENT_OBSERVED",
        nullable=False,
    )
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    qualified_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ast_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # sha256 over the identifying tuple; makes re-observation idempotent (section 37).
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(50), default="UNCHECKED", server_default="UNCHECKED", nullable=False
    )
    # Independence group: evidence items sharing a group are not counted as
    # independent corroboration when scoring confidence (section 8).
    independence_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    claim: Mapped["Claim"] = relationship("Claim", back_populates="evidence_links")
    symbol: Mapped["CodeEntity | None"] = relationship(
        "CodeEntity", foreign_keys=[symbol_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "claim_id", "evidence_hash", name="uq_claim_evidence_fingerprint"
        ),
        Index("idx_claimev_claim_relation", "claim_id", "relation"),
        Index("idx_claimev_file", "file_path"),
        Index("idx_claimev_symbol", "symbol_id"),
    )


class VerificationPolicy(Base):
    """A named, versioned strategy for deciding whether a claim holds."""

    __tablename__ = "verification_policies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Strategy identifier resolved by the verification engine's registry.
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    # Which claims this policy applies to; NULL means any.
    applies_to_evidence_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    applies_to_epistemic_state: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    minimum_authority: Mapped[str] = mapped_column(
        String(50),
        default="AGENT_OBSERVED",
        server_default="AGENT_OBSERVED",
        nullable=False,
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", "version", name="uq_verification_policy_version"
        ),
    )


class VerificationRun(Base):
    """One execution of verification over a set of claims at a point in history."""

    __tablename__ = "verification_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # sha256 over (project, commit, branch, workspace, policy set, claim set).
    # Re-running verification for an unchanged state reuses the run instead of
    # manufacturing new evidence of freshness (sections 8 and 37).
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verifier: Mapped[str] = mapped_column(String(100), default="system", nullable=False)
    trigger: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="RUNNING", nullable=False)
    claims_evaluated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    partially_verified_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    not_applicable_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    results: Mapped[list["VerificationResult"]] = relationship(
        "VerificationResult",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_verification_run_idempotency"
        ),
        Index("idx_verrun_project_commit", "project_id", "commit_sha"),
    )


class VerificationResult(Base):
    """The outcome of evaluating one claim under one policy, with its reasoning."""

    __tablename__ = "verification_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("verification_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_name: Mapped[str] = mapped_column(
        String(150), default="unknown", nullable=False
    )
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_checked: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verifier: Mapped[str] = mapped_column(String(100), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    run: Mapped["VerificationRun"] = relationship(
        "VerificationRun", back_populates="results"
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id", "claim_id", "policy_name", name="uq_verification_result_unique"
        ),
        Index("idx_verres_claim_created", "claim_id", "created_at"),
        Index("idx_verres_outcome", "outcome"),
    )


class MemoryDecision(Base):
    """An auditable reconciliation decision (specification section 9).

    Every keep / reanchor / amend / revise / stale / conflict / supersede /
    invalidate / unknown outcome is persisted with its reason code, the change that
    triggered it, the evidence consulted, and the memory versions on both sides --
    so the reasoning can be audited, replayed and benchmarked rather than inferred.
    """

    __tablename__ = "memory_decisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    change_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("change_sets.id", ondelete="SET NULL"), nullable=True
    )
    verification_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("verification_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workspace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor: Mapped[str] = mapped_column(
        String(100), default="reconciliation", nullable=False
    )
    # sha256 over (memory, change_set, decision, reason_code, commit). Replaying the
    # same change produces the same decision row rather than a duplicate.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_memory_decision_idempotency"
        ),
        Index("idx_memdec_project_created", "project_id", "created_at"),
        Index("idx_memdec_memory", "memory_id"),
        Index("idx_memdec_changeset", "change_set_id"),
    )


class SuccessEpisode(Base):
    """A recorded approach that worked (specification section 17).

    CortexForge must learn from what succeeded, not only from what failed, so that a
    future similar task can retrieve a known-good approach.
    """

    __tablename__ = "success_episodes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    failure_episode_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("failure_episodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    task_context: Mapped[str] = mapped_column(Text, nullable=False)
    approach: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_files: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    affected_symbols: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    test_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    tests_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # sha256 over (project, task, normalized approach) so replaying an event stream
    # records one logical success, not one per delivery.
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "signature", name="uq_success_episode_signature"
        ),
        Index("idx_success_project_created", "project_id", "created_at"),
    )


class RetrievalEvent(Base):
    """What retrieval returned, what the agent used, and how the task turned out.

    Specification section 27: without this record, retrieval policies cannot be
    evaluated and memory usefulness cannot be learned.
    """

    __tablename__ = "retrieval_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    returned_memory_ids: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    selected_memory_ids: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    used_memory_ids: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False
    )
    excluded_reasons: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    stale_returned_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    conflicted_returned_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    task_outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tests_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    retrieval_version: Mapped[str] = mapped_column(
        String(50), default="v2", nullable=False
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    memory_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_retrievalev_project_created", "project_id", "created_at"),
        Index("idx_retrievalev_task", "task_id"),
    )


class AuditLog(Base):
    """System-wide audit trail for cognitively significant actions (section 44)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(150), default="system", nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_audit_project_created", "project_id", "created_at"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )


class WebhookDelivery(Base):
    """Ledger of processed inbound webhook deliveries (sections 33 and 37).

    Keyed on the provider's delivery identifier so a redelivered event is recognised
    and produces no second cognitive change.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    provider: Mapped[str] = mapped_column(String(50), default="github", nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(150), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery"),
    )


class SymbolLineage(Base):
    """Durable identity for a logical symbol across renames and moves (section 11).

    ``logical_id`` is stable: when ``foo()`` in ``a.py`` becomes ``bar()`` in
    ``b.py``, a new row is written with the same ``logical_id`` rather than the old
    symbol being deleted and a new one created.
    """

    __tablename__ = "symbol_lineages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    logical_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_kind: Mapped[str] = mapped_column(
        String(50), default="OBSERVED", server_default="OBSERVED", nullable=False
    )
    predecessor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("symbol_lineages.id", ondelete="SET NULL"), nullable=True
    )
    valid_from_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_to_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "logical_id",
            "qualified_name",
            "file_path",
            "valid_from_commit",
            name="uq_symbol_lineage_step",
        ),
        Index("idx_lineage_project_logical", "project_id", "logical_id"),
        Index("idx_lineage_project_qualified", "project_id", "qualified_name"),
    )


class Job(Base):
    """A durable background job (specification sections 38 and 39).

    Jobs used to live in an in-process dictionary, so a restart mid-index left no
    trace that indexing had been happening: the work was neither finished nor
    recoverable, and nothing could tell the difference between "never started"
    and "died halfway through". Persisting them makes both answerable.

    Recovery works through leases rather than timestamps alone. A worker claims a
    job by writing a lease that expires; if the worker dies, the lease lapses and
    another worker can pick the job up. A job that simply took a long time keeps
    renewing its lease and is not stolen.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="PENDING", server_default="PENDING", nullable=False
    )
    # sha256 over (project, type, parameters). Submitting the same work twice
    # while it is still outstanding returns the existing job rather than running
    # it again (section 37).
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Which stage the job reached, so a resumed job can continue rather than
    # restart. A half-applied cognitive update is worse than an unstarted one.
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Identity of the worker currently holding this job, and when its claim
    # expires. A lapsed lease is what makes a crashed job recoverable.
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_idempotency"),
        Index("idx_job_status_type", "status", "job_type"),
        Index("idx_job_project", "project_id"),
        Index("idx_job_lease", "status", "lease_expires_at"),
    )


class HumanApprovalRecord(Base):
    """Server-side human authorization record for sensitive mutations (§29)."""

    __tablename__ = "human_approval_records"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="PENDING", nullable=False
    )  # PENDING, APPROVED, REJECTED, EXPIRED, CONSUMED
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_approval_project", "project_id"),
        Index("idx_approval_token", "token"),
        Index("idx_approval_digest", "payload_digest"),
    )

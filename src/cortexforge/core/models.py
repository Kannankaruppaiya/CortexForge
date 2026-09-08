"""SQLAlchemy 2.0 Declarative Models for CortexForge."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
    default_branch: Mapped[str] = mapped_column(String(100), default="main", nullable=False)
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
        "Commit", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    change_sets: Mapped[list["ChangeSet"]] = relationship(
        "ChangeSet", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    test_runs: Mapped[list["TestRun"]] = relationship(
        "TestRun", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    failure_episodes: Mapped[list["FailureEpisode"]] = relationship(
        "FailureEpisode", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    architecture_rules: Mapped[list["ArchitectureRule"]] = relationship(
        "ArchitectureRule", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    cognitive_snapshots: Mapped[list["CognitiveSnapshot"]] = relationship(
        "CognitiveSnapshot", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    code_reviews: Mapped[list["CodeReview"]] = relationship(
        "CodeReview", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
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
    source: Mapped[str] = mapped_column(String(50), default="tree_sitter", nullable=False)
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
        Index("idx_rel_project_source", "project_id", "source_entity_id", "relationship_type"),
        Index("idx_rel_project_target", "project_id", "target_entity_id", "relationship_type"),
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
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0", nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(50), default="code", nullable=False
    )  # code, git, test, agent_observation, doc, user
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system", nullable=False)
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
    valid_from_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        "MemoryEvidence", back_populates="memory", cascade="all, delete-orphan", lazy="selectin"
    )
    versions: Mapped[list["MemoryVersion"]] = relationship(
        "MemoryVersion", back_populates="memory", cascade="all, delete-orphan", lazy="selectin"
    )
    supersedes: Mapped["Memory | None"] = relationship(
        "Memory", foreign_keys=[supersedes_id], remote_side=[id], post_update=True
    )
    superseded_by: Mapped["Memory | None"] = relationship(
        "Memory", foreign_keys=[superseded_by_id], remote_side=[id], post_update=True
    )

    __table_args__ = (
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
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
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
    symbol: Mapped["CodeEntity | None"] = relationship("CodeEntity", foreign_keys=[symbol_id])

    __table_args__ = (
        Index("idx_ev_memory", "memory_id"),
        Index("idx_ev_symbol", "symbol_id"),
        Index("idx_ev_file", "file_path"),
    )


class MemoryRelation(Base):
    __tablename__ = "memory_relations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
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

    project: Mapped["Project"] = relationship("Project", back_populates="agent_tasks")
    events: Mapped[list["AgentEvent"]] = relationship(
        "AgentEvent", back_populates="task", cascade="all, delete-orphan", lazy="selectin"
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
    is_working_tree: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="change_sets")
    file_changes: Mapped[list["FileChange"]] = relationship(
        "FileChange", back_populates="change_set", cascade="all, delete-orphan", lazy="selectin"
    )
    symbol_changes: Mapped[list["SymbolChange"]] = relationship(
        "SymbolChange", back_populates="change_set", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("idx_changeset_project_commits", "project_id", "base_commit_sha", "target_commit_sha"),
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

    change_set: Mapped["ChangeSet"] = relationship("ChangeSet", back_populates="file_changes")

    __table_args__ = (
        Index("idx_filechange_set_path", "change_set_id", "file_path"),
    )


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

    change_set: Mapped["ChangeSet"] = relationship("ChangeSet", back_populates="symbol_changes")
    old_symbol: Mapped["CodeEntity | None"] = relationship("CodeEntity", foreign_keys=[old_symbol_id])
    new_symbol: Mapped["CodeEntity | None"] = relationship("CodeEntity", foreign_keys=[new_symbol_id])

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
    task: Mapped["AgentTask | None"] = relationship("AgentTask", back_populates="test_runs")
    results: Mapped[list["TestCaseResult"]] = relationship(
        "TestCaseResult", back_populates="test_run", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("idx_testrun_project_commit", "project_id", "commit_sha"),
    )


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
    affected_files: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=list, nullable=True)
    affected_symbols: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=list, nullable=True)
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
        String(36), ForeignKey("test_case_results.id", ondelete="SET NULL"), nullable=True
    )
    failure_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    error_class: Mapped[str] = mapped_column(String(100), default="Error", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_approach: Mapped[str] = mapped_column(Text, nullable=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_or_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_files: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=list, nullable=True)
    affected_symbols: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=list, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="failure_episodes")
    task: Mapped["AgentTask | None"] = relationship("AgentTask", back_populates="failure_episodes")
    test_case_result: Mapped["TestCaseResult | None"] = relationship(
        "TestCaseResult", back_populates="failure_episodes"
    )
    fix_attempts: Mapped[list["FixAttempt"]] = relationship(
        "FixAttempt", back_populates="failure_episode", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("idx_fail_project_sig", "project_id", "failure_signature"),
    )


class FixAttempt(Base):
    __tablename__ = "fix_attempts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    failure_episode_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("failure_episodes.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempted_fix: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    why_worked_or_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    failure_episode: Mapped["FailureEpisode"] = relationship("FailureEpisode", back_populates="fix_attempts")


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
    scope: Mapped[str] = mapped_column(String(50), default="PROJECT", nullable=False)
    severity: Mapped[str] = mapped_column(String(50), default="ERROR", nullable=False)
    forbidden_source_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    forbidden_target_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    enforcement_status: Mapped[str] = mapped_column(String(50), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="architecture_rules")
    violations: Mapped[list["RuleViolation"]] = relationship(
        "RuleViolation", back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )


class RuleViolation(Base):
    __tablename__ = "rule_violations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    rule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("architecture_rules.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("code_entities.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    violation_details: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    rule: Mapped["ArchitectureRule"] = relationship("ArchitectureRule", back_populates="violations")
    source_entity: Mapped["CodeEntity"] = relationship("CodeEntity", foreign_keys=[source_entity_id])
    target_entity: Mapped["CodeEntity"] = relationship("CodeEntity", foreign_keys=[target_entity_id])


class CodeReview(Base):
    __tablename__ = "code_reviews"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), default="cortex-reviewer", nullable=False)
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
    cognitive_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    memory_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    graph_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    index_generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_memories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stale_memories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicted_memories_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(50), default="v2", nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(50), default="1.0.0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="cognitive_snapshots")

    __table_args__ = (
        Index("idx_cogsnap_project_commit", "project_id", "commit_sha"),
    )

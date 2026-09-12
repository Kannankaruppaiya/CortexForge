"""Pydantic v2 Schemas for CortexForge API & Domain Contracts."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CognitiveLayer(str, Enum):
    L0 = "L0"  # Project Identity
    L1 = "L1"  # Structural Architecture
    L2 = "L2"  # Conventions & Patterns
    L3 = "L3"  # Decisions & Rationale
    L4 = "L4"  # Failures & Post-Mortems
    L5 = "L5"  # Durable Lessons
    L6 = "L6"  # Active Working State


class MemoryStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    UNVERIFIED = "UNVERIFIED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"
    ARCHIVED = "ARCHIVED"


class MemoryType(str, Enum):
    FACT = "FACT"
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    EPISODE = "EPISODE"
    FAILURE = "FAILURE"
    FIX = "FIX"
    ARCHITECTURE = "ARCHITECTURE"
    PATTERN = "PATTERN"
    CONVENTION = "CONVENTION"
    INVARIANT = "INVARIANT"
    GOAL = "GOAL"
    LESSON = "LESSON"
    WARNING = "WARNING"
    TASK_STATE = "TASK_STATE"
    SKILL = "SKILL"


class ProjectBase(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255, description="Project or repository name"
    )
    source_type: str = Field("LOCAL", description="LOCAL, GITHUB, or GIT_URL")
    repository_url: str | None = Field(None, description="Remote Git repository URL")
    clone_url: str | None = Field(None, description="Git clone URL")
    github_repository_id: str | None = Field(None, description="GitHub repository ID")
    github_owner: str | None = Field(None, description="GitHub repository owner")
    github_repo: str | None = Field(None, description="GitHub repository name")
    managed_workspace: bool = Field(
        False, description="Whether project lives in managed workspace"
    )
    local_path: str | None = Field(
        None,
        description="Filesystem path to repository (required for LOCAL, server-derived for managed)",
    )
    default_branch: str = Field(
        "main", min_length=1, max_length=100, description="Default git branch"
    )
    language: str | None = Field(
        None, max_length=50, description="Primary detected programming language"
    )


class ProjectCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255, description="Project or repository name"
    )
    source_type: str = Field(
        "LOCAL",
        pattern="^(LOCAL|GITHUB|GIT_URL)$",
        description="LOCAL, GITHUB, or GIT_URL",
    )
    local_path: str | None = Field(
        None, description="Absolute local filesystem path to repository (for LOCAL)"
    )
    clone_url: str | None = Field(None, description="Git clone URL (for GIT_URL)")
    repository_url: str | None = Field(None, description="Remote repository URL")
    github_repository_id: str | None = Field(
        None, description="GitHub repository ID (for GITHUB)"
    )
    github_owner: str | None = Field(
        None, description="GitHub repository owner (for GITHUB)"
    )
    github_repo: str | None = Field(
        None, description="GitHub repository name (for GITHUB)"
    )
    default_branch: str = Field(
        "main", min_length=1, max_length=100, description="Default git branch"
    )
    language: str | None = Field(
        None, max_length=50, description="Primary programming language"
    )

    @model_validator(mode="after")
    def validate_source_fields(self) -> "ProjectCreate":
        src = self.source_type.upper()
        if src == "LOCAL":
            if not self.local_path or not self.local_path.strip():
                raise ValueError("local_path is required when source_type is LOCAL.")
        elif src == "GIT_URL":
            if not self.clone_url or not self.clone_url.strip():
                raise ValueError("clone_url is required when source_type is GIT_URL.")
        elif (
            src == "GITHUB"
            and not self.github_repo
            and not self.repository_url
            and not self.clone_url
        ):
            raise ValueError(
                "github_repo, repository_url, or clone_url is required when source_type is GITHUB."
            )
        return self


class ProjectUpdate(BaseModel):
    name: str | None = None
    default_branch: str | None = None
    language: str | None = None
    status: str | None = None


class ProjectRead(ProjectBase):
    id: str
    owner_user_id: str | None = None
    last_indexed_commit: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    file_count: int | None = 0
    entity_count: int | None = 0
    memory_count: int | None = 0
    initial_job_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class LocalRepoValidationRequest(BaseModel):
    path: str | None = Field(
        None, max_length=1024, description="Candidate local repository path"
    )
    local_path: str | None = Field(
        None, max_length=1024, description="Alternative candidate local repository path"
    )

    @model_validator(mode="after")
    def validate_path(self) -> "LocalRepoValidationRequest":
        effective = self.path or self.local_path
        if not effective or not effective.strip():
            raise ValueError("path or local_path is required")
        self.path = effective.strip()
        return self


class LocalRepoValidationResponse(BaseModel):
    valid: bool
    is_git: bool
    path: str
    default_branch: str | None = None
    detected_language: str | None = None
    languages: dict[str, float] | None = None
    error: str | None = None
    # Set when the path is already registered as a CortexForge project
    existing_project_id: str | None = None
    existing_project_name: str | None = None


class DirectoryEntry(BaseModel):
    name: str
    path: str
    is_dir: bool = True
    is_git: bool = False


class DirectoryBrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None = None
    workspace_root: str
    directories: list[DirectoryEntry]
    is_windows: bool = False
    is_drive_root: bool = False
    error: str | None = None


class GitHubRepoItem(BaseModel):
    id: str
    name: str
    full_name: str
    owner: str
    default_branch: str = "main"
    description: str | None = None
    private: bool = False
    clone_url: str
    language: str | None = None


class ProjectMembershipCreate(BaseModel):
    user_id: str
    role: str = "MEMBER"  # OWNER, ADMIN, MEMBER, VIEWER


class ProjectMembershipUpdate(BaseModel):
    role: str


class ProjectMembershipRead(BaseModel):
    id: str
    user_id: str
    project_id: str
    role: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectOwnershipTransferRequest(BaseModel):
    new_owner_user_id: str
    previous_owner_role: str = "ADMIN"


class ProjectOwnershipTransferResponse(BaseModel):
    project_id: str
    previous_owner_user_id: str
    new_owner_user_id: str
    message: str


class AgentCredentialCreate(BaseModel):
    name: str = "default"
    expires_in_days: int | None = None


class AgentCredentialRead(BaseModel):
    id: str
    agent_id: str
    key_id: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentCredentialSecretResponse(AgentCredentialRead):
    raw_api_key: str  # Only returned once upon generation!


class CodeEntityRead(BaseModel):
    id: str
    project_id: str
    entity_type: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    content_hash: str
    language: str
    entity_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RelationshipRead(BaseModel):
    id: str
    project_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    confidence: float
    source: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ComponentSummary(BaseModel):
    name: str
    qualified_name: str
    entity_type: str
    file_path: str
    line_range: list[int]
    signature: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)


class ModuleSummary(BaseModel):
    module_path: str
    file_count: int
    entity_count: int
    top_level_components: list[ComponentSummary] = Field(default_factory=list)


class ArchitectureResponse(BaseModel):
    project_id: str
    project_name: str
    total_files: int
    total_entities: int
    total_relationships: int
    languages: list[str]
    modules: list[ModuleSummary]
    primary_apis: list[ComponentSummary] = Field(default_factory=list)
    primary_models: list[ComponentSummary] = Field(default_factory=list)


class ScanRequest(BaseModel):
    incremental: bool = Field(
        True, description="Whether to scan only files changed since last scan"
    )
    max_files: int | None = Field(
        None, description="Optional limit on number of files to process"
    )


class ScanResponse(BaseModel):
    project_id: str
    files_scanned: int
    entities_extracted: int
    relationships_extracted: int
    graph_generation: int = 1
    duration_ms: float
    status: str
    errors: list[str] = Field(default_factory=list)


class MemoryEvidenceRead(BaseModel):
    id: str
    source_type: str
    source_id: str | None = None
    file_path: str | None = None
    uri: str | None = None
    kind: str | None = None
    evidence_type: str = "CODE"
    relation: str = "SUPPORTS"
    authority: str = "AGENT_OBSERVED"
    symbol_id: str | None = None
    commit_sha: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    evidence_hash: str
    snippet_hash: str | None = None
    ast_fingerprint: str | None = None
    confidence: float
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryEvidenceCreate(BaseModel):
    source_type: str = "code"
    source_id: str | None = None
    file_path: str | None = None
    uri: str | None = None
    kind: str | None = None
    evidence_type: str = "CODE"
    relation: str = "SUPPORTS"
    authority: str | None = None
    symbol_id: str | None = None
    commit_sha: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    evidence_hash: str | None = None
    snippet_hash: str | None = None
    ast_fingerprint: str | None = None
    confidence: float = 1.0
    detail: dict[str, Any] = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    layer: str = Field("L1", description="Cognitive layer L0, L1, L2, L3, L4, L5, L6")
    memory_type: str = Field(
        ..., description="FACT, DECISION, CONSTRAINT, EPISODE, FAILURE, FIX, etc."
    )
    title: str = Field(..., max_length=255)
    content: str
    summary: str
    importance: float = Field(0.5, ge=0.0, le=1.0)
    confidence: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        deprecated=True,
        description=(
            "Ignored on write. Confidence is derived from authority, evidence and "
            "verification outcome so that a caller cannot assert its own certainty; "
            "the field is retained only for request-shape compatibility."
        ),
    )
    freshness_score: float = Field(1.0, ge=0.0, le=1.0)
    source_type: str = "code"
    authority: str | None = Field(
        None,
        description=(
            "Explicit authority level (USER_CONFIRMED, REVIEW_CONFIRMED, "
            "TEST_VERIFIED, CODE_VERIFIED, GIT_DERIVED, AGENT_OBSERVED, "
            "LLM_GENERATED, REPOSITORY_TEXT, UNTRUSTED). Derived from source_type "
            "when omitted."
        ),
    )
    scope: str = Field(
        "PROJECT",
        description="PROJECT, MODULE, FILE, SYMBOL, FEATURE, TASK, BRANCH, ENVIRONMENT",
    )
    branch: str | None = None
    workspace: str | None = None
    is_working_tree: bool = Field(
        False,
        description="True if observed from an uncommitted working tree rather than committed truth",
    )
    source_reference: str | None = None
    source_commit: str | None = None
    created_by: str = "agent"
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    conflict_group: str | None = None
    valid_from_commit: str | None = None
    valid_to_commit: str | None = None
    valid_from_time: datetime | None = None
    valid_to_time: datetime | None = None
    evidence: list[MemoryEvidenceCreate] | None = None


class MemoryRead(BaseModel):
    id: str
    project_id: str
    layer: str = "L1"
    memory_type: str
    title: str
    content: str
    summary: str
    status: str
    confidence: float
    importance: float
    freshness_score: float = 1.0
    source_type: str
    authority: str = "AGENT_OBSERVED"
    epistemic_state: str = "OBSERVATION"
    scope: str = "PROJECT"
    branch: str | None = None
    workspace: str | None = None
    is_working_tree: bool = False
    source_reference: str | None = None
    source_commit: str | None = None
    valid_from_commit: str | None = None
    valid_to_commit: str | None = None
    valid_from_time: datetime | None = None
    valid_to_time: datetime | None = None
    created_by: str
    version: int
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    conflict_group: str | None = None
    created_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None = None
    evidences: list[MemoryEvidenceRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MemoryVerifyRequest(BaseModel):
    verify_source_code: bool = True
    verify_tests: bool = False


class ChangeImpactRequest(BaseModel):
    modified_files: list[str]
    mark_stale: bool = False


class ChangeImpactResponse(BaseModel):
    project_id: str
    modified_files: list[str]
    directly_changed_entities: list[str]
    affected_dependents: list[str]
    memories_flagged_stale: list[str]
    critical_constraints: list[str]
    warnings: list[str]


class HealthResponse(BaseModel):
    status: str = "healthy"
    database_connected: bool
    version: str = "0.1.0"
    timestamp: datetime


class CognitiveSnapshotRead(BaseModel):
    id: str
    project_id: str
    commit_sha: str
    cognitive_generation: int
    graph_generation: int
    memory_generation: int
    index_generation: int
    retrieval_version: str
    embedding_version: str
    snapshot_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleModality(str, Enum):
    MUST = "MUST"
    MUST_NOT = "MUST_NOT"
    SHOULD = "SHOULD"
    ONLY_IF = "ONLY_IF"
    REQUIRES = "REQUIRES"


class ArchitectureRuleCreate(BaseModel):
    rule_name: str = Field(..., max_length=255)
    description: str = Field(..., description="Description or rationale for the rule")
    modality: str = "MUST_NOT"
    authority: str = "USER_CONFIRMED"
    source: str = "user"
    evidence: list[Any] = Field(default_factory=list)
    version: int = 1
    scope: str = "PROJECT"
    severity: str = "ERROR"
    forbidden_source_pattern: str
    forbidden_target_pattern: str
    enforcement_status: str = "ACTIVE"


class ArchitectureRuleRead(BaseModel):
    id: str
    project_id: str
    rule_name: str
    description: str
    modality: str = "MUST_NOT"
    authority: str = "USER_CONFIRMED"
    source: str = "user"
    evidence: list[Any] = Field(default_factory=list)
    version: int = 1
    scope: str = "PROJECT"
    severity: str = "ERROR"
    forbidden_source_pattern: str
    forbidden_target_pattern: str
    enforcement_status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleViolationRead(BaseModel):
    id: str
    rule_id: str
    source_entity_id: str
    target_entity_id: str
    commit_sha: str | None = None
    violation_details: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProvenanceTraceRead(BaseModel):
    memory_id: str
    title: str
    layer: str
    memory_type: str
    status: str
    confidence: float
    why_cortexforge_believes_this: str
    evidences: list[dict[str, Any]] = Field(default_factory=list)
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    versions: list[dict[str, Any]] = Field(default_factory=list)
    tests: list[dict[str, Any]] = Field(default_factory=list)


class TestCaseResultRead(BaseModel):
    id: str
    test_run_id: str
    test_name: str
    suite: str | None = None
    status: str
    duration_ms: float = 0.0
    error_message: str | None = None
    failure_signature: str | None = None
    is_flaky: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestRunRead(BaseModel):
    id: str
    project_id: str
    task_id: str | None = None
    commit_sha: str | None = None
    framework: str
    environment: str | None = None
    status: str
    total_tests: int
    passed_count: int
    failed_count: int
    duration_ms: float
    created_at: datetime
    results: list[TestCaseResultRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class FixAttemptRead(BaseModel):
    id: str
    failure_episode_id: str
    commit_sha: str | None = None
    attempted_fix: str
    success: bool
    why_worked_or_failed: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FailureEpisodeRead(BaseModel):
    id: str
    project_id: str
    task_id: str | None = None
    test_case_result_id: str | None = None
    commit_sha: str | None = None
    failure_signature: str
    error_class: str
    error_message: str
    normalized_trace: str | None = None
    attempted_approach: str
    rejected_reason: str | None = None
    command_or_tool: str | None = None
    root_cause: str | None = None
    root_cause_claim_id: str | None = None
    root_cause_details: dict[str, Any] = Field(default_factory=dict)
    affected_files: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    created_at: datetime
    fix_attempts: list[FixAttemptRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# Authentication, User & Agent Schemas (Individual-User-First Model)
# ==============================================================================


class UserRead(BaseModel):
    id: str
    email: str
    email_verified_at: datetime | None = None
    display_name: str | None = None
    github_user_id: str | None = None
    github_login: str | None = None
    avatar_url: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=255, description="Valid user email address")
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Account password (min 8 characters)",
    )
    display_name: str | None = Field(
        None, max_length=100, description="Optional user display name"
    )


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., max_length=128)


class OTPRequest(BaseModel):
    email: str = Field(
        ..., max_length=255, description="Email to send one-time code to"
    )


class OTPVerifyRequest(BaseModel):
    email: str = Field(..., max_length=255)
    otp: str = Field(
        ..., min_length=6, max_length=6, description="6-digit verification code"
    )


class PasswordResetRequest(BaseModel):
    email: str = Field(..., max_length=255)


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., description="Cryptographic password reset token")
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class SessionRead(BaseModel):
    id: str
    user_id: str
    user_agent: str | None = None
    ip_address: str | None = None
    expires_at: datetime
    created_at: datetime
    is_current: bool = False

    model_config = ConfigDict(from_attributes=True)


class AgentCreate(BaseModel):
    name: str = Field(
        ...,
        max_length=100,
        description="Friendly agent name, e.g., Claude Agent, Codex Agent",
    )
    type: str = Field(
        "custom", max_length=50, description="Agent type or model provider"
    )


class AgentRead(BaseModel):
    id: str
    owner_user_id: str
    name: str
    type: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentCreatedResponse(BaseModel):
    agent: AgentRead
    api_key: str = Field(..., description="One-time visible agent API key")


class AgentPermissionGrant(BaseModel):
    agent_id: str
    project_id: str
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_in_seconds: int | None = None


class AgentPermissionRead(BaseModel):
    id: str
    agent_id: str
    project_id: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

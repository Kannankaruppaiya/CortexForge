"""Pydantic v2 Schemas for CortexForge API & Domain Contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    name: str = Field(..., max_length=255, description="Project or repository name")
    repository_url: str | None = Field(None, description="Remote Git repository URL")
    local_path: str = Field(..., description="Absolute local filesystem path to repository")
    default_branch: str = Field("main", max_length=100, description="Default git branch")
    language: str | None = Field(None, max_length=50, description="Primary detected programming language")


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = None
    default_branch: str | None = None
    language: str | None = None
    status: str | None = None


class ProjectRead(ProjectBase):
    id: str
    last_indexed_commit: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    file_count: int | None = 0
    entity_count: int | None = 0
    memory_count: int | None = 0

    model_config = ConfigDict(from_attributes=True)


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
    incremental: bool = Field(True, description="Whether to scan only files changed since last scan")
    max_files: int | None = Field(None, description="Optional limit on number of files to process")


class ScanResponse(BaseModel):
    project_id: str
    files_scanned: int
    entities_extracted: int
    relationships_extracted: int
    duration_ms: float
    status: str
    errors: list[str] = Field(default_factory=list)


class MemoryEvidenceRead(BaseModel):
    id: str
    source_type: str
    source_id: str | None = None
    file_path: str
    commit_sha: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    evidence_hash: str
    confidence: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryEvidenceCreate(BaseModel):
    source_type: str = "code"
    source_id: str | None = None
    file_path: str
    commit_sha: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    evidence_hash: str | None = None
    confidence: float = 1.0


class MemoryCreate(BaseModel):
    memory_type: str = Field(..., description="FACT, DECISION, CONSTRAINT, EPISODE, FAILURE, etc.")
    title: str = Field(..., max_length=255)
    content: str
    summary: str
    importance: float = Field(0.5, ge=0.0, le=1.0)
    source_type: str = "code"
    source_reference: str | None = None
    created_by: str = "agent"
    evidence: list[MemoryEvidenceCreate] | None = None


class MemoryRead(BaseModel):
    id: str
    project_id: str
    memory_type: str
    title: str
    content: str
    summary: str
    status: str
    confidence: float
    importance: float
    source_type: str
    source_reference: str | None = None
    created_by: str
    version: int
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


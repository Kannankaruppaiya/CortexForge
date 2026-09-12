"""Configuration and contract changes must reach memory (specification section 14).

The evidence types for configuration, schemas and API contracts existed, and the
verification engine had a policy that checks them, but nothing produced such
evidence -- so both were mechanisms with no inputs. These tests assert the loop is
now closed: a configuration file is indexed, a memory can be grounded in one
specific key, and removing that key invalidates that memory without disturbing
memories grounded in keys that survived.

That last property is the one worth having. A change-impact system that
invalidated every memory touching a modified file would be technically correct
and practically useless.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.config_intelligence import (
    ConfigIntelligenceProvider,
    classify,
    discover_config_files,
)
from cortexforge.code_intelligence.scanner import (
    DEFAULT_IGNORED_DIRS,
    RepositoryScanner,
)
from cortexforge.cognition.epistemics import EvidenceType
from cortexforge.core.models import CodeEntity, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService

ENV_V1 = """# Service configuration
DATABASE_URL=postgresql://localhost/app
REDIS_URL=redis://localhost:6379/0
FEATURE_NEW_CHECKOUT=true
"""

ENV_V2_REDIS_REMOVED = """# Service configuration
DATABASE_URL=postgresql://localhost/app
FEATURE_NEW_CHECKOUT=true
"""

COMPOSE = """version: "3.9"
services:
  api:
    image: app:latest
    ports:
      - "8000:8000"
  cache:
    image: redis:7
"""

OPENAPI = """{
  "openapi": "3.0.0",
  "info": {"title": "App", "version": "1.0.0"},
  "paths": {
    "/users": {"get": {"summary": "List users"}},
    "/users/{id}": {"delete": {"summary": "Delete a user"}}
  }
}
"""

MIGRATION = '''"""add email column

Revision ID: abc123
"""
from alembic import op
import sqlalchemy as sa

revision = "abc123"


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255)))
    op.create_index("idx_users_email", "users", ["email"])
'''


@pytest_asyncio.fixture
async def config_repo(tmp_path):
    """A repository whose behaviour lives largely outside its code."""
    repo = tmp_path / "config_project"
    (repo / "services").mkdir(parents=True)
    (repo / "migrations" / "versions").mkdir(parents=True)

    (repo / ".env.example").write_text(ENV_V1, encoding="utf-8")
    (repo / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (repo / "openapi.json").write_text(OPENAPI, encoding="utf-8")
    (repo / "migrations" / "versions" / "abc123_add_email.py").write_text(
        MIGRATION, encoding="utf-8"
    )
    (repo / "services" / "app.py").write_text(
        "class App:\n    def run(self) -> None:\n        pass\n", encoding="utf-8"
    )
    return repo


def test_artifacts_are_classified_by_kind_and_evidence_type():
    """Each artifact kind maps to the evidence type a claim about it carries."""
    assert classify(".env.example") == "env"
    assert classify("docker-compose.yml") == "compose"
    assert classify(".github/workflows/ci.yml") == "ci"
    assert classify("openapi.json") == "openapi"
    assert classify("migrations/versions/abc_add_thing.py") == "migration"
    assert classify("Dockerfile") == "docker"

    # Not configuration, and not guessed at.
    assert classify("services/app.py") is None
    assert classify("README.md") is None

    # Generated artifacts are excluded: a lockfile would contribute thousands of
    # keys nobody would anchor a claim to.
    assert classify("package-lock.json") is None
    assert classify("uv.lock") is None
    assert classify("benchmarks/results/run_1.json") is None


def test_env_values_are_never_captured():
    """A `.env` file is where credentials live; only key names are indexed."""
    artifact = ConfigIntelligenceProvider().parse(
        ".env",
        b"DATABASE_URL=postgresql://user:hunter2@db/app\nAPI_KEY=sk-secret-value\n",
    )

    assert artifact is not None
    assert {key.name for key in artifact.keys} == {"DATABASE_URL", "API_KEY"}
    for key in artifact.keys:
        assert key.value_preview is None
    # The secret must not appear anywhere in the parsed artifact.
    assert "hunter2" not in str(artifact)
    assert "sk-secret-value" not in str(artifact)


def test_openapi_paths_and_operations_become_addressable():
    """An endpoint is a thing a memory can be grounded in."""
    artifact = ConfigIntelligenceProvider().parse("openapi.json", OPENAPI.encode())

    assert artifact is not None
    assert artifact.evidence_type == EvidenceType.API_CONTRACT.value
    names = {key.name for key in artifact.keys}
    assert "/users" in names
    assert "GET /users" in names
    assert "DELETE /users/{id}" in names


def test_migration_operations_become_addressable():
    """A schema claim should be reachable from the migration that made it true."""
    artifact = ConfigIntelligenceProvider().parse(
        "migrations/versions/a.py", MIGRATION.encode()
    )

    assert artifact is not None
    assert artifact.evidence_type == EvidenceType.SCHEMA.value
    names = {key.name for key in artifact.keys}
    assert "add_column:users" in names
    assert "create_index:idx_users_email" in names
    assert artifact.detail["revision"] == "abc123"


def test_discovery_finds_configuration_across_a_repository(config_repo):
    """Discovery must reach CI definitions inside dot-directories."""
    (config_repo / ".github" / "workflows").mkdir(parents=True)
    (config_repo / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )

    found = discover_config_files(str(config_repo), DEFAULT_IGNORED_DIRS)

    assert ".env.example" in found
    assert "docker-compose.yml" in found
    assert "openapi.json" in found
    assert "migrations/versions/abc123_add_email.py" in found
    # `.github` is the one dot-directory that is not pruned, because CI config is
    # exactly what this exists to index.
    assert ".github/workflows/ci.yml" in found


@pytest.mark.asyncio
async def test_configuration_is_indexed_as_addressable_entities(
    config_repo, test_session: AsyncSession
):
    """Config files and their individual keys both become entities."""
    project = Project(name="ConfigProject", local_path=str(config_repo), status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    await RepositoryScanner().scan_project(test_session, project, incremental=False)

    entities = (
        (
            await test_session.execute(
                select(CodeEntity).where(CodeEntity.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    by_qualified = {entity.qualified_name: entity for entity in entities}

    # The file itself is addressable.
    assert ".env.example" in by_qualified
    assert by_qualified[".env.example"].entity_type == "config"

    # So is each individual setting, which is what lets one memory depend on one
    # key rather than on the whole file.
    assert ".env.example:REDIS_URL" in by_qualified
    redis_key = by_qualified[".env.example:REDIS_URL"]
    assert redis_key.entity_type == "config_key"
    assert redis_key.entity_metadata["evidence_type"] == EvidenceType.CONFIG.value

    # Contract and schema artifacts carry their own evidence types.
    assert by_qualified["openapi.json"].entity_metadata["evidence_type"] == (
        EvidenceType.API_CONTRACT.value
    )
    migration = "migrations/versions/abc123_add_email.py"
    assert by_qualified[migration].entity_metadata["evidence_type"] == (
        EvidenceType.SCHEMA.value
    )

    # Code is still indexed as code.
    assert any(entity.entity_type in ("class", "method") for entity in entities)


@pytest.mark.asyncio
async def test_removing_a_setting_invalidates_only_what_depended_on_it(
    config_repo, test_session: AsyncSession
):
    """A configuration change must reach memory with key-level precision.

    Two memories are grounded in the same file but different keys. Removing one
    key must invalidate exactly one of them. Invalidating both would make
    configuration indexing worse than useless -- every deployment tweak would
    wipe out unrelated knowledge.
    """
    project = Project(name="ConfigImpact", local_path=str(config_repo), status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scanner = RepositoryScanner()
    await scanner.scan_project(test_session, project, incremental=False)

    service = MemoryService()
    redis_memory = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            title="The service requires a Redis connection",
            content="REDIS_URL must be configured for the service to start.",
            summary="REDIS_URL is required",
            source_type="config",
            importance=0.9,
            evidence=[
                MemoryEvidenceCreate(
                    file_path=".env.example",
                    source_type="config",
                    line_start=3,
                    line_end=3,
                )
            ],
        ),
    )
    database_memory = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            title="The service requires a database connection",
            content="DATABASE_URL must point at the application database.",
            summary="DATABASE_URL is required",
            source_type="config",
            importance=0.9,
            evidence=[
                MemoryEvidenceCreate(
                    file_path=".env.example",
                    source_type="config",
                    line_start=2,
                    line_end=2,
                )
            ],
        ),
    )

    assert redis_memory.status == MemoryState.ACTIVE.value
    assert database_memory.status == MemoryState.ACTIVE.value

    # Redis is dropped from the configuration.
    (config_repo / ".env.example").write_text(ENV_V2_REDIS_REMOVED, encoding="utf-8")
    await scanner.scan_project(test_session, project, incremental=False)

    report = await SemanticChangePropagator().propagate_changes(
        test_session, project.id, modified_files=[".env.example"], mark_stale=True
    )

    await test_session.refresh(redis_memory)
    await test_session.refresh(database_memory)

    assert redis_memory.status != MemoryState.ACTIVE.value, (
        "the memory depending on the removed setting must not stay believed; "
        f"decisions were {report.decisions}"
    )
    assert database_memory.status == MemoryState.ACTIVE.value, (
        "a memory grounded in a setting that still exists must be untouched; "
        f"decisions were {report.decisions}"
    )

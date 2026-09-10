"""Integration tests for Repository Scanner, Relational Graph, REST API, and MCP."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import cortexforge.apps.api.main
from cortexforge.apps.api.main import app
from cortexforge.apps.mcp.server import (
    memory_get_lessons,
    project_get_architecture,
    project_get_component,
    task_complete,
    task_record_event,
    task_start,
)
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Base, CodeEntity, Project, Relationship
from cortexforge.graph.service import GraphService


@pytest.fixture(scope="session")
def sample_repo():
    """Create a temporary multi-language repository."""
    tmp_dir = tempfile.mkdtemp(prefix="cortexforge_test_repo_")

    # Python module
    os.makedirs(os.path.join(tmp_dir, "services"), exist_ok=True)
    with open(os.path.join(tmp_dir, "services", "auth.py"), "w", encoding="utf-8") as f:
        f.write("""
class AuthService:
    def authenticate(self, user: str) -> bool:
        return user == "admin"
""")

    with open(
        os.path.join(tmp_dir, "services", "payment.py"), "w", encoding="utf-8"
    ) as f:
        f.write("""
import services.auth

class PaymentService:
    def __init__(self):
        self.auth = services.auth.AuthService()

    def process(self, amount: float) -> bool:
        return amount > 0
""")

    # TypeScript module
    os.makedirs(os.path.join(tmp_dir, "client"), exist_ok=True)
    with open(os.path.join(tmp_dir, "client", "api.ts"), "w", encoding="utf-8") as f:
        f.write("""
export interface ApiConfig {
    baseUrl: string;
}

export class ApiClient {
    fetchData(): string {
        return "data";
    }
}
""")

    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def test_session():
    """Create an in-memory SQLite async test database."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_scanner_and_architecture_synthesis(
    sample_repo, test_session: AsyncSession
):
    """Verify repository scanner parses AST and builds architecture."""
    project = Project(
        name="TestApp",
        local_path=os.path.realpath(sample_repo),
        status="INITIALIZING",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scanner = RepositoryScanner()
    scan_res = await scanner.scan_project(test_session, project, incremental=False)

    assert scan_res.status == "SUCCESS"
    assert scan_res.files_scanned >= 3
    assert scan_res.entities_extracted >= 3
    assert scan_res.duration_ms > 0

    # Test Graph Architecture Synthesis
    graph_service = GraphService()
    arch = await graph_service.get_project_architecture(test_session, project.id)

    assert arch is not None
    assert arch.project_name == "TestApp"
    assert arch.total_files >= 3
    assert arch.total_entities >= 3
    assert "python" in arch.languages
    assert "typescript" in arch.languages

    module_names = [m.module_path for m in arch.modules]
    assert any("services" in m for m in module_names)
    assert any("client" in m for m in module_names)


@pytest.mark.asyncio
async def test_rest_api_endpoints(sample_repo, test_session: AsyncSession):
    """Test FastAPI REST endpoints."""

    # Override get_db_session dependency with test_session
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Health
        health_resp = await client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "healthy"

        # Register Project
        proj_resp = await client.post(
            "/api/v1/projects",
            json={"name": "ApiTestRepo", "local_path": sample_repo},
        )
        assert proj_resp.status_code in (201, 409)
        if proj_resp.status_code == 201:
            proj_data = proj_resp.json()
            project_id = proj_data["id"]

            # Trigger Scan
            scan_resp = await client.post(f"/api/v1/projects/{project_id}/scan")
            assert scan_resp.status_code == 200
            assert scan_resp.json()["status"] in ("SUCCESS", "PARTIAL_SUCCESS")

            # Get Architecture
            arch_resp = await client.get(f"/api/v1/projects/{project_id}/architecture")
            assert arch_resp.status_code == 200
            arch_data = arch_resp.json()
            assert arch_data["project_name"] == "ApiTestRepo"
            assert len(arch_data["modules"]) >= 2

            # Check Change Impact
            impact_resp = await client.post(
                f"/api/v1/projects/{project_id}/impact",
                json={"modified_files": ["services/auth.py"], "mark_stale": False},
            )
            assert impact_resp.status_code == 200
            impact_data = impact_resp.json()
            assert "directly_changed_entities" in impact_data
            assert any(
                "AuthService" in e for e in impact_data["directly_changed_entities"]
            )

        # Static SPA serving is mounted only when apps/web/dist has been built.
        # Asserting on it unconditionally makes an API test fail for the unrelated
        # reason that `npm run build` has not run, so the assertion is made
        # conditional on the artifact actually existing.
        dist_index = (
            Path(cortexforge.apps.api.main.__file__).resolve().parents[4]
            / "apps"
            / "web"
            / "dist"
            / "index.html"
        )
        spa_resp = await client.get("/")
        if dist_index.exists():
            assert spa_resp.status_code == 200
            assert "CortexForge" in spa_resp.text
        else:
            assert spa_resp.status_code == 404, (
                "with no built frontend the SPA route must not be mounted at all"
            )

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mcp_tools(sample_repo):
    """Verify MCP tools format architecture and components."""
    # project_get_architecture tool
    arch_text = await project_get_architecture(sample_repo)
    assert "# Project Architecture:" in arch_text
    assert "Modules" in arch_text
    assert "AuthService" in arch_text or "PaymentService" in arch_text

    # project_get_component tool
    comp_text = await project_get_component("AuthService", sample_repo)
    assert "AuthService" in comp_text
    assert "auth.py" in comp_text

    # task_start tool
    start_res = await task_start(
        "Refactor login verification", project_id_or_path=sample_repo
    )
    assert "Task started:" in start_res
    task_id = start_res.split("`")[1]

    # task_record_event tool
    ev_res = await task_record_event(
        task_id, event_type="TOOL_CALLED", tool_name="search_symbols"
    )
    assert "Recorded tool call" in ev_res

    # task_complete tool
    comp_res = await task_complete(
        task_id,
        success=True,
        lesson_learned="Always salt hashes before hashing in AuthService",
    )
    assert "COMPLETED" in comp_res

    # memory_get_lessons tool. A lesson recorded by a completing agent is an
    # observation, not yet an established rule, so it is surfaced under the
    # proposals heading and labelled with its authority.
    lessons_res = await memory_get_lessons(sample_repo)
    assert "Lessons" in lessons_res
    assert "AuthService" in lessons_res


def _git_run(repo: str, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )
    return res.stdout.strip()


def _git_commit(repo: str, msg: str) -> str:
    _git_run(repo, "add", "-A")
    _git_run(repo, "commit", "-q", "-m", msg)
    return _git_run(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_incremental_relationship_graph_preservation(
    tmp_path, test_session: AsyncSession
):
    """Verify incremental scan preserves cross-file inbound relationships and updates generations."""
    repo = str(tmp_path / "inc_repo")
    os.makedirs(os.path.join(repo, "services"), exist_ok=True)

    _git_run(str(tmp_path), "init", "-q", repo)
    _git_run(repo, "config", "user.email", "test@example.com")
    _git_run(repo, "config", "user.name", "Test")

    auth_file = os.path.join(repo, "services", "auth.py")
    payment_file = os.path.join(repo, "services", "payment.py")

    with open(auth_file, "w", encoding="utf-8") as f:
        f.write("""class AuthService:
    def authenticate(self, user: str) -> bool:
        return user == "admin"
""")

    with open(payment_file, "w", encoding="utf-8") as f:
        f.write("""import services.auth

class PaymentService:
    def __init__(self):
        self.auth = services.auth.AuthService()

    def process(self, amount: float) -> bool:
        return amount > 0
""")

    _git_commit(repo, "initial")

    project = Project(
        name="IncrementalGraphRepo",
        local_path=os.path.realpath(repo),
        status="INITIALIZING",
        default_branch="main",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scanner = RepositoryScanner()
    res1 = await scanner.scan_project(test_session, project, incremental=False)

    assert res1.status == "SUCCESS"
    assert res1.files_scanned == 2
    assert res1.graph_generation == 1

    # Verify cross-file relationship exists
    rels_stmt = select(Relationship).where(Relationship.project_id == project.id)
    initial_rels = (await test_session.execute(rels_stmt)).scalars().all()
    assert len(initial_rels) >= 1

    # Check entities in payment.py and auth.py
    ents_stmt = select(CodeEntity).where(CodeEntity.project_id == project.id)
    ents = {e.name: e for e in (await test_session.execute(ents_stmt)).scalars().all()}
    assert "AuthService" in ents
    assert "PaymentService" in ents

    # Modify ONLY services/auth.py
    with open(auth_file, "w", encoding="utf-8") as f:
        f.write("""class AuthService:
    def authenticate(self, user: str) -> bool:
        return bool(user)

    def verify_token(self, token: str) -> bool:
        return len(token) > 8
""")

    _git_commit(repo, "update auth only")

    # Run incremental scan
    res2 = await scanner.scan_project(test_session, project, incremental=True)

    assert res2.status == "SUCCESS"
    assert res2.files_scanned == 1  # Only auth.py scanned
    assert res2.graph_generation == 2

    # Inbound relationship from untouched PaymentService to AuthService must still exist!
    rels_after = (await test_session.execute(rels_stmt)).scalars().all()
    assert len(rels_after) >= 1

    # Verify AuthService was updated with new method
    ents_after = {
        e.name: e for e in (await test_session.execute(ents_stmt)).scalars().all()
    }
    assert "verify_token" in ents_after
    assert "PaymentService" in ents_after

    # Rescanning without changes returns files_scanned=0 and preserves graph
    res3 = await scanner.scan_project(test_session, project, incremental=True)
    assert res3.status == "SUCCESS"
    assert res3.files_scanned == 0
    assert res3.graph_generation == 2

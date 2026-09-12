"""Integration tests for Memory Lifecycle, Verification, Consolidation, Change Propagation, and GitHub Webhooks."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import Base, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer


@pytest.fixture(scope="session")
def memory_test_repo():
    """Create a temporary project folder with sample code files."""
    tmp_dir = tempfile.mkdtemp(prefix="cortex_mem_test_")
    os.makedirs(os.path.join(tmp_dir, "services"), exist_ok=True)
    with open(os.path.join(tmp_dir, "services", "auth.py"), "w", encoding="utf-8") as f:
        f.write("""
class AuthService:
    def verify_token(self, token: str) -> bool:
        return True
""")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def mem_session():
    """In-memory SQLite async test database."""
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
async def test_memory_crud_and_versioning(memory_test_repo, mem_session: AsyncSession):
    """Test memory creation, evidence attachment, and version audit trail."""
    project = Project(
        name="MemTestProject",
        local_path=memory_test_repo,
        status="READY",
    )
    mem_session.add(project)
    await mem_session.commit()
    await mem_session.refresh(project)

    mem_service = MemoryService()

    # 1. Create Memory with Evidence
    create_payload = MemoryCreate(
        memory_type="DECISION",
        title="JWT Stateless Authentication",
        content="We use stateless JWT tokens verified against RSA public key.",
        summary="Stateless JWT authentication decision",
        importance=0.9,
        evidence=[
            MemoryEvidenceCreate(
                file_path="services/auth.py",
                line_start=2,
                line_end=4,
            )
        ],
    )
    memory = await mem_service.create_memory(mem_session, project.id, create_payload)
    assert memory.id is not None
    assert memory.version == 1
    assert memory.status == "ACTIVE"
    assert len(memory.evidences) == 1

    # 2. Update Memory -> Check Version Increment and Audit Log
    updated = await mem_service.update_memory(
        mem_session,
        memory_id=memory.id,
        content="Updated: JWT tokens are verified with Ed25519 signatures.",
        change_reason="Security migration to Ed25519",
    )
    assert updated.version == 2
    assert "Ed25519" in updated.content

    # Check version history
    full_mem = await mem_service.get_memory(mem_session, memory.id)
    assert len(full_mem.versions) == 2
    assert full_mem.versions[1].change_reason == "Security migration to Ed25519"

    # 3. Vector Hybrid Search
    search_results = await mem_service.search_memories(
        mem_session, project.id, query="JWT authentication token"
    )
    assert len(search_results) >= 1
    assert search_results[0]["memory"].id == memory.id
    assert search_results[0]["combined_score"] > 0.0


@pytest.mark.asyncio
async def test_memory_verification_and_change_propagation(
    memory_test_repo, mem_session: AsyncSession
):
    """Test verification engine and semantic change propagation."""
    project = Project(
        name="PropTestProject",
        local_path=memory_test_repo,
        status="READY",
    )
    mem_session.add(project)
    await mem_session.commit()
    await mem_session.refresh(project)

    # Initial scan
    scanner = RepositoryScanner()
    await scanner.scan_project(mem_session, project, incremental=False)

    mem_service = MemoryService()
    # Create memory grounded in services/auth.py
    mem = await mem_service.create_memory(
        mem_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            title="Token Revocation Invariant",
            content="Token must be checked against blacklist table.",
            summary="Token revocation constraint",
            importance=0.95,
            evidence=[MemoryEvidenceCreate(file_path="services/auth.py", line_start=2)],
        ),
    )

    # Test Verification Engine
    verifier = MemoryVerificationEngine()
    st = await verifier.verify_single_memory(mem_session, mem, memory_test_repo)
    assert st == "ACTIVE"

    propagator = SemanticChangePropagator()

    # Declaring a file "modified" without changing it must not invalidate anything.
    # Staleness has to be earned by an actual change to the grounding code, or the
    # system would degrade its own knowledge every time a build touched a file.
    unchanged_report = await propagator.propagate_changes(
        mem_session, project.id, modified_files=["services/auth.py"], mark_stale=True
    )
    assert unchanged_report.memories_flagged_stale == []
    await mem_session.refresh(mem)
    assert mem.status == "ACTIVE"
    assert any(d["decision"] == "KEEP" for d in unchanged_report.decisions)

    # Now genuinely change the grounded method's implementation.
    auth_path = os.path.join(memory_test_repo, "services", "auth.py")
    with open(auth_path, "w", encoding="utf-8") as handle:
        handle.write("""
class AuthService:
    def verify_token(self, token: str) -> bool:
        return self.blacklist.check(token) is False
""")

    impact_report = await propagator.propagate_changes(
        mem_session, project.id, modified_files=["services/auth.py"], mark_stale=True
    )

    assert len(impact_report.memories_flagged_stale) >= 1
    # The decision must be explainable, not merely applied.
    revisions = [d for d in impact_report.decisions if d["decision"] == "REVISE"]
    assert revisions
    assert revisions[0]["reason_code"] in ("BODY_CHANGED", "SIGNATURE_CHANGED")

    await mem_session.refresh(mem)
    assert mem.status == "STALE"

    # Re-running the identical analysis must not duplicate the change record.
    repeat = await propagator.propagate_changes(
        mem_session, project.id, modified_files=["services/auth.py"], mark_stale=True
    )
    assert repeat.change_set_id == impact_report.change_set_id


@pytest.mark.asyncio
async def test_memory_consolidation(memory_test_repo, mem_session: AsyncSession):
    """Test clustering episodic failures into durable architectural lessons."""
    project = Project(
        name="ConsolidationTestProject",
        local_path=memory_test_repo,
        status="READY",
    )
    mem_session.add(project)
    await mem_session.commit()
    await mem_session.refresh(project)

    mem_service = MemoryService()

    # Create two related episodic failures
    episodic_sources = [
        await mem_service.create_memory(
            mem_session,
            project.id,
            MemoryCreate(
                memory_type="FAILURE",
                title="Database Connection Pool Exhaustion on Worker Startup",
                content="Background workers opened 50 unclosed asyncpg connections, crashing PostgreSQL.",
                summary="Worker connection pool exhaustion incident",
                importance=0.7,
            ),
        ),
        await mem_service.create_memory(
            mem_session,
            project.id,
            MemoryCreate(
                memory_type="FAILURE",
                title="API Worker Hanging Due to Database Pool Starvation",
                content="API workers hung when asyncpg connection pool hit maximum overflow limit.",
                summary="API worker pool starvation incident",
                importance=0.7,
            ),
        ),
    ]

    # Run consolidation engine
    consolidation = MemoryConsolidationEngine(memory_service=mem_service)
    res = await consolidation.consolidate_project(mem_session, project.id)

    assert res["clusters_consolidated"] >= 1
    assert res["durable_memories_created"] >= 1

    # A proposed lesson is not yet knowledge, so the episodes it was derived from
    # must still be intact. Consolidation may never destroy what it has not
    # successfully replaced (specification section 26).
    assert res["memories_archived"] == 0
    for episode in episodic_sources:
        await mem_session.refresh(episode)
        assert episode.status != "ARCHIVED"

    lessons = await mem_service.list_memories(
        mem_session, project.id, memory_type="LESSON"
    )
    assert len(lessons) >= 1
    assert lessons[0].status == "REVIEW_REQUIRED"
    assert lessons[0].authority == "LLM_GENERATED"

    # Re-running consolidation over the same episodes must not create a second
    # lesson: clusters are identified by a fingerprint over their members.
    repeat = await consolidation.consolidate_project(mem_session, project.id)
    assert repeat["durable_memories_created"] == 0
    lessons_after = await mem_service.list_memories(
        mem_session, project.id, memory_type="LESSON"
    )
    assert len(lessons_after) == len(lessons)

    # Approval is the decision that turns a proposal into knowledge, and only then
    # are the source episodes archived.
    approved = await consolidation.approve_lesson(
        mem_session, lessons[0].id, approver="reviewer@example.com"
    )
    assert approved.status == "ACTIVE"
    assert approved.authority == "REVIEW_CONFIRMED"


@pytest.mark.asyncio
async def test_context_composer(memory_test_repo, mem_session: AsyncSession):
    """Test token-budget structured context composer."""
    project = Project(
        name="ContextTestProject",
        local_path=memory_test_repo,
        status="READY",
    )
    mem_session.add(project)
    await mem_session.commit()
    await mem_session.refresh(project)

    mem_service = MemoryService()
    await mem_service.create_memory(
        mem_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="AsyncIO for All Database IO",
            content="All database operations must use SQLAlchemy async sessions.",
            summary="Mandatory AsyncIO database pattern",
            importance=0.8,
        ),
    )

    composer = ContextComposer()
    context_str = await composer.build_context(
        mem_session,
        project_id=project.id,
        task_text="Add new repository endpoint for user search",
        profile="medium",
    )

    assert "<!-- CORTEXFORGE VERIFIED PROJECT CONTEXT -->" in context_str
    assert "Relevant Decisions" in context_str
    assert "AsyncIO for All Database IO" in context_str
    assert "<!-- END CORTEXFORGE CONTEXT -->" in context_str

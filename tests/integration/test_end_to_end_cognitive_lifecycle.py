"""Mandatory End-to-End Cognitive Lifecycle Acceptance Test.

Verifies the complete 11-step cognitive cycle:
1. Index synthetic repository
2. Ask initial task & retrieve context
3. Store architectural decision (L3) & failure post-mortem (L4)
4. Ask second task; CortexForge retrieves relevant decision & failure prevention
5. Modify code in repository (changing architecture)
6. Detect changes & propagate impact
7. Run memory verification with SHA-256 evidence grounding
8. Detect that outdated memory is marked STALE
9. Store updated verified memory
10. Consolidate repeated memories into durable lessons (L5)
11. Retrieve again; confirm new context reflects the updated state and measurable tokens.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import (
    Base,
    FailureEpisode,
    FixAttempt,
    Memory,
    Project,
    TestCaseResult,
    TestRun,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.fixture
def synthetic_repo():
    """Create a synthetic repo with an auth service."""
    tmp = tempfile.mkdtemp(prefix="cortex_e2e_")
    services_dir = os.path.join(tmp, "services")
    os.makedirs(services_dir, exist_ok=True)

    auth_file = os.path.join(services_dir, "auth.py")
    with open(auth_file, "w", encoding="utf-8") as f:
        f.write("""class AuthService:
    def login(self, username: str, password: str) -> str:
        # Initial implementation uses Redis session store
        return "redis_session_123"

    def logout(self, session_id: str) -> bool:
        return True
""")

    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def e2e_session():
    """Isolated async in-memory SQLite session for acceptance testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_end_to_end_cognitive_lifecycle(synthetic_repo, e2e_session: AsyncSession):
    scanner = RepositoryScanner()
    mem_service = MemoryService()
    verifier = MemoryVerificationEngine()
    propagator = SemanticChangePropagator()
    consolidator = MemoryConsolidationEngine()
    retrieval = HybridRetrievalEngine()
    composer = ContextComposer(retrieval_engine=retrieval)

    # ----------------------------------------------------
    # Step 1: Register and Index the project
    # ----------------------------------------------------
    project = Project(
        name="SyntheticE2E",
        local_path=synthetic_repo,
        status="ACTIVE",
    )
    e2e_session.add(project)
    await e2e_session.commit()
    await e2e_session.refresh(project)

    scan_res = await scanner.scan_project(e2e_session, project)
    assert scan_res.files_scanned >= 1
    assert scan_res.entities_extracted >= 1

    # ----------------------------------------------------
    # Step 2: Initial Task Query
    # ----------------------------------------------------
    initial_context = await composer.build_context(
        e2e_session,
        project_id=project.id,
        task_text="Inspect current authentication session handling",
        profile="medium",
        target_files=["services/auth.py"],
    )
    assert "PROJECT CONTEXT: SyntheticE2E" in initial_context
    assert len(initial_context) > 50

    # ----------------------------------------------------
    # Step 3: Store an Architectural Decision (L3) & Failure (L4)
    # ----------------------------------------------------
    decision = await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Redis Session Store for Auth",
            content="Authentication relies on centralized Redis sessions for revoking active logins.",
            summary="Use Redis for session authentication",
            importance=0.85,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    line_start=2,
                    line_end=4,
                )
            ],
        ),
    )
    assert decision.id is not None
    assert decision.status == "ACTIVE"
    assert len(decision.evidences) == 1
    # Evidence hash is computed automatically as SHA-256
    assert len(decision.evidences[0].evidence_hash) == 64

    failure = await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="FAILURE",
            title="Redis Connection Timeout in Multi-AZ",
            content="Direct synchronous Redis connection in AuthService timed out during failover.",
            summary="Avoid direct synchronous Redis connection in request loop",
            importance=0.80,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    line_start=3,
                    line_end=4,
                )
            ],
        ),
    )
    assert failure.id is not None
    assert failure.status == "ACTIVE"

    # ----------------------------------------------------
    # Step 4: Ask second task; CortexForge retrieves decision and failure post-mortem
    # ----------------------------------------------------
    task2_context = await composer.build_context(
        e2e_session,
        project_id=project.id,
        task_text="Add rate limiting to login method in AuthService",
        profile="medium",
        target_files=["services/auth.py"],
    )
    assert "Relevant Decisions" in task2_context
    assert "Redis Session Store for Auth" in task2_context
    assert "Previous Failures & Anti-Patterns" in task2_context
    assert "Redis Connection Timeout" in task2_context

    # ----------------------------------------------------
    # Step 5: Modify code in repository (Architectural shift)
    # ----------------------------------------------------
    # Change AuthService to use JWT tokens instead of Redis session
    auth_file = os.path.join(synthetic_repo, "services", "auth.py")
    with open(auth_file, "w", encoding="utf-8") as f:
        f.write("""class AuthService:
    def login(self, username: str, password: str) -> str:
        # Migrated to stateless JWT tokens
        return "jwt.token.eyJhbGciOiJIUzI1NiJ9"

    def logout(self, token: str) -> bool:
        return True
""")

    # ----------------------------------------------------
    # Step 6: Detect changes and propagate impact
    # ----------------------------------------------------
    impact = await propagator.analyze_change(
        e2e_session,
        project_id=project.id,
        modified_files=["services/auth.py"],
        mark_stale=False,
    )
    assert "services/auth.py" in impact.modified_files
    assert len(impact.memories_flagged_stale) >= 1

    # ----------------------------------------------------
    # Step 7 & 8: Verify memories using SHA-256 evidence grounding
    # ----------------------------------------------------
    verification_stats = await verifier.verify_project_memories(e2e_session, project.id)
    assert verification_stats["stale"] >= 1

    # Re-fetch decision from db to verify status transition
    updated_dec = await mem_service.get_memory(e2e_session, decision.id)
    assert updated_dec is not None
    assert updated_dec.status == "STALE"

    # ----------------------------------------------------
    # Step 9: Store updated verified memory for the new architecture
    # ----------------------------------------------------
    jwt_decision = await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Stateless JWT Authentication",
            content="Authentication migrated to stateless signed JWT tokens.",
            summary="Use stateless JWT tokens for auth",
            importance=0.90,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    line_start=2,
                    line_end=4,
                )
            ],
        ),
    )
    assert jwt_decision.status == "ACTIVE"

    # Add related episodic memories to test consolidation
    await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="FAILURE",
            title="JWT Signature Verification Missing Expiry Check",
            content="Stateless JWT verification missed exp claim causing replay vulnerabilities.",
            summary="Ensure JWT exp claim is strictly verified",
            importance=0.75,
        ),
    )
    await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="FAILURE",
            title="JWT Signature Algorithm Confusion Vulnerability",
            content="Stateless JWT parser accepted none algorithm in header allowing bypass.",
            summary="Disallow none algorithm in JWT verification",
            importance=0.80,
        ),
    )

    # ----------------------------------------------------
    # Step 10: Consolidate repeated memories into durable lessons (L5)
    # ----------------------------------------------------
    cons_res = await consolidator.consolidate_project_memories(e2e_session, project.id)
    assert cons_res["clusters_consolidated"] >= 1
    assert cons_res["durable_memories_created"] >= 1

    # Verify a durable lesson was created in database
    lesson_stmt = select(Memory).where(
        Memory.project_id == project.id, Memory.memory_type == "LESSON"
    )
    lesson_res = await e2e_session.execute(lesson_stmt)
    lessons = list(lesson_res.scalars().all())
    assert len(lessons) >= 1
    assert lessons[0].status == "ACTIVE"

    # ----------------------------------------------------
    # Step 11: Retrieve again; confirm context reflects updated state
    # ----------------------------------------------------
    final_context = await composer.build_context(
        e2e_session,
        project_id=project.id,
        task_text="Check authentication architecture",
        profile="medium",
        target_files=["services/auth.py"],
    )
    # Should feature the new JWT decision and/or durable lessons
    assert "Stateless JWT Authentication" in final_context or "Project Conventions & Durable Lessons" in final_context
    # Warning should flag the stale Redis decision
    assert "Potentially Stale Memories Detected" in final_context
    assert "Redis" in final_context

    # ----------------------------------------------------
    # Step 12: Conflict Resolution & Supersession
    # ----------------------------------------------------
    # Add a memory that directly contradicts the earlier Redis decision
    contradiction_memory = await mem_service.create_memory(
        e2e_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Redis Session Store Removed",
            content="Redis session store was completely removed and disabled.",
            summary="Redis session store disabled",
            source_type="verified_code",
            importance=0.90,
        ),
    )
    # create_memory automatically ran conflict resolution against the earlier Redis decision
    await e2e_session.refresh(decision)
    await e2e_session.refresh(contradiction_memory)
    assert contradiction_memory.supersedes_id == decision.id
    assert decision.status == "SUPERSEDED"
    assert decision.conflict_group is not None


    # ----------------------------------------------------
    # Step 13: Agent Workflow Orchestration & Failure Ingestion
    # ----------------------------------------------------
    orchestrator = AgentWorkflowOrchestrator(
        memory_service=mem_service,
        composer=composer,
    )

    task, ctx = await orchestrator.start_task(
        e2e_session,
        project_id=project.id,
        task_text="Migrate legacy auth tokens to RSA keys",
    )
    assert task.id is not None
    assert task.status == "IN_PROGRESS"
    assert len(ctx) > 20

    # Record failing test result to capture structured failure episode
    await orchestrator.record_test_result(
        e2e_session,
        task_id=task.id,
        test_name="test_rsa_token_signature",
        status="FAILED",
        error_text="ValueError: Invalid RSA key size 512",
        stack_trace='File "/app/auth.py", line 42, in test_rsa\n    verify(token)\nValueError: Invalid RSA key size 512',
    )

    # ----------------------------------------------------
    # Step 14: Verify Failure Intelligence & Record Fix Attempt
    # ----------------------------------------------------
    fe_stmt = select(FailureEpisode).where(FailureEpisode.task_id == task.id)
    fe_res = await e2e_session.execute(fe_stmt)
    fail_episodes = list(fe_res.scalars().all())
    assert len(fail_episodes) >= 1
    fail_ep = fail_episodes[0]
    assert "Invalid RSA key size 512" in fail_ep.error_message
    assert len(fail_ep.failure_signature) == 16

    # Record fix attempt
    fix = await orchestrator.record_fix_attempt(
        e2e_session,
        failure_episode_id=fail_ep.id,
        attempted_fix="Upgrade key generation to 2048-bit RSA keys",
        success=True,
        why_worked_or_failed="2048-bit keys satisfy minimum cryptographic security requirement",
    )
    assert fix.id is not None
    assert fix.success is True

    # Record passing test result after fix
    await orchestrator.record_test_result(
        e2e_session,
        task_id=task.id,
        test_name="test_rsa_token_signature",
        status="PASSED",
    )

    # ----------------------------------------------------
    # Step 15: Complete task and run verification
    # ----------------------------------------------------
    completed_task = await orchestrator.complete_task(
        e2e_session,
        task_id=task.id,
        success=True,
        lesson_learned="RSA token keys must be at least 2048 bits",
    )
    assert completed_task.status == "COMPLETED"
    assert completed_task.success is True

    # ----------------------------------------------------
    # Step 16: Safe Idempotent Consolidation
    # ----------------------------------------------------
    c1 = await consolidator.consolidate_project_memories(e2e_session, project.id)
    # Running consolidation a second time should be idempotent
    c2 = await consolidator.consolidate_project_memories(e2e_session, project.id)
    assert c2["durable_memories_created"] == 0  # Idempotent!

    # ----------------------------------------------------
    # Step 17: Capture Cognitive Snapshot
    # ----------------------------------------------------
    snapshot = await CognitiveSnapshotEngine.take_snapshot(
        e2e_session,
        project_id=project.id,
        commit_sha="e2e4a8f9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
    )
    assert snapshot.active_memories_count >= 1
    assert snapshot.cognitive_generation >= 1

    # ----------------------------------------------------
    # Step 18: Re-request task context for next agent task
    # ----------------------------------------------------
    next_task_context = await composer.build_context(
        e2e_session,
        project_id=project.id,
        task_text="Check authentication architecture and key sizing",
        profile="medium",
        target_files=["services/auth.py"],
    )

    # ----------------------------------------------------
    # Step 19: Confirm obsolete memory NOT retrieved as active decision
    # ----------------------------------------------------
    # The old Redis session decision was SUPERSEDED and must not be selected as an active decision
    assert "Redis Session Store for Auth" not in [
        m["title"] for m in next_task_context.selected_memories if m.get("memory_type") == "DECISION"
    ]

    # ----------------------------------------------------
    # Step 20: Confirm token budgeting, explainability, and replay
    # ----------------------------------------------------
    assert next_task_context.estimated_tokens <= next_task_context.token_budget
    assert len(next_task_context.explainability_report) > 20
    assert len(next_task_context.selected_memories) >= 1

    # Replay state at snapshot commit
    replay = await CognitiveSnapshotEngine.replay_state_at_commit(
        e2e_session,
        project_id=project.id,
        commit_sha="e2e4a8f9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
        task_text="Check authentication architecture and key sizing",
    )
    assert replay["snapshot_generation"] == snapshot.cognitive_generation
    assert len(replay["selected_memories"]) >= 1




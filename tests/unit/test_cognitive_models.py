"""Unit tests for Phase 1 first-class cognitive models and schema relationships."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.core.models import (
    AgentTask,
    ArchitectureRule,
    Base,
    ChangeSet,
    CodeEntity,
    CodeReview,
    CognitiveSnapshot,
    Commit,
    FailureEpisode,
    FileChange,
    FixAttempt,
    Memory,
    Project,
    RuleViolation,
    SymbolChange,
    TestCaseResult,
    TestRun,
)


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_first_class_models_crud_and_relationships(async_db: AsyncSession):
    # 1. Create Project
    project = Project(
        name="CognitionCore",
        local_path="/workspace/cognition_core",
        default_branch="main",
        status="READY",
    )
    async_db.add(project)
    await async_db.commit()
    await async_db.refresh(project)

    # 2. Add Commit
    commit = Commit(
        project_id=project.id,
        commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        parent_sha="0000000000000000000000000000000000000000",
        branch="main",
        author="dev@cortexforge.io",
        message="feat: initial architectural core",
        committed_at=datetime.now(UTC),
    )
    async_db.add(commit)

    # 3. Add ChangeSet, FileChange, and SymbolChange
    change_set = ChangeSet(
        project_id=project.id,
        base_commit_sha="0000000000000000000000000000000000000000",
        target_commit_sha=commit.commit_sha,
        is_working_tree=False,
    )
    async_db.add(change_set)
    await async_db.flush()

    file_change = FileChange(
        change_set_id=change_set.id,
        file_path="src/core.py",
        change_type="MODIFIED",
        lines_added=15,
        lines_deleted=2,
    )
    symbol_change = SymbolChange(
        change_set_id=change_set.id,
        file_path="src/core.py",
        symbol_name="process_token",
        qualified_name="src.core.process_token",
        entity_type="function",
        change_type="SIGNATURE_CHANGED",
        old_signature="def process_token(token: str) -> None",
        new_signature="def process_token(token: str, strict: bool = False) -> bool",
    )
    async_db.add_all([file_change, symbol_change])

    # 4. Add AgentTask, TestRun, and TestCaseResult
    task = AgentTask(
        project_id=project.id,
        task_text="Add strict verification to process_token",
        status="IN_PROGRESS",
    )
    async_db.add(task)
    await async_db.flush()

    test_run = TestRun(
        project_id=project.id,
        task_id=task.id,
        commit_sha=commit.commit_sha,
        framework="pytest",
        status="FAILED",
        total_tests=1,
        failed_count=1,
        duration_ms=45.0,
    )
    async_db.add(test_run)
    await async_db.flush()

    test_case = TestCaseResult(
        test_run_id=test_run.id,
        test_name="test_process_token_strict",
        status="FAILED",
        duration_ms=45.0,
        error_message="AssertionError: Expected True, got False",
        failure_signature="sig_assert_proc_token",
        affected_files=["src/core.py"],
        affected_symbols=["src.core.process_token"],
    )
    async_db.add(test_case)
    await async_db.flush()

    # 5. Add FailureEpisode and FixAttempt
    failure_episode = FailureEpisode(
        project_id=project.id,
        task_id=task.id,
        test_case_result_id=test_case.id,
        failure_signature=test_case.failure_signature,
        error_class="AssertionError",
        error_message=test_case.error_message,
        attempted_approach="Execute token validation without passing strict flag",
        rejected_reason="Returned False on valid tokens",
        affected_files=["src/core.py"],
        affected_symbols=["src.core.process_token"],
        commit_sha=commit.commit_sha,
    )
    async_db.add(failure_episode)
    await async_db.flush()

    fix_attempt = FixAttempt(
        failure_episode_id=failure_episode.id,
        commit_sha=commit.commit_sha,
        attempted_fix="Update caller to pass strict=True when verifying admin tokens",
        success=True,
        why_worked_or_failed="Aligns with updated process_token signature",
    )
    async_db.add(fix_attempt)

    # 6. Add ArchitectureRule and RuleViolation
    rule = ArchitectureRule(
        project_id=project.id,
        rule_name="domain_independence",
        description="Core domain must not import UI presentation layer",
        scope="PROJECT",
        severity="ERROR",
        forbidden_source_pattern="src/core.*",
        forbidden_target_pattern="src/ui.*",
        enforcement_status="ACTIVE",
    )
    async_db.add(rule)
    await async_db.flush()

    # Dummy CodeEntity for violation test
    src_ent = CodeEntity(
        project_id=project.id,
        entity_type="function",
        name="render_helper",
        qualified_name="src.core.render_helper",
        file_path="src/core.py",
        start_line=1,
        end_line=10,
        content_hash="h1",
        language="python",
    )
    tgt_ent = CodeEntity(
        project_id=project.id,
        entity_type="class",
        name="ViewWidget",
        qualified_name="src.ui.ViewWidget",
        file_path="src/ui/view.py",
        start_line=1,
        end_line=20,
        content_hash="h2",
        language="python",
    )
    async_db.add_all([src_ent, tgt_ent])
    await async_db.flush()

    violation = RuleViolation(
        rule_id=rule.id,
        source_entity_id=src_ent.id,
        target_entity_id=tgt_ent.id,
        commit_sha=commit.commit_sha,
        violation_details="render_helper imports ViewWidget directly across boundary",
    )
    async_db.add(violation)

    # 7. Add CodeReview and CognitiveSnapshot
    review = CodeReview(
        project_id=project.id,
        commit_sha=commit.commit_sha,
        reviewer="lead-architect",
        status="APPROVED",
        summary="Verified signature mutation and test isolation",
        comments=[{"file": "src/core.py", "comment": "Clean parameter validation"}],
    )
    snapshot = CognitiveSnapshot(
        project_id=project.id,
        commit_sha=commit.commit_sha,
        cognitive_generation=1,
        memory_generation=1,
        graph_generation=1,
        index_generation=1,
        active_memories_count=10,
        stale_memories_count=0,
        conflicted_memories_count=0,
        retrieval_version="v2",
        embedding_version="1.0.0",
    )
    async_db.add_all([review, snapshot])

    # 8. Add Memory with Scope and Temporal Validity
    memory = Memory(
        project_id=project.id,
        layer="L2",
        memory_type="CONVENTION",
        title="Token Verification Strict Mode",
        content="Always pass strict=True when authenticating administrative tokens in process_token.",
        summary="Strict mode convention for process_token",
        status="ACTIVE",
        scope="FUNCTION",
        valid_from_commit=commit.commit_sha,
        source_type="code_verified",
    )
    async_db.add(memory)
    proj_id = project.id
    await async_db.commit()

    # Expire session cache to ensure fresh relation load
    async_db.expire_all()

    # Query verification
    p_stmt = select(Project).where(Project.id == proj_id)
    p_res = await async_db.execute(p_stmt)
    loaded_project = p_res.scalars().one()

    assert len(loaded_project.commits) == 1
    assert loaded_project.commits[0].commit_sha == commit.commit_sha
    assert len(loaded_project.change_sets) == 1
    assert len(loaded_project.change_sets[0].file_changes) == 1
    assert len(loaded_project.change_sets[0].symbol_changes) == 1
    assert len(loaded_project.test_runs) == 1
    assert len(loaded_project.test_runs[0].results) == 1
    assert len(loaded_project.failure_episodes) == 1
    assert len(loaded_project.failure_episodes[0].fix_attempts) == 1
    assert loaded_project.failure_episodes[0].fix_attempts[0].success is True
    assert len(loaded_project.architecture_rules) == 1
    assert len(loaded_project.architecture_rules[0].violations) == 1
    assert len(loaded_project.cognitive_snapshots) == 1
    assert len(loaded_project.code_reviews) == 1

    # Check Memory scope
    m_stmt = select(Memory).where(Memory.project_id == project.id)
    m_res = await async_db.execute(m_stmt)
    loaded_memory = m_res.scalars().one()
    assert loaded_memory.scope == "FUNCTION"
    assert loaded_memory.valid_from_commit == commit.commit_sha

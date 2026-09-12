"""Unit tests for agent workflow orchestration, canonical events, and failure intelligence."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.events import (
    AntigravityAdapter,
    CanonicalEventType,
    ClaudeCodeAdapter,
    CursorAdapter,
    EventAdapterRegistry,
    GenericMCPAdapter,
)
from cortexforge.agent.failure_intelligence import FailureNormalizer
from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.core.models import AgentTask, Project
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


def test_failure_trace_normalization_and_stable_fingerprint():
    """Verify volatile paths, lines, and hex addresses are stripped to yield identical signatures."""
    trace_win = """
Traceback (most recent call last):
  File "C:\\Users\\Kannan\\AppData\\Local\\Temp\\test_run\\service.py", line 142, in process_payment
    raise TypeError("Invalid gateway response: 0x000001B840BF0230")
TypeError: Invalid gateway response: 0x000001B840BF0230
"""
    trace_linux = """
Traceback (most recent call last):
  File "/home/runner/work/app/service.py", line 987, in process_payment
    raise TypeError("Invalid gateway response: 0x7ffd98e21a40")
TypeError: Invalid gateway response: 0x7ffd98e21a40
"""
    norm_win = FailureNormalizer.normalize_stack_trace(trace_win)
    norm_linux = FailureNormalizer.normalize_stack_trace(trace_linux)

    assert "<PATH>/service.py" in norm_win
    assert "line <LINE>" in norm_win
    assert "<HEX>" in norm_win

    sig_win = FailureNormalizer.compute_signature("TypeError", norm_win)
    sig_linux = FailureNormalizer.compute_signature("TypeError", norm_linux)

    # Section 16 Invariant: same logical failure across machines/lines yields identical signature
    assert sig_win == sig_linux


def test_multi_vendor_event_adapters():
    """Verify adapters correctly map vendor-specific hooks into CanonicalEventType."""
    claude = ClaudeCodeAdapter()
    ev_claude = claude.normalize_event(
        {"hook_name": "pre_tool_execution"}, task_id="t1"
    )
    assert ev_claude.event_type == CanonicalEventType.TOOL_CALLED
    assert ev_claude.source == "claude_code"

    # Claude explicit event_type pass-through
    ev_claude_ctx = claude.normalize_event(
        {"event_type": "CONTEXT_REQUESTED"}, task_id="t1"
    )
    assert ev_claude_ctx.event_type == CanonicalEventType.CONTEXT_REQUESTED

    antigravity = AntigravityAdapter()
    ev_ag = antigravity.normalize_event({"type": "USER_INPUT"}, task_id="t2")
    assert ev_ag.event_type == CanonicalEventType.TASK_STARTED
    assert ev_ag.source == "gemini_antigravity"

    # Antigravity test failures and passes
    ev_ag_fail = antigravity.normalize_event(
        {"type": "TEST_RUN", "status": "FAILED"}, task_id="t2"
    )
    assert ev_ag_fail.event_type == CanonicalEventType.TEST_FAILED
    ev_ag_pass = antigravity.normalize_event(
        {"type": "TEST_RUN", "status": "PASSED"}, task_id="t2"
    )
    assert ev_ag_pass.event_type == CanonicalEventType.TEST_PASSED

    # Antigravity explicit event_type pass-through
    ev_ag_exp = antigravity.normalize_event(
        {"event_type": CanonicalEventType.MEMORY_RETRIEVED.value}, task_id="t2"
    )
    assert ev_ag_exp.event_type == CanonicalEventType.MEMORY_RETRIEVED

    cursor = CursorAdapter()
    ev_cur = cursor.normalize_event({"action": "file_edit_save"}, task_id="t3")
    assert ev_cur.event_type == CanonicalEventType.FILE_CHANGED
    ev_cur_fail = cursor.normalize_event(
        {"action": "terminal_exec", "output": "TEST FAILED: 1 error"}, task_id="t3"
    )
    assert ev_cur_fail.event_type == CanonicalEventType.TEST_FAILED

    mcp = GenericMCPAdapter()
    ev_mcp = mcp.normalize_event({"type": "TEST_FAILED"}, task_id="t4")
    assert ev_mcp.event_type == CanonicalEventType.TEST_FAILED

    # Registry lookup with aliases
    assert EventAdapterRegistry.get_adapter("claude_code").adapter_name == "claude_code"
    assert EventAdapterRegistry.get_adapter("claude").adapter_name == "claude_code"
    assert (
        EventAdapterRegistry.get_adapter("gemini_antigravity").adapter_name
        == "gemini_antigravity"
    )
    assert (
        EventAdapterRegistry.get_adapter("antigravity").adapter_name
        == "gemini_antigravity"
    )
    assert EventAdapterRegistry.get_adapter("unknown").adapter_name == "mcp"


@pytest.mark.asyncio
async def test_agent_workflow_orchestration_lifecycle(
    test_session: AsyncSession, tmp_path
):
    """Verify full end-to-end task workflow: start -> tool call -> test fail -> complete -> memory updated."""
    project = Project(name="OrchProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    mem_service = MemoryService(embedding_provider=embed_provider)
    retrieval = HybridRetrievalEngine(embedding_provider=embed_provider)
    composer = ContextComposer(retrieval_engine=retrieval)

    orchestrator = AgentWorkflowOrchestrator(
        memory_service=mem_service,
        composer=composer,
    )

    # 1. Start Task
    task, context = await orchestrator.start_task(
        session=test_session,
        project_id=project.id,
        task_text="Implement payment retry logic",
        agent_id="test_claude",
        agent_source="claude_code",
        profile="small",
    )
    assert task.status == "IN_PROGRESS"
    assert "PROJECT CONTEXT: OrchProj" in context
    assert len(task.events) >= 2  # TASK_STARTED and CONTEXT_REQUESTED

    # 2. Record Tool Call
    event_tool = await orchestrator.record_tool_call(
        session=test_session,
        task_id=task.id,
        tool_name="edit_file",
        tool_args={"path": "payment.py"},
    )
    assert event_tool.event_type == "TOOL_CALLED"
    await test_session.refresh(task)
    assert task.tool_calls == 1

    # 3. Record Test Failure -> creates structured L4 Failure memory
    await orchestrator.record_test_result(
        session=test_session,
        task_id=task.id,
        test_name="test_payment_retry_exhausted",
        status="FAILED",
        error_text="GatewayTimeoutError: Payment processor did not respond in 3000ms",
        stack_trace="File 'payment.py', line 54, in retry\nGatewayTimeoutError: 0x123",
        affected_files=["payment.py"],
    )

    failures = await mem_service.get_failures(test_session, project.id)
    assert len(failures) >= 1
    assert failures[0].layer == "L4"
    assert "GatewayTimeoutError" in failures[0].content

    # 4. Complete Task with Durable Lesson
    completed_task = await orchestrator.complete_task(
        session=test_session,
        task_id=task.id,
        success=True,
        token_input=1500,
        token_output=450,
        lesson_learned="Always configure exponential backoff with jitter on gateway retries.",
    )
    assert completed_task is not None
    assert completed_task.status == "COMPLETED"
    assert completed_task.success is True
    assert completed_task.token_input == 1500

    # Verify L5 durable lesson was persisted
    lessons = await mem_service.list_memories(test_session, project.id, layer="L5")
    assert len(lessons) >= 1
    assert "exponential backoff" in lessons[0].content


@pytest.mark.asyncio
async def test_find_similar_tasks_multi_signal(test_session: AsyncSession, tmp_path):
    """Verify multi-signal similarity matching across text, files, symbols, and failure signatures (§28)."""
    from cortexforge.core.models import FixAttempt

    project = Project(name="SimilarTaskProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    mem_service = MemoryService(embedding_provider=embed_provider)
    retrieval = HybridRetrievalEngine(embedding_provider=embed_provider)
    composer = ContextComposer(retrieval_engine=retrieval)

    orchestrator = AgentWorkflowOrchestrator(
        memory_service=mem_service,
        composer=composer,
    )

    # Task 1: Payment gateway retry (relevant to payment failures)
    task1, _ = await orchestrator.start_task(
        session=test_session,
        project_id=project.id,
        task_text="Implement payment gateway retry logic",
        agent_id="agent1",
    )
    await orchestrator.record_tool_call(
        session=test_session,
        task_id=task1.id,
        tool_name="edit_file",
        tool_args={"path": "payment.py", "symbol": "process_payment"},
    )
    await orchestrator.record_test_result(
        session=test_session,
        task_id=task1.id,
        test_name="test_gateway_timeout",
        status="FAILED",
        error_text="GatewayTimeoutError: Gateway response timed out after 5000ms",
        stack_trace="File 'payment.py', line 54, in retry\nGatewayTimeoutError: timeout",
        affected_files=["payment.py"],
        affected_symbols=["process_payment"],
    )
    # Add a fix attempt to the recorded failure episode
    await test_session.refresh(task1, ["failure_episodes"])
    assert len(task1.failure_episodes) >= 1
    target_sig = task1.failure_episodes[0].failure_signature
    fix = FixAttempt(
        failure_episode_id=task1.failure_episodes[0].id,
        attempted_fix="Configured exponential backoff with jitter and 10s deadline",
        success=True,
        why_worked_or_failed="Tests passed after timeout increase",
    )
    test_session.add(fix)
    await test_session.commit()

    await orchestrator.complete_task(
        session=test_session,
        task_id=task1.id,
        success=True,
    )

    # Task 2: Completely unrelated task (database indexing)
    task2, _ = await orchestrator.start_task(
        session=test_session,
        project_id=project.id,
        task_text="Add B-tree index on user_email column",
        agent_id="agent2",
    )
    await orchestrator.record_tool_call(
        session=test_session,
        task_id=task2.id,
        tool_name="edit_file",
        tool_args={"path": "db/schema.sql"},
    )
    await orchestrator.complete_task(
        session=test_session,
        task_id=task2.id,
        success=True,
    )

    # Query with multi-signal input (text + file + symbol + failure signature)
    similar = await orchestrator.find_similar_tasks(
        session=test_session,
        project_id=project.id,
        task_text="Resolve payment gateway timeout in checkout flow",
        files=["payment.py"],
        symbols=["process_payment"],
        failure_signature=target_sig,
    )

    assert len(similar) >= 1
    top = similar[0]
    assert top["task_id"] == task1.id
    assert top["similarity_score"] > 0.4
    assert top["score_breakdown"]["file_score"] == 1.0
    assert top["score_breakdown"]["symbol_score"] == 1.0
    assert top["score_breakdown"]["failure_score"] == 1.0
    assert top["score_breakdown"]["text_score"] > 0.0

    # Verify failure episodes and fix attempts are surfaced
    assert len(top["failure_episodes"]) >= 1
    fe = top["failure_episodes"][0]
    assert fe["fix_status"] == "FIXED"
    assert "GatewayTimeoutError" in fe["error_message"]

    assert len(top["fix_attempts"]) >= 1
    fa = top["fix_attempts"][0]
    assert "exponential backoff" in fa["approach_description"]
    assert fa["outcome"] == "SUCCESS"


@pytest.mark.asyncio
async def test_task_session_provenance_persistence(
    test_session: AsyncSession, tmp_path
):
    """Verify that start_task records workspace, session, provider, model, and parent task provenance."""
    project = Project(name="ProvenanceProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    orchestrator = AgentWorkflowOrchestrator()

    task, _ = await orchestrator.start_task(
        session=test_session,
        project_id=project.id,
        task_text="Refactor payment webhook authentication",
        agent_id="agent-claude-3-7",
        agent_source="claude_code",
        workspace_id="ws-enterprise-prod-01",
        session_id="sess-8899aabb",
        parent_task_id="task-root-1122",
        provider="anthropic",
        model="claude-3-7-sonnet",
        model_version="20250219",
    )
    await test_session.commit()

    reloaded = await test_session.get(AgentTask, task.id)
    assert reloaded is not None
    assert reloaded.workspace_id == "ws-enterprise-prod-01"
    assert reloaded.session_id == "sess-8899aabb"
    assert reloaded.parent_task_id == "task-root-1122"
    assert reloaded.provider == "anthropic"
    assert reloaded.model == "claude-3-7-sonnet"
    assert reloaded.model_version == "20250219"

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
from cortexforge.core.models import Project
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
    ev_claude = claude.normalize_event({"hook_name": "pre_tool_execution"}, task_id="t1")
    assert ev_claude.event_type == CanonicalEventType.TOOL_CALLED
    assert ev_claude.source == "claude_code"

    antigravity = AntigravityAdapter()
    ev_ag = antigravity.normalize_event({"type": "USER_INPUT"}, task_id="t2")
    assert ev_ag.event_type == CanonicalEventType.TASK_STARTED
    assert ev_ag.source == "gemini_antigravity"

    cursor = CursorAdapter()
    ev_cur = cursor.normalize_event({"action": "file_edit_save"}, task_id="t3")
    assert ev_cur.event_type == CanonicalEventType.FILE_CHANGED

    mcp = GenericMCPAdapter()
    ev_mcp = mcp.normalize_event({"type": "TEST_FAILED"}, task_id="t4")
    assert ev_mcp.event_type == CanonicalEventType.TEST_FAILED

    # Registry lookup
    assert EventAdapterRegistry.get_adapter("claude_code").adapter_name == "claude_code"
    assert EventAdapterRegistry.get_adapter("unknown").adapter_name == "mcp"


@pytest.mark.asyncio
async def test_agent_workflow_orchestration_lifecycle(test_session: AsyncSession, tmp_path):
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

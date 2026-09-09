"""Integration test for MCP task_find_similar tool with multi-signal matching."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.apps.mcp.server import task_find_similar
from cortexforge.core.models import FixAttempt, Project
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.mark.asyncio
async def test_mcp_task_find_similar_integration(test_session: AsyncSession, tmp_path):
    """Verify MCP task_find_similar returns structured task similarities, failure post-mortems, and fixes."""
    project = Project(name="McpSimProj", local_path=str(tmp_path), status="READY")
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

    # 1. Start and complete a task with test failure and fix
    task, _ = await orchestrator.start_task(
        session=test_session,
        project_id=project.id,
        task_text="Implement token bucket rate limiter",
        agent_id="test_agent",
    )
    await orchestrator.record_tool_call(
        session=test_session,
        task_id=task.id,
        tool_name="edit_file",
        tool_args={"path": "limiter.py"},
    )
    await orchestrator.record_test_result(
        session=test_session,
        task_id=task.id,
        test_name="test_rate_limiter_burst_capacity",
        status="FAILED",
        error_text="CapacityExceededError: Burst limit exceeded without refill",
        stack_trace="File 'limiter.py', line 30\nCapacityExceededError",
        affected_files=["limiter.py"],
        affected_symbols=["TokenBucket"],
    )
    await test_session.refresh(task, ["failure_episodes"])
    assert len(task.failure_episodes) >= 1
    sig = task.failure_episodes[0].failure_signature

    fix = FixAttempt(
        failure_episode_id=task.failure_episodes[0].id,
        attempted_fix="Added token leak timer on acquisition",
        success=True,
        why_worked_or_failed="Burst tests passed reliably",
    )
    test_session.add(fix)
    await test_session.commit()

    await orchestrator.complete_task(
        session=test_session,
        task_id=task.id,
        success=True,
        lesson_learned="Always initialize last_refill_timestamp on bucket startup.",
    )

    # 2. Call the MCP tool task_find_similar
    output = await task_find_similar(
        task_text="Rate limiter burst capacity failure",
        project_id_or_path=str(tmp_path),
        files=["limiter.py"],
        symbols=["TokenBucket"],
        failure_signature=sig,
    )

    assert "# Similar Historical Tasks" in output
    assert "Implement token bucket rate limiter" in output
    assert "**Signals**:" in output
    assert "CapacityExceededError" in output
    assert "Fix Status: FIXED" in output
    assert "Added token leak timer on acquisition" in output

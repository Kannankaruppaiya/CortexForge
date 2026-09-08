"""End-to-end agent workflow orchestration connecting tasks, diffs, tests, and cognitive memories."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.events import (
    CanonicalEventType,
    EventAdapterRegistry,
)
from cortexforge.agent.failure_intelligence import FailureIntelligenceEngine
from cortexforge.code_intelligence.change_propagator import (
    ChangeImpactReport,
    SemanticChangePropagator,
)
from cortexforge.core.models import AgentEvent, AgentTask, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ComposedContext, ContextComposer


class AgentWorkflowOrchestrator:
    """Orchestrates the continuous cognitive cycle between AI coding agents and CortexForge."""

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        composer: ContextComposer | None = None,
        propagator: SemanticChangePropagator | None = None,
        verifier: MemoryVerificationEngine | None = None,
        failure_engine: FailureIntelligenceEngine | None = None,
        consolidator: MemoryConsolidationEngine | None = None,
    ) -> None:
        self.memory_service = memory_service or MemoryService()
        self.composer = composer or ContextComposer()
        self.propagator = propagator or SemanticChangePropagator()
        self.verifier = verifier or MemoryVerificationEngine()
        self.failure_engine = failure_engine or FailureIntelligenceEngine()
        self.consolidator = consolidator or MemoryConsolidationEngine()

    async def start_task(
        self,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        agent_id: str = "generic_agent",
        agent_source: str = "mcp",
        profile: str = "medium",
        target_files: list[str] | None = None,
    ) -> tuple[AgentTask, ComposedContext]:
        """Initialize task, generate token-budgeted cognitive context, and start audit trail."""
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} does not exist.")

        # 1. Create AgentTask record
        task = AgentTask(
            project_id=project_id,
            agent_id=agent_id,
            task_text=task_text,
            status="IN_PROGRESS",
            created_at=datetime.now(UTC),
        )
        session.add(task)
        await session.flush()

        # 2. Record TASK_STARTED event
        adapter = EventAdapterRegistry.get_adapter(agent_source)
        ev_start = adapter.normalize_event(
            {"event_type": CanonicalEventType.TASK_STARTED.value, "task_text": task_text},
            task_id=task.id,
        )
        session.add(
            AgentEvent(
                task_id=task.id,
                event_type=ev_start.event_type.value,
                source=ev_start.source,
                payload=ev_start.payload,
            )
        )

        # 3. Generate token-budgeted explainable context
        context = await self.composer.build_context(
            session=session,
            project_id=project_id,
            task_text=task_text,
            profile=profile,
            target_files=target_files,
        )

        # 4. Record CONTEXT_REQUESTED event
        ev_ctx = adapter.normalize_event(
            {
                "event_type": CanonicalEventType.CONTEXT_REQUESTED.value,
                "profile": profile,
                "estimated_tokens": getattr(context, "estimated_tokens", 0),
            },
            task_id=task.id,
        )
        session.add(
            AgentEvent(
                task_id=task.id,
                event_type=ev_ctx.event_type.value,
                source=ev_ctx.source,
                payload=ev_ctx.payload,
            )
        )

        await session.commit()
        await session.refresh(task)
        return task, context

    async def record_tool_call(
        self,
        session: AsyncSession,
        task_id: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        tool_result: Any | None = None,
        agent_source: str = "mcp",
    ) -> AgentEvent:
        """Record an agent tool execution event and increment task counter."""
        task = await session.get(AgentTask, task_id)
        if task:
            task.tool_calls += 1

        adapter = EventAdapterRegistry.get_adapter(agent_source)
        ev = adapter.normalize_event(
            {
                "event_type": CanonicalEventType.TOOL_CALLED.value,
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "tool_result": str(tool_result)[:500] if tool_result else None,
            },
            task_id=task_id,
        )
        event_record = AgentEvent(
            task_id=task_id,
            event_type=ev.event_type.value,
            source=ev.source,
            payload=ev.payload,
        )
        session.add(event_record)
        await session.commit()
        return event_record

    async def record_file_change(
        self,
        session: AsyncSession,
        task_id: str,
        file_path: str,
        commit_base: str | None = None,
        agent_source: str = "mcp",
    ) -> ChangeImpactReport:
        """Observe file edit, run semantic AST diff, and propagate symbol-level invalidation."""
        task = await session.get(AgentTask, task_id)
        project_id = task.project_id if task else None

        # Log event
        adapter = EventAdapterRegistry.get_adapter(agent_source)
        ev = adapter.normalize_event(
            {
                "event_type": CanonicalEventType.FILE_CHANGED.value,
                "file_path": file_path,
                "commit_base": commit_base,
            },
            task_id=task_id,
        )
        session.add(
            AgentEvent(
                task_id=task_id,
                event_type=ev.event_type.value,
                source=ev.source,
                payload=ev.payload,
            )
        )

        impact = ChangeImpactReport(
            modified_files=[file_path],
            directly_changed_entities=[],
            affected_dependents=[],
            memories_flagged_stale=[],
            critical_constraints=[],
        )
        if project_id:
            impact = await self.propagator.propagate_changes(
                session=session,
                project_id=project_id,
                modified_files=[file_path],
                base_commit=commit_base,
            )


        await session.commit()
        return impact

    async def record_test_result(
        self,
        session: AsyncSession,
        task_id: str,
        test_name: str,
        status: str,  # "PASSED" or "FAILED"
        error_text: str | None = None,
        stack_trace: str | None = None,
        affected_files: list[str] | None = None,
        agent_source: str = "mcp",
    ) -> None:
        """Capture test results, normalize stack traces, and record failure episodes."""
        task = await session.get(AgentTask, task_id)
        if not task:
            return

        is_failure = status.upper() == "FAILED"
        ev_type = CanonicalEventType.TEST_FAILED if is_failure else CanonicalEventType.TEST_PASSED

        adapter = EventAdapterRegistry.get_adapter(agent_source)
        ev = adapter.normalize_event(
            {
                "event_type": ev_type.value,
                "test_name": test_name,
                "status": status,
                "error_text": error_text,
            },
            task_id=task_id,
        )
        session.add(
            AgentEvent(
                task_id=task_id,
                event_type=ev.event_type.value,
                source=ev.source,
                payload=ev.payload,
            )
        )

        # If test failed, create a structured L4 Failure episode memory
        if is_failure and error_text:
            episode = self.failure_engine.process_test_failure(
                task_id=task_id,
                task_text=task.task_text,
                error_text=error_text,
                stack_trace=stack_trace,
                affected_files=affected_files or [],
            )

            await self.memory_service.create_memory(
                session=session,
                project_id=task.project_id,
                payload=MemoryCreate(
                    title=f"Test Failure in {test_name}",
                    content=f"Error: {episode.error_message}\nSignature: {episode.failure_signature}\nTrace:\n{episode.normalized_trace}",
                    summary=f"Failed test {test_name} with signature {episode.failure_signature}",
                    layer="L4",
                    memory_type="FAILURE",
                    source_type="verified_test",
                    importance=0.85,
                    confidence=0.90,
                    evidence=[
                        MemoryEvidenceCreate(
                            file_path=f,
                            source_type="test",
                        )
                        for f in (affected_files or [])
                    ],
                ),
            )

        await session.commit()

    async def complete_task(
        self,
        session: AsyncSession,
        task_id: str,
        success: bool = True,
        token_input: int = 0,
        token_output: int = 0,
        lesson_learned: str | None = None,
        agent_source: str = "mcp",
    ) -> AgentTask | None:
        """Mark task completed, reverify project memories, and promote durable knowledge."""
        task = await session.get(AgentTask, task_id)
        if not task:
            return None

        task.status = "COMPLETED"
        task.success = success
        task.completed_at = datetime.now(UTC)
        task.token_input += token_input
        task.token_output += token_output

        adapter = EventAdapterRegistry.get_adapter(agent_source)
        ev = adapter.normalize_event(
            {
                "event_type": CanonicalEventType.TASK_COMPLETED.value,
                "success": success,
                "tokens": {"input": task.token_input, "output": task.token_output},
            },
            task_id=task_id,
        )
        session.add(
            AgentEvent(
                task_id=task_id,
                event_type=ev.event_type.value,
                source=ev.source,
                payload=ev.payload,
            )
        )

        # 1. Run memory verification engine across the project
        await self.verifier.verify_project_memories(session, task.project_id)

        # 2. If a durable lesson was learned, record in L5
        if success and lesson_learned:
            await self.memory_service.create_memory(
                session=session,
                project_id=task.project_id,
                payload=MemoryCreate(
                    title=f"Lesson from task: {task.task_text[:60]}",
                    content=lesson_learned,
                    summary=lesson_learned[:150],
                    layer="L5",
                    memory_type="LESSON",
                    source_type="agent_observation",
                    importance=0.8,
                    confidence=0.85,
                ),
            )

        await session.commit()
        await session.refresh(task)
        return task

"""End-to-end agent workflow orchestration connecting tasks, diffs, tests, and cognitive memories."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.events import (
    CanonicalEventType,
    EventAdapterRegistry,
)
from cortexforge.agent.failure_intelligence import FailureIntelligenceEngine
from cortexforge.agent.success_intelligence import SuccessIntelligence
from cortexforge.agent.test_intelligence import TestIntelligenceEngine
from cortexforge.code_intelligence.change_propagator import (
    ChangeImpactReport,
    SemanticChangePropagator,
)
from cortexforge.cognition.epistemics import TestAttribution
from cortexforge.core.models import (
    AgentEvent,
    AgentTask,
    FailureEpisode,
    FixAttempt,
    Project,
    RetrievalEvent,
    TestCaseResult,
    TestRun,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.observability.audit import record_audit
from cortexforge.retrieval.composer import ComposedContext, ContextComposer
from cortexforge.retrieval.usefulness import RetrievalUsefulnessTracker

logger = logging.getLogger(__name__)


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
        success_intelligence: SuccessIntelligence | None = None,
        usefulness: RetrievalUsefulnessTracker | None = None,
    ) -> None:
        self.memory_service = memory_service or MemoryService()
        self.composer = composer or ContextComposer()
        self.propagator = propagator or SemanticChangePropagator()
        self.verifier = verifier or MemoryVerificationEngine()
        self.failure_engine = failure_engine or FailureIntelligenceEngine()
        self.consolidator = consolidator or MemoryConsolidationEngine()
        self.success_intelligence = success_intelligence or SuccessIntelligence()
        self.usefulness = usefulness or RetrievalUsefulnessTracker()
        self.test_intelligence = TestIntelligenceEngine()

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
        started = time.perf_counter()
        context = await self.composer.build_context(
            session=session,
            project_id=project_id,
            task_text=task_text,
            profile=profile,
            target_files=target_files,
        )
        latency_ms = (time.perf_counter() - started) * 1000

        # 3a. Record what retrieval returned and what the composer selected from
        #     it. The other half of the loop -- which memories the agent actually
        #     used -- is attached when the task completes (section 27).
        selected = [item["id"] for item in context.selected_memories]
        excluded = [item["id"] for item in context.excluded_memories]
        await self.usefulness.record_retrieval(
            session,
            project_id=project_id,
            query=task_text,
            returned_memory_ids=selected + excluded,
            selected_memory_ids=selected,
            excluded_reasons={
                item["id"]: item.get("reason", "") for item in context.excluded_memories
            },
            stale_returned_count=len(context.stale_warnings),
            conflicted_returned_count=len(context.conflict_warnings),
            task_id=task.id,
            context_tokens=getattr(context, "estimated_tokens", 0),
            latency_ms=round(latency_ms, 2),
            embedding_model=self.composer.retrieval_engine.embedding_provider.model_name,
            embedding_version=self.composer.retrieval_engine.embedding_provider.version,
        )

        # 3b. Surface approaches that worked on similar tasks before, so the agent
        #     starts from what is known to work rather than from nothing.
        prior_successes = await self.success_intelligence.find_similar_successes(
            session,
            project_id=project_id,
            task_text=task_text,
            target_files=target_files,
        )
        if prior_successes:
            session.add(
                AgentEvent(
                    task_id=task.id,
                    event_type=CanonicalEventType.MEMORY_RETRIEVED.value,
                    source=agent_source,
                    payload={
                        "kind": "prior_successes",
                        "successes": prior_successes,
                    },
                )
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

        # 1. Log canonical AgentEvent
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

        # 2. Get or create first-class TestRun for this task
        tr_stmt = select(TestRun).where(TestRun.task_id == task_id)
        tr_res = await session.execute(tr_stmt)
        test_run = tr_res.scalars().first()
        if not test_run:
            test_run = TestRun(
                project_id=task.project_id,
                task_id=task.id,
                framework="pytest",
                status="FAILED" if is_failure else "PASSED",
                total_tests=0,
                passed_count=0,
                failed_count=0,
            )
            session.add(test_run)
            await session.flush()

        test_run.total_tests += 1
        if is_failure:
            test_run.failed_count += 1
            test_run.status = "FAILED"
        else:
            test_run.passed_count += 1

        # 3. Create first-class TestCaseResult
        episode = None
        if is_failure and error_text:
            episode = self.failure_engine.process_test_failure(
                task_id=task_id,
                task_text=task.task_text,
                error_text=error_text,
                stack_trace=stack_trace,
                affected_files=affected_files or [],
            )

        tc_result = TestCaseResult(
            test_run_id=test_run.id,
            test_name=test_name,
            status=status.upper(),
            error_message=error_text[:500] if error_text else None,
            stack_trace=stack_trace,
            failure_signature=episode.failure_signature if episode else None,
            affected_files={"files": affected_files or []},
        )
        session.add(tc_result)
        await session.flush()

        # 4. Attribute the outcome before deciding what it means.
        #
        # A failing test is not automatically evidence that this change is wrong.
        # A test that has been flipping for weeks, or that was already red before
        # the change, tells you about itself rather than about the work in hand
        # (section 15). Recording a FAILURE memory for such a test would let a
        # noisy suite erode the project's knowledge one build at a time.
        attribution = None
        if is_failure:
            report = await self.test_intelligence.attribute_run(session, test_run.id)
            verdict = next(
                (v for v in report.verdicts if v.test_name == test_name), None
            )
            if verdict is not None:
                attribution = verdict
                tc_result.status = (
                    "FLAKY"
                    if verdict.attribution == TestAttribution.FLAKY.value
                    else tc_result.status
                )

        blames_this_change = attribution is None or attribution.is_evidence_against_the_change

        # 5. If the failure is genuinely attributable, record it as a first-class
        #    episode and durable L4 memory. If it is not, the result is still
        #    stored -- it happened -- but it does not become evidence against the
        #    change.
        if is_failure and episode and error_text and blames_this_change:
            fail_episode = FailureEpisode(
                project_id=task.project_id,
                task_id=task.id,
                test_case_result_id=tc_result.id,
                failure_signature=episode.failure_signature,
                error_class=error_text.split(":")[0][:100] if ":" in error_text else "TestFailure",
                error_message=episode.error_message,
                normalized_trace=episode.normalized_trace,
                attempted_approach=task.task_text,
                affected_files={"files": affected_files or []},
            )
            fail_episode.rejected_reason = None
            session.add(fail_episode)

            await self.memory_service.create_memory(
                session=session,
                project_id=task.project_id,
                payload=MemoryCreate(
                    title=f"Test Failure in {test_name}",
                    content=(
                        f"Error: {episode.error_message}\n"
                        f"Signature: {episode.failure_signature}\n"
                        f"Attribution: {attribution.attribution if attribution else 'UNKNOWN'}"
                        f" - {attribution.reason if attribution else 'no history available'}\n"
                        f"Trace:\n{episode.normalized_trace}"
                    ),
                    summary=f"Failed test {test_name} with signature {episode.failure_signature}",
                    layer="L4",
                    memory_type="FAILURE",
                    source_type="verified_test",
                    importance=0.85,
                    evidence=[
                        MemoryEvidenceCreate(
                            file_path=f,
                            source_type="test",
                        )
                        for f in (affected_files or [])
                    ],
                ),
            )

        elif is_failure and attribution is not None:
            logger.info(
                "Test %s failed but was attributed %s (%s); recorded without "
                "creating a failure memory.",
                test_name,
                attribution.attribution,
                attribution.reason,
            )

        await session.commit()

    async def record_fix_attempt(
        self,
        session: AsyncSession,
        failure_episode_id: str,
        attempted_fix: str,
        success: bool,
        why_worked_or_failed: str | None = None,
        commit_sha: str | None = None,
    ) -> FixAttempt:
        """Record an attempted fix for a failure episode and capture whether it resolved the issue."""
        fix = FixAttempt(
            failure_episode_id=failure_episode_id,
            commit_sha=commit_sha,
            attempted_fix=attempted_fix,
            success=success,
            why_worked_or_failed=why_worked_or_failed,
        )
        session.add(fix)
        await session.commit()
        return fix

    async def record_rejected_approach(
        self,
        session: AsyncSession,
        failure_episode_id: str,
        rejected_reason: str,
    ) -> None:
        """Explicitly preserve a rejected approach and rationale to prevent future agent loops."""
        fail_ep = await session.get(FailureEpisode, failure_episode_id)
        if fail_ep:
            fail_ep.rejected_reason = rejected_reason
            await session.commit()

    async def find_similar_tasks(
        self,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve historically similar engineering tasks, approaches, failures, and fixes."""
        stmt = select(AgentTask).where(
            AgentTask.project_id == project_id,
            AgentTask.status == "COMPLETED",
        )
        res = await session.execute(stmt)
        completed_tasks = list(res.scalars().all())

        if not completed_tasks:
            return []

        query_words = set(task_text.lower().split())
        scored_tasks: list[tuple[float, AgentTask]] = []

        for t in completed_tasks:
            t_words = set(t.task_text.lower().split())
            overlap = len(query_words & t_words)
            jaccard = overlap / max(1, len(query_words | t_words))
            if jaccard > 0.05 or overlap >= 2:
                scored_tasks.append((jaccard, t))

        scored_tasks.sort(key=lambda x: x[0], reverse=True)
        top_tasks = scored_tasks[:limit]

        results: list[dict[str, Any]] = []
        for score, t in top_tasks:
            # Fetch failure episodes
            fe_stmt = select(FailureEpisode).where(FailureEpisode.task_id == t.id)
            fe_res = await session.execute(fe_stmt)
            episodes = list(fe_res.scalars().all())

            fail_data = []
            for ep in episodes:
                fail_data.append({
                    "error_message": ep.error_message,
                    "failure_signature": ep.failure_signature,
                    "attempted_approach": ep.attempted_approach,
                    "rejected_reason": ep.rejected_reason,
                })

            results.append({
                "task_id": t.id,
                "task_text": t.task_text,
                "success": t.success,
                "similarity_score": round(score, 3),
                "tool_calls": t.tool_calls,
                "failure_episodes": fail_data,
            })

        return results

    async def complete_task(
        self,
        session: AsyncSession,
        task_id: str,
        success: bool = True,
        token_input: int = 0,
        token_output: int = 0,
        lesson_learned: str | None = None,
        agent_source: str = "mcp",
        successful_approach: str | None = None,
        affected_files: list[str] | None = None,
        commit_sha: str | None = None,
    ) -> AgentTask | None:
        """Close out a task: verify, record what worked, and log the outcome.

        A completed task is the moment both kinds of learning are available. The
        failure path was already recorded as episodes; this also records the
        approach that *worked*, so a future agent facing a similar task can
        retrieve it rather than rediscovering it (section 17).
        """
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

        # 1. Re-verify project memories against the repository as the task left it.
        await self.verifier.verify_project_memories(
            session, task.project_id, commit_sha=commit_sha
        )

        # 2. Record the approach that worked, so it is retrievable next time.
        if success:
            approach = successful_approach or lesson_learned
            if approach:
                await self.success_intelligence.record_task_success(
                    session,
                    task,
                    approach=approach,
                    affected_files=affected_files,
                    commit_sha=commit_sha,
                )

        # 3. A lesson stated by an agent is an observation, not a verified rule.
        #    It is recorded, and the activation policy in MemoryService decides
        #    what state it enters -- which, absent code grounding, is UNVERIFIED.
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
                ),
            )

        # 4. Close the retrieval loop: which memories this task actually used, and
        #    how it turned out. Without this, retrieval quality stays unmeasurable
        #    (section 27).
        await self._close_retrieval_events(session, task, success)

        await record_audit(
            session,
            action="TASK_COMPLETED",
            resource_type="agent_task",
            resource_id=task.id,
            actor=agent_source,
            project_id=task.project_id,
            after={"success": success, "tokens": task.token_input + task.token_output},
            reason=task.task_text[:200],
        )

        await session.commit()
        await session.refresh(task)
        return task

    async def _close_retrieval_events(
        self, session: AsyncSession, task: AgentTask, success: bool
    ) -> None:
        """Attribute the task's outcome to the retrievals that informed it.

        Memories the agent reported reading are recorded as *used*; the rest were
        retrieved and not used, which is exactly the signal needed to tell useful
        retrieval from noise.
        """
        events = await session.execute(
            select(RetrievalEvent).where(
                RetrievalEvent.task_id == task.id,
                RetrievalEvent.task_outcome.is_(None),
            )
        )
        pending = list(events.scalars().all())
        if not pending:
            return

        used_ids: set[str] = set()
        agent_events = await session.execute(
            select(AgentEvent).where(AgentEvent.task_id == task.id)
        )
        for event in agent_events.scalars().all():
            payload = event.payload or {}
            for key in ("memory_ids", "used_memory_ids", "referenced_memory_ids"):
                value = payload.get(key)
                if isinstance(value, list):
                    used_ids.update(str(item) for item in value)

        outcome = "SUCCESS" if success else "FAILURE"
        for event in pending:
            await self.usefulness.record_usage(
                session,
                event.id,
                used_memory_ids=sorted(used_ids & set(event.returned_memory_ids or [])),
                task_outcome=outcome,
            )

"""Retrieval usefulness learning (specification section 27).

Retrieval quality cannot be improved by inspection. Without a record of what was
returned, what the composer selected, what the agent actually used, and how the
task turned out, every claim about retrieval precision is an assertion rather than
a measurement -- which is precisely how fabricated benchmark numbers get into a
dashboard.

This module records that trail and computes metrics from it. Every number it
returns is derived from stored events; when there are no events it says so rather
than returning a plausible-looking zero.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import RetrievalEvent

logger = logging.getLogger(__name__)


@dataclass
class RetrievalMetrics:
    """Measured retrieval quality over a window of recorded events."""

    events: int = 0
    mean_returned: float = 0.0
    mean_selected: float = 0.0
    # Of the memories the composer selected, the share the agent actually used.
    precision: float | None = None
    # Of the memories the agent used, the share retrieval had surfaced.
    recall: float | None = None
    # Share of returned memories that were stale -- knowledge the agent should not
    # have been shown as current.
    stale_hit_rate: float | None = None
    conflicted_hit_rate: float | None = None
    # Share of selected-but-unused memories: context spent without effect.
    redundancy: float | None = None
    mean_context_tokens: float = 0.0
    mean_latency_ms: float = 0.0
    task_success_rate: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        """Whether these numbers rest on any recorded evidence at all."""
        return self.events > 0


def query_fingerprint(project_id: str, query: str) -> str:
    """Stable hash of a retrieval query, for grouping repeated asks."""
    return hashlib.sha256(f"{project_id}|{query.strip().lower()}".encode()).hexdigest()


class RetrievalUsefulnessTracker:
    """Records retrieval events and measures how useful retrieval actually was."""

    async def record_retrieval(
        self,
        session: AsyncSession,
        project_id: str,
        query: str,
        returned_memory_ids: list[str],
        selected_memory_ids: list[str] | None = None,
        excluded_reasons: dict[str, str] | None = None,
        stale_returned_count: int = 0,
        conflicted_returned_count: int = 0,
        task_id: str | None = None,
        context_tokens: int = 0,
        latency_ms: float = 0.0,
        retrieval_version: str = "v2",
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        memory_generation: int | None = None,
        trace_id: str | None = None,
    ) -> RetrievalEvent:
        """Record what one retrieval returned and what was selected from it."""
        event = RetrievalEvent(
            project_id=project_id,
            task_id=task_id,
            query=query,
            query_hash=query_fingerprint(project_id, query),
            returned_memory_ids=list(returned_memory_ids),
            selected_memory_ids=list(selected_memory_ids or []),
            used_memory_ids=[],
            excluded_reasons=excluded_reasons or {},
            stale_returned_count=stale_returned_count,
            conflicted_returned_count=conflicted_returned_count,
            context_tokens=context_tokens,
            latency_ms=latency_ms,
            retrieval_version=retrieval_version,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            memory_generation=memory_generation,
            trace_id=trace_id,
        )
        session.add(event)
        await session.flush()
        return event

    async def record_usage(
        self,
        session: AsyncSession,
        event_id: str,
        used_memory_ids: list[str],
        task_outcome: str | None = None,
        tests_passed: int | None = None,
        tests_failed: int | None = None,
    ) -> RetrievalEvent | None:
        """Close the loop: which retrieved memories the agent actually used.

        This is the half that makes the rest meaningful. Retrieval that returns ten
        relevant-looking memories of which the agent reads one is not doing well,
        and only this record can tell the difference.
        """
        event = await session.get(RetrievalEvent, event_id)
        if event is None:
            return None

        event.used_memory_ids = sorted(set(used_memory_ids))
        if task_outcome is not None:
            event.task_outcome = task_outcome
        if tests_passed is not None:
            event.tests_passed = tests_passed
        if tests_failed is not None:
            event.tests_failed = tests_failed
        await session.flush()
        return event

    async def measure(
        self,
        session: AsyncSession,
        project_id: str,
        limit: int = 500,
    ) -> RetrievalMetrics:
        """Compute retrieval metrics from recorded events.

        Metrics that require usage feedback are ``None`` when no event carries it,
        rather than being reported as zero: "we have not measured this" and "this
        measured zero" are different statements and must not look alike.
        """
        res = await session.execute(
            select(RetrievalEvent)
            .where(RetrievalEvent.project_id == project_id)
            .order_by(RetrievalEvent.created_at.desc())
            .limit(limit)
        )
        events = list(res.scalars().all())

        metrics = RetrievalMetrics(events=len(events))
        if not events:
            metrics.notes.append(
                "No retrieval events recorded for this project, so retrieval quality "
                "is unmeasured. It is not zero; it is unknown."
            )
            return metrics

        total_returned = sum(len(e.returned_memory_ids or []) for e in events)
        total_selected = sum(len(e.selected_memory_ids or []) for e in events)
        metrics.mean_returned = round(total_returned / len(events), 3)
        metrics.mean_selected = round(total_selected / len(events), 3)
        metrics.mean_context_tokens = round(
            sum(e.context_tokens for e in events) / len(events), 1
        )
        metrics.mean_latency_ms = round(sum(e.latency_ms for e in events) / len(events), 2)

        if total_returned:
            metrics.stale_hit_rate = round(
                sum(e.stale_returned_count for e in events) / total_returned, 4
            )
            metrics.conflicted_hit_rate = round(
                sum(e.conflicted_returned_count for e in events) / total_returned, 4
            )

        with_usage = [e for e in events if e.used_memory_ids]
        if with_usage:
            precision_values: list[float] = []
            recall_values: list[float] = []
            redundancy_values: list[float] = []

            for event in with_usage:
                selected = set(event.selected_memory_ids or [])
                returned = set(event.returned_memory_ids or [])
                used = set(event.used_memory_ids or [])

                if selected:
                    precision_values.append(len(selected & used) / len(selected))
                    redundancy_values.append(len(selected - used) / len(selected))
                if used:
                    recall_values.append(len(used & returned) / len(used))

            if precision_values:
                metrics.precision = round(sum(precision_values) / len(precision_values), 4)
            if recall_values:
                metrics.recall = round(sum(recall_values) / len(recall_values), 4)
            if redundancy_values:
                metrics.redundancy = round(sum(redundancy_values) / len(redundancy_values), 4)
        else:
            metrics.notes.append(
                f"{len(events)} retrieval event(s) recorded, but none reports which "
                "memories the agent used, so precision, recall and redundancy are "
                "unmeasured."
            )

        outcomes = [e for e in events if e.task_outcome]
        if outcomes:
            successes = sum(1 for e in outcomes if e.task_outcome.upper() == "SUCCESS")
            metrics.task_success_rate = round(successes / len(outcomes), 4)
        else:
            metrics.notes.append(
                "No retrieval event carries a task outcome, so the correlation "
                "between retrieval and task success is unmeasured."
            )

        return metrics

    async def memory_usefulness(
        self, session: AsyncSession, project_id: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Per-memory record of how often it was returned, selected and used.

        A memory retrieved constantly and never used is noise the composer should
        be de-prioritising; this is the evidence for that judgement.
        """
        res = await session.execute(
            select(RetrievalEvent)
            .where(RetrievalEvent.project_id == project_id)
            .order_by(RetrievalEvent.created_at.desc())
            .limit(limit)
        )
        events = list(res.scalars().all())

        stats: dict[str, dict[str, int]] = {}
        for event in events:
            for memory_id in event.returned_memory_ids or []:
                stats.setdefault(memory_id, {"returned": 0, "selected": 0, "used": 0})
                stats[memory_id]["returned"] += 1
            for memory_id in event.selected_memory_ids or []:
                stats.setdefault(memory_id, {"returned": 0, "selected": 0, "used": 0})
                stats[memory_id]["selected"] += 1
            for memory_id in event.used_memory_ids or []:
                stats.setdefault(memory_id, {"returned": 0, "selected": 0, "used": 0})
                stats[memory_id]["used"] += 1

        rows = []
        for memory_id, counts in stats.items():
            selected = counts["selected"]
            rows.append(
                {
                    "memory_id": memory_id,
                    **counts,
                    # None, not 0.0: a memory never selected has no usefulness
                    # ratio to report.
                    "usefulness": (
                        round(counts["used"] / selected, 4) if selected else None
                    ),
                }
            )

        rows.sort(key=lambda row: (row["selected"], row["returned"]), reverse=True)
        return rows

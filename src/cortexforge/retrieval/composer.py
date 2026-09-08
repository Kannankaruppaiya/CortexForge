from typing import Any, Self

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.graph.service import GraphService
from cortexforge.retrieval.engine import HybridRetrievalEngine, ScoredItem

PROFILE_BUDGETS = {
    "small": 1200,
    "medium": 3500,
    "large": 8000,
    "custom": 3500,
}


class ContextMemoryItem(BaseModel):
    id: str
    title: str
    layer: str
    memory_type: str
    confidence: float
    provenance: str | None = None
    reason: str


class ContextPacket(BaseModel):
    """Structured, explainable context report and prompt injection packet."""

    project_id: str
    project_name: str
    profile: str
    token_budget: int
    estimated_tokens: int
    context_markdown: str
    selected_memories: list[ContextMemoryItem]
    excluded_memories: list[ContextMemoryItem]
    stale_warnings: list[str]
    conflict_warnings: list[str]
    explainability_report: str


class ComposedContext(str):
    """String subclass containing context markdown plus attached explainability metadata."""

    profile: str
    token_budget: int
    estimated_tokens: int
    selected_memories: list[dict[str, Any]]
    excluded_memories: list[dict[str, Any]]
    stale_warnings: list[str]
    conflict_warnings: list[str]
    explainability_report: str

    def __new__(cls, content: str, **kwargs: Any) -> Self:
        instance = super().__new__(cls, content)
        for k, v in kwargs.items():
            setattr(instance, k, v)
        return instance


class ContextComposer:
    """Composes structured, compact, token-bounded context blocks for agent prompt injection."""

    def __init__(
        self,
        retrieval_engine: HybridRetrievalEngine | None = None,
        graph_service: GraphService | None = None,
    ) -> None:
        self.retrieval_engine = retrieval_engine or HybridRetrievalEngine()
        self.graph_service = graph_service or GraphService()

    async def build_context(
        self,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        profile: str = "medium",  # "small", "medium", "large", "custom"
        target_files: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> ComposedContext:
        """Compose structured markdown context and explainability report."""
        packet = await self.compose_context_packet(
            session=session,
            project_id=project_id,
            task_text=task_text,
            profile=profile,
            target_files=target_files,
            max_tokens=max_tokens,
        )

        return ComposedContext(
            packet.context_markdown,
            profile=packet.profile,
            token_budget=packet.token_budget,
            estimated_tokens=packet.estimated_tokens,
            selected_memories=[m.model_dump() for m in packet.selected_memories],
            excluded_memories=[m.model_dump() for m in packet.excluded_memories],
            stale_warnings=packet.stale_warnings,
            conflict_warnings=packet.conflict_warnings,
            explainability_report=packet.explainability_report,
        )

    async def compose_context_packet(
        self,
        session: AsyncSession,
        project_id: str,
        task_text: str,
        profile: str = "medium",
        target_files: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> ContextPacket:
        """Construct full structured ContextPacket with explainability and budgeting."""
        norm_profile = profile.lower()
        budget = max_tokens or PROFILE_BUDGETS.get(norm_profile, 3500)

        # Retrieve candidates with safety margin
        retrieval_limit = 10 if norm_profile == "small" else (20 if norm_profile == "medium" else 40)
        items: list[ScoredItem] = await self.retrieval_engine.retrieve(
            session,
            project_id=project_id,
            query=task_text,
            target_files=target_files,
            limit=retrieval_limit,
        )

        arch = await self.graph_service.get_project_architecture(session, project_id, depth=2)
        project_name = arch.project_name if arch else "Project"

        # Categorize retrieved items into cognitive layers
        decisions: list[ScoredItem] = []
        constraints: list[ScoredItem] = []
        failures: list[ScoredItem] = []
        working_state: list[ScoredItem] = []
        lessons: list[ScoredItem] = []
        stale_items: list[ScoredItem] = []
        conflicted_items: list[ScoredItem] = []

        for item in items:
            layer = (item.layer or "L1").upper()
            m_type = (item.memory_type or "").upper()

            if item.status == "CONFLICTED":
                conflicted_items.append(item)
            elif item.status == "STALE":
                stale_items.append(item)
            elif layer == "L6" or m_type == "WORKING_STATE":
                working_state.append(item)
            elif layer == "L3" or m_type == "DECISION":
                decisions.append(item)
            elif m_type == "CONSTRAINT":
                constraints.append(item)
            elif layer == "L4" or m_type in ("FAILURE", "FIX"):
                failures.append(item)
            elif layer == "L5" or m_type in ("LESSON", "CONVENTION", "PATTERN"):
                lessons.append(item)
            else:
                lessons.append(item)

        # Apply budgeting quota per section
        selected_memories: list[ContextMemoryItem] = []
        excluded_memories: list[ContextMemoryItem] = []

        def select_top(pool: list[ScoredItem], quota: int, reason_prefix: str) -> list[ScoredItem]:
            accepted = pool[:quota]
            for it in accepted:
                selected_memories.append(
                    ContextMemoryItem(
                        id=it.id,
                        title=it.title,
                        layer=it.layer or "L1",
                        memory_type=it.memory_type or "KNOWLEDGE",
                        confidence=it.breakdown.get("conf", 1.0),
                        provenance=it.provenance,
                        reason=f"{reason_prefix} (score={it.score})",
                    )
                )
            for it in pool[quota:]:
                excluded_memories.append(
                    ContextMemoryItem(
                        id=it.id,
                        title=it.title,
                        layer=it.layer or "L1",
                        memory_type=it.memory_type or "KNOWLEDGE",
                        confidence=it.breakdown.get("conf", 1.0),
                        provenance=it.provenance,
                        reason=f"Exceeded quota for {reason_prefix}",
                    )
                )
            return accepted

        # Quotas based on profile
        q_dec = 2 if norm_profile == "small" else (4 if norm_profile == "medium" else 8)
        q_fail = 2 if norm_profile == "small" else (4 if norm_profile == "medium" else 6)
        q_const = 2 if norm_profile == "small" else (4 if norm_profile == "medium" else 6)
        q_less = 2 if norm_profile == "small" else (4 if norm_profile == "medium" else 8)
        q_work = 2 if norm_profile == "small" else (3 if norm_profile == "medium" else 5)

        active_decisions = select_top(decisions, q_dec, "Top architectural decision")
        active_failures = select_top(failures, q_fail, "Historical failure to avoid")
        active_constraints = select_top(constraints, q_const, "Active operational constraint")
        active_working_state = select_top(working_state, q_work, "Active working state")
        active_lessons = select_top(lessons, q_less, "Durable project lesson")

        # Collect affected components from graph
        affected_components = []
        if target_files:
            for tf in target_files:
                deps = await self.graph_service.get_dependencies(session, project_id, tf, depth=1)
                callers = await self.graph_service.get_dependents(session, project_id, tf, depth=1)
                for d in deps[:3]:
                    affected_components.append(f"{d['name']} (dep)")
                for c in callers[:3]:
                    affected_components.append(f"{c['name']} (caller)")

        # Build Context Markdown
        lines = [
            "<!-- CORTEXFORGE VERIFIED PROJECT CONTEXT -->",
            f"# PROJECT CONTEXT: {project_name}",
            "",
            "## Current Task",
            f"{task_text.strip()}",
            "",
        ]

        # Active Working State (L6)
        if active_working_state:
            lines.append("## Active Working State (L6)")
            for w in active_working_state:
                lines.append(f"- **{w.title}**: {w.summary}")
            lines.append("")

        # Relevant Architecture (L1)
        if arch and norm_profile in ("medium", "large"):
            lines.append("## Relevant Architecture (L1)")
            mod_names = [m.module_path for m in arch.modules[:4]]
            lines.append(f"- **Active Modules**: {', '.join(mod_names)}")
            if arch.primary_apis:
                lines.append(f"- **Key APIs**: {', '.join(a.name for a in arch.primary_apis[:3])}")
            lines.append("")

        # Relevant Decisions (L3)
        if active_decisions:
            lines.append("## Relevant Decisions (L3)")
            for d in active_decisions:
                lines.append(f"- **{d.title.replace('[DECISION] ', '')}**: {d.summary}")
            lines.append("")

        # Relevant Constraints (L5)
        if active_constraints:
            lines.append("## Relevant Constraints")
            for c in active_constraints:
                lines.append(f"- **{c.title.replace('[CONSTRAINT] ', '')}**: {c.summary}")
            lines.append("")

        # Previous Failures & Anti-Patterns (L4)
        if active_failures:
            lines.append("## Previous Failures & Anti-Patterns (Avoid Repeating) (L4)")
            for f in active_failures:
                lines.append(f"- **{f.title.replace('[FAILURE] ', '').replace('[FIX] ', '')}**: {f.summary}")
                if norm_profile in ("medium", "large"):
                    lines.append(f"  *Cause & Prevention*: {f.content[:180]}...")
            lines.append("")

        # Affected Components & Blast Radius
        if affected_components:
            lines.append("## Affected Components & Blast Radius")
            lines.append(f"- {', '.join(list(set(affected_components))[:8])}")
            lines.append("")

        # Conventions & Durable Lessons (L2/L5)
        if active_lessons and norm_profile in ("medium", "large"):
            lines.append("## Project Conventions & Durable Lessons (L2/L5)")
            for item in active_lessons:
                clean_t = item.title
                for pfx in ["[CONVENTION] ", "[LESSON] ", "[ARCH] ", "[NOTE] "]:
                    clean_t = clean_t.replace(pfx, "")
                desc = item.summary or (item.content[:140] + ("..." if len(item.content) > 140 else ""))
                lines.append(f"- **{clean_t}**: {desc}")
            lines.append("")

        # Warnings: Stale and Conflicted
        stale_warnings_out = []
        if stale_items:
            lines.append("## [WARNING] Potentially Stale Memories Detected")
            for s in stale_items[:3]:
                msg = f"`{s.title}`: Referenced code modified since last verification."
                stale_warnings_out.append(msg)
                lines.append(f"- {msg} Re-verify before making assumptions.")
            lines.append("")

        conflict_warnings_out = []
        if conflicted_items:
            lines.append("## [CAUTION] Conflicted / Contradictory Memories Detected")
            for c in conflicted_items[:3]:
                msg = f"`{c.title}`: Disputed by newer evidence or contradictory memory."
                conflict_warnings_out.append(msg)
                lines.append(f"- {msg}")
            lines.append("")

        lines.append("<!-- END CORTEXFORGE CONTEXT -->")
        raw_text = "\n".join(lines)

        # Enforce Token Budget
        words = raw_text.split()
        max_words = int(budget * 0.75)
        if len(words) > max_words:
            raw_text = " ".join(words[:max_words]) + "\n\n<!-- Truncated to fit token budget -->\n<!-- END CORTEXFORGE CONTEXT -->"

        estimated_tokens = int(len(raw_text.split()) * 1.33)

        explainability_report = (
            f"Context Profile: {norm_profile.upper()} (Budget: {budget} tokens, Estimated: {estimated_tokens} tokens)\n"
            f"Selected Memories: {len(selected_memories)} | Excluded: {len(excluded_memories)}\n"
            f"Stale Warnings: {len(stale_warnings_out)} | Conflict Warnings: {len(conflict_warnings_out)}"
        )

        return ContextPacket(
            project_id=project_id,
            project_name=project_name,
            profile=norm_profile,
            token_budget=budget,
            estimated_tokens=estimated_tokens,
            context_markdown=raw_text,
            selected_memories=selected_memories,
            excluded_memories=excluded_memories,
            stale_warnings=stale_warnings_out,
            conflict_warnings=conflict_warnings_out,
            explainability_report=explainability_report,
        )

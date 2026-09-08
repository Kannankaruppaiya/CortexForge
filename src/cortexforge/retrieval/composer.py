"""Token-budget-aware structured context composer for AI coding agents."""

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.graph.service import GraphService
from cortexforge.retrieval.engine import HybridRetrievalEngine, ScoredItem


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
        profile: str = "medium",  # "small", "medium", "large"
        target_files: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Compose structured markdown context for the given task."""
        # Token budgets
        budget_map = {"small": 1200, "medium": 3500, "large": 8000}
        target_token_budget = max_tokens or budget_map.get(profile.lower(), 3500)

        # Retrieve top relevant memories and entities
        retrieval_limit = 5 if profile == "small" else (12 if profile == "medium" else 25)
        items: list[ScoredItem] = await self.retrieval_engine.retrieve(
            session,
            project_id=project_id,
            query=task_text,
            target_files=target_files,
            limit=retrieval_limit,
        )

        arch = await self.graph_service.get_project_architecture(session, project_id, depth=2)
        project_name = arch.project_name if arch else "Project"

        # Categorize retrieved items
        decisions: list[ScoredItem] = []
        constraints: list[ScoredItem] = []
        failures: list[ScoredItem] = []
        stale_warnings: list[ScoredItem] = []
        other_lessons: list[ScoredItem] = []

        for item in items:
            if item.status == "STALE":
                stale_warnings.append(item)
            elif "[DECISION]" in item.title:
                decisions.append(item)
            elif "[CONSTRAINT]" in item.title:
                constraints.append(item)
            elif "[FAILURE]" in item.title or "[FIX]" in item.title:
                failures.append(item)
            else:
                other_lessons.append(item)

        # Collect affected components if target_files provided
        affected_components = []
        if target_files:
            for tf in target_files:
                deps = await self.graph_service.get_dependencies(session, project_id, tf, depth=1)
                callers = await self.graph_service.get_dependents(session, project_id, tf, depth=1)
                for d in deps[:3]:
                    affected_components.append(f"{d['name']} (dep)")
                for c in callers[:3]:
                    affected_components.append(f"{c['name']} (caller)")

        # Build Structured Output
        lines = [
            "<!-- CORTEXFORGE VERIFIED PROJECT CONTEXT -->",
            f"# PROJECT CONTEXT: {project_name}",
            "",
            "## Current Task",
            f"{task_text.strip()}",
            "",
        ]

        # Relevant Architecture
        if arch and profile in ("medium", "large"):
            lines.append("## Relevant Architecture")
            mod_names = [m.module_path for m in arch.modules[:4]]
            lines.append(f"- **Active Modules**: {', '.join(mod_names)}")
            if arch.primary_apis:
                lines.append(f"- **Key APIs**: {', '.join(a.name for a in arch.primary_apis[:3])}")
            lines.append("")

        # Relevant Decisions (L3)
        if decisions:
            lines.append("## Relevant Decisions")
            for d in decisions[:4]:
                lines.append(f"- **{d.title.replace('[DECISION] ', '')}**: {d.summary}")
            lines.append("")

        # Relevant Constraints (L5)
        if constraints:
            lines.append("## Relevant Constraints")
            for c in constraints[:4]:
                lines.append(f"- **{c.title.replace('[CONSTRAINT] ', '')}**: {c.summary}")
            lines.append("")

        # Previous Failures & Anti-patterns (L4)
        if failures:
            lines.append("## Previous Failures & Anti-Patterns (Avoid Repeating)")
            for f in failures[:4]:
                lines.append(f"- **{f.title.replace('[FAILURE] ', '').replace('[FIX] ', '')}**: {f.summary}")
                if profile in ("medium", "large"):
                    lines.append(f"  *Cause & Prevention*: {f.content[:200]}...")
            lines.append("")

        # Affected Components
        if affected_components:
            lines.append("## Affected Components & Blast Radius")
            lines.append(f"- {', '.join(list(set(affected_components))[:8])}")
            lines.append("")

        # Stale Memory Warning
        if stale_warnings:
            lines.append("## [WARNING] Potentially Stale Memories Detected")
            for s in stale_warnings[:3]:
                lines.append(f"- `{s.title}` was flagged STALE because referenced code changed. Re-verify before relying on this pattern.")
            lines.append("")

        lines.append("<!-- END CORTEXFORGE CONTEXT -->")
        out = "\n".join(lines)
        words = out.split()
        max_words = int(target_token_budget * 0.75)
        if len(words) > max_words:
            out = " ".join(words[:max_words]) + "\n\n<!-- Truncated to fit token budget -->\n<!-- END CORTEXFORGE CONTEXT -->"
        return out

"""Semantic change propagation engine mapping Git diffs to code graph and memories."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import CodeEntity, Memory
from cortexforge.graph.service import GraphService


@dataclass
class ChangeImpactReport:
    modified_files: list[str]
    directly_changed_entities: list[str]
    affected_dependents: list[str]
    memories_flagged_stale: list[str]
    critical_constraints: list[str]
    warnings: list[str] = field(default_factory=list)


class SemanticChangePropagator:
    """Propagates code modifications across the graph to invalidate or flag stale memories."""

    def __init__(self, graph_service: GraphService | None = None) -> None:
        self.graph_service = graph_service or GraphService()

    async def propagate_changes(
        self,
        session: AsyncSession,
        project_id: str,
        modified_files: list[str],
        mark_stale: bool = True,
    ) -> ChangeImpactReport:
        """Analyze impact of modified files and update affected memory states."""
        # 1. Match modified files to code entities
        normalized_files = [f.replace("\\", "/") for f in modified_files]
        entities_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        res = await session.execute(entities_stmt)
        all_entities = list(res.scalars().all())

        changed_entities: list[CodeEntity] = []
        for ent in all_entities:
            if any(ent.file_path == nf or nf.endswith(ent.file_path) for nf in normalized_files):
                changed_entities.append(ent)

        # 2. Graph propagation: find all upstream callers and dependents (blast radius)
        affected_dependent_names: set[str] = set()
        for cent in changed_entities:
            callers = await self.graph_service.get_dependents(
                session, project_id=project_id, entity_name_or_id=cent.id, depth=2
            )
            for c in callers:
                affected_dependent_names.add(f"{c['name']} ({c['file']})")

        # 3. Find affected memories grounded in changed files or entities
        memories_stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status == "ACTIVE",
            )
        )
        mres = await session.execute(memories_stmt)
        active_memories = list(mres.scalars().all())

        stale_memory_titles: list[str] = []
        critical_constraints: list[str] = []
        warnings: list[str] = []

        for mem in active_memories:
            grounded_in_change = False
            if mem.evidences:
                for ev in mem.evidences:
                    ev_norm = ev.file_path.replace("\\", "/")
                    if any(ev_norm == nf or nf.endswith(ev_norm) for nf in normalized_files):
                        grounded_in_change = True
                        break

            if grounded_in_change:
                stale_memory_titles.append(f"[{mem.memory_type}] {mem.title}")
                if mem.memory_type == "CONSTRAINT":
                    critical_constraints.append(f"CONSTRAINT: {mem.title} - {mem.summary}")
                elif mem.memory_type == "FAILURE":
                    warnings.append(f"KNOWN FAILURE AREA: {mem.title} - {mem.summary}")

                if mark_stale:
                    mem.status = "STALE"
                    mem.updated_at = datetime.now(UTC)

        if mark_stale and stale_memory_titles:
            await session.commit()

        return ChangeImpactReport(
            modified_files=normalized_files,
            directly_changed_entities=[e.qualified_name for e in changed_entities],
            affected_dependents=sorted(affected_dependent_names),
            memories_flagged_stale=stale_memory_titles,
            critical_constraints=critical_constraints,
            warnings=warnings,
        )

"""Graph traversal and project architecture synthesis service."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import CodeEntity, Project, Relationship
from cortexforge.core.schemas import (
    ArchitectureResponse,
    ComponentSummary,
    ModuleSummary,
)


class GraphService:
    """Relational graph query and architecture synthesis service."""

    # Entity types that represent physical container nodes rather than inner code/config entities.
    # In CortexForge's graph model, "file" entities represent the physical file container itself
    # (already summarized via file_count) rather than a code component within that file.
    NON_DISPLAYABLE_ENTITY_TYPES: frozenset[str] = frozenset({"file"})

    # Maximum number of top-level components exposed per module summary to maintain balanced payloads.
    MAX_TOP_LEVEL_COMPONENTS: int = 25

    async def get_project_architecture(
        self, session: AsyncSession, project_id: str, depth: int = 2
    ) -> ArchitectureResponse | None:
        """Synthesize high-level structural model of the project."""
        project = await session.get(Project, project_id)
        if not project:
            return None

        # Fetch all code entities for the project
        entities_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        entities_res = await session.execute(entities_stmt)
        all_entities = list(entities_res.scalars().all())

        # Fetch all relationships
        rels_stmt = select(Relationship).where(Relationship.project_id == project_id)
        rels_res = await session.execute(rels_stmt)
        all_rels = list(rels_res.scalars().all())

        # Build adjacency maps
        entity_by_id: dict[str, CodeEntity] = {e.id: e for e in all_entities}
        outgoing: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, list[str]] = defaultdict(list)

        for rel in all_rels:
            src = entity_by_id.get(rel.source_entity_id)
            tgt = entity_by_id.get(rel.target_entity_id)
            if src and tgt:
                outgoing[src.qualified_name].append(tgt.name)
                incoming[tgt.qualified_name].append(src.name)

        # Partition entities into modules by top-level directory
        modules_map: dict[str, list[CodeEntity]] = defaultdict(list)
        files_set: set[str] = set()
        languages_set: set[str] = set()

        for entity in all_entities:
            files_set.add(entity.file_path)
            if entity.language != "unknown":
                languages_set.add(entity.language)

            parts = entity.file_path.replace("\\", "/").split("/")
            module_name = parts[0] if len(parts) > 1 else "root"
            if len(parts) > 2 and parts[0] in ("src", "packages", "apps", "libs"):
                module_name = f"{parts[0]}/{parts[1]}"
            modules_map[module_name].append(entity)

        module_summaries: list[ModuleSummary] = []
        primary_apis: list[ComponentSummary] = []
        primary_models: list[ComponentSummary] = []

        for mod_name, mod_entities in sorted(modules_map.items()):
            mod_files = {e.file_path for e in mod_entities}
            top_components: list[ComponentSummary] = []

            # Sort entities deterministically by (file_path, start_line, name)
            sorted_entities = sorted(
                mod_entities,
                key=lambda e: (e.file_path or "", e.start_line or 0, e.name or ""),
            )

            for e in sorted_entities:
                if e.entity_type not in self.NON_DISPLAYABLE_ENTITY_TYPES:
                    comp = ComponentSummary(
                        name=e.name,
                        qualified_name=e.qualified_name,
                        entity_type=e.entity_type,
                        file_path=e.file_path,
                        line_range=[e.start_line, e.end_line],
                        signature=e.signature,
                        dependencies=sorted(set(outgoing.get(e.qualified_name, [])))[:10],
                        dependents=sorted(set(incoming.get(e.qualified_name, [])))[:10],
                    )
                    top_components.append(comp)

                    # Identify likely APIs or models
                    lower_name = e.name.lower()
                    if (
                        "api" in lower_name
                        or "route" in lower_name
                        or "controller" in lower_name
                        or "service" in lower_name
                        or e.entity_type == "api"
                    ):
                        primary_apis.append(comp)
                    elif (
                        "model" in lower_name
                        or "schema" in lower_name
                        or e.entity_type in ("model", "interface")
                    ):
                        primary_models.append(comp)

            module_summaries.append(
                ModuleSummary(
                    module_path=mod_name,
                    file_count=len(mod_files),
                    entity_count=len(mod_entities),
                    top_level_components=top_components[: self.MAX_TOP_LEVEL_COMPONENTS],
                )
            )

        return ArchitectureResponse(
            project_id=project.id,
            project_name=project.name,
            total_files=len(files_set),
            total_entities=len(all_entities),
            total_relationships=len(all_rels),
            languages=sorted(languages_set),
            modules=module_summaries,
            primary_apis=primary_apis[:10],
            primary_models=primary_models[:10],
        )

    async def resolve_entity(
        self, session: AsyncSession, project_id: str, entity_name_or_id: str
    ) -> tuple[str, CodeEntity | None, list[str]]:
        """Resolve an entity by ID, qualified name, or short name with ambiguity detection (Item 9).

        Returns:
            (status, entity, candidate_qualified_names)
            where status is "RESOLVED", "AMBIGUOUS", or "NOT_FOUND".
        """
        # 1. Exact ID match
        id_stmt = select(CodeEntity).where(
            CodeEntity.project_id == project_id,
            CodeEntity.id == entity_name_or_id,
        )
        exact_id = (await session.execute(id_stmt)).scalars().first()
        if exact_id:
            return "RESOLVED", exact_id, []

        # 2. Exact qualified_name match
        qn_stmt = select(CodeEntity).where(
            CodeEntity.project_id == project_id,
            CodeEntity.qualified_name == entity_name_or_id,
        )
        exact_qn = list((await session.execute(qn_stmt)).scalars().all())
        if len(exact_qn) == 1:
            return "RESOLVED", exact_qn[0], []
        elif len(exact_qn) > 1:
            candidates = [f"{m.file_path}:{m.qualified_name}" for m in exact_qn]
            return "AMBIGUOUS", None, candidates

        # 3. Short name or suffix match -- check for ambiguity
        name_stmt = select(CodeEntity).where(
            CodeEntity.project_id == project_id,
            (CodeEntity.name == entity_name_or_id)
            | (CodeEntity.qualified_name.endswith(f":{entity_name_or_id}"))
            | (CodeEntity.qualified_name.endswith(f".{entity_name_or_id}")),
        )
        matches = list((await session.execute(name_stmt)).scalars().all())
        if len(matches) == 1:
            return "RESOLVED", matches[0], []
        elif len(matches) > 1:
            candidates = [m.qualified_name for m in matches]
            return "AMBIGUOUS", None, candidates

        return "NOT_FOUND", None, []

    async def get_dependencies(
        self,
        session: AsyncSession,
        project_id: str,
        entity_name_or_id: str,
        depth: int = 2,
    ) -> list[dict[str, str]]:
        """Resolve downstream dependencies for a given entity."""
        status, start_entity, _ = await self.resolve_entity(
            session, project_id, entity_name_or_id
        )
        if status != "RESOLVED" or not start_entity:
            return []

        visited: set[str] = {start_entity.id}
        frontier: list[str] = [start_entity.id]
        results: list[dict[str, str]] = []

        for current_depth in range(1, depth + 1):
            if not frontier:
                break
            next_frontier = []
            rel_stmt = (
                select(Relationship, CodeEntity)
                .join(CodeEntity, Relationship.target_entity_id == CodeEntity.id)
                .where(
                    Relationship.project_id == project_id,
                    Relationship.source_entity_id.in_(frontier),
                )
            )
            rel_res = await session.execute(rel_stmt)
            for rel, target_entity in rel_res.all():
                if target_entity.id not in visited:
                    visited.add(target_entity.id)
                    next_frontier.append(target_entity.id)
                    results.append(
                        {
                            "name": target_entity.name,
                            "qualified_name": target_entity.qualified_name,
                            "type": target_entity.entity_type,
                            "file": target_entity.file_path,
                            "relationship": rel.relationship_type,
                            "depth": str(current_depth),
                        }
                    )
            frontier = next_frontier

        return results

    async def get_dependents(
        self,
        session: AsyncSession,
        project_id: str,
        entity_name_or_id: str,
        depth: int = 2,
    ) -> list[dict[str, str]]:
        """Resolve upstream callers and dependents (blast radius) for a given entity."""
        status, start_entity, _ = await self.resolve_entity(
            session, project_id, entity_name_or_id
        )
        if status != "RESOLVED" or not start_entity:
            return []

        visited: set[str] = {start_entity.id}
        frontier: list[str] = [start_entity.id]
        results: list[dict[str, str]] = []

        for current_depth in range(1, depth + 1):
            if not frontier:
                break
            next_frontier = []
            rel_stmt = (
                select(Relationship, CodeEntity)
                .join(CodeEntity, Relationship.source_entity_id == CodeEntity.id)
                .where(
                    Relationship.project_id == project_id,
                    Relationship.target_entity_id.in_(frontier),
                )
            )
            rel_res = await session.execute(rel_stmt)
            for rel, source_entity in rel_res.all():
                if source_entity.id not in visited:
                    visited.add(source_entity.id)
                    next_frontier.append(source_entity.id)
                    results.append(
                        {
                            "name": source_entity.name,
                            "qualified_name": source_entity.qualified_name,
                            "type": source_entity.entity_type,
                            "file": source_entity.file_path,
                            "relationship": rel.relationship_type,
                            "depth": str(current_depth),
                        }
                    )
            frontier = next_frontier

        return results

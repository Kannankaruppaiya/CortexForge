"""Model Context Protocol (MCP) Server for CortexForge."""

import os

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import CodeEntity, Project
from cortexforge.graph.service import GraphService

mcp_server = MCPServer(
    name="cortexforge",
    instructions="CortexForge verified project memory layer. Query architecture, components, and graph dependencies without re-exploring the entire repository.",
)

scanner = RepositoryScanner()
graph_service = GraphService()


async def _resolve_project(session, project_id_or_path: str) -> Project | None:
    """Resolve project by ID or by local filesystem path, creating and scanning if needed."""
    # Try ID first
    project = await session.get(Project, project_id_or_path)
    if project:
        return project

    # Try path
    canonical_path = os.path.realpath(project_id_or_path)
    stmt = select(Project).where(Project.local_path == canonical_path)
    res = await session.execute(stmt)
    project = res.scalars().first()
    if not project and os.path.exists(canonical_path):
        name = os.path.basename(canonical_path) or "project"
        project = Project(
            name=name,
            local_path=canonical_path,
            status="INITIALIZING",
        )
        session.add(project)
        await session.flush()
        await scanner.scan_project(session, project, incremental=False)
        await session.refresh(project)

    return project


@mcp_server.tool(
    name="project_get_architecture",
    description="Returns high-level structural architecture of the project (modules, primary APIs, models, and dependencies).",
)
async def project_get_architecture(
    project_id_or_path: str = ".", depth: int = 2
) -> str:
    """Retrieve synthesized structural architecture of the project."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        arch = await graph_service.get_project_architecture(
            session, project.id, depth=depth
        )
        if not arch:
            return f"Error: Architecture not found for project '{project.name}'."

        # Format as high-density structured markdown
        lines = [
            f"# Project Architecture: {arch.project_name}",
            f"- **Files**: {arch.total_files} | **Entities**: {arch.total_entities} | **Relationships**: {arch.total_relationships}",
            f"- **Languages**: {', '.join(arch.languages)}",
            "",
            "## Modules",
        ]
        for mod in arch.modules:
            lines.append(f"### {mod.module_path} ({mod.file_count} files, {mod.entity_count} symbols)")
            for comp in mod.top_level_components[:8]:
                deps_str = f" -> [{', '.join(comp.dependencies[:3])}]" if comp.dependencies else ""
                lines.append(f"  - `{comp.entity_type}` **{comp.name}** ({comp.file_path}:{comp.line_range[0]}-{comp.line_range[1]}){deps_str}")
            lines.append("")

        if arch.primary_apis:
            lines.append("## Primary APIs / Entrypoints")
            for api in arch.primary_apis:
                lines.append(f"- **{api.name}** (`{api.file_path}`): {api.signature or ''}")
            lines.append("")

        if arch.primary_models:
            lines.append("## Primary Data Models")
            for m in arch.primary_models:
                lines.append(f"- **{m.name}** (`{m.file_path}`)")
            lines.append("")

        return "\n".join(lines)


@mcp_server.tool(
    name="project_get_component",
    description="Inspects detailed AST symbol definition, signature, location, dependencies, and callers.",
)
async def project_get_component(
    qualified_name: str, project_id_or_path: str = "."
) -> str:
    """Retrieve detailed information on a specific code component."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        stmt = select(CodeEntity).where(
            CodeEntity.project_id == project.id,
            (CodeEntity.qualified_name == qualified_name)
            | (CodeEntity.name == qualified_name)
            | (CodeEntity.qualified_name.endswith(f":{qualified_name}"))
            | (CodeEntity.qualified_name.endswith(f".{qualified_name}")),
        )
        res = await session.execute(stmt)
        entity = res.scalars().first()
        if not entity:
            return f"Component '{qualified_name}' not found in project '{project.name}'."

        deps = await graph_service.get_dependencies(session, project.id, entity.id, depth=2)
        callers = await graph_service.get_dependents(session, project.id, entity.id, depth=2)

        out = [
            f"# Component: {entity.name} ({entity.entity_type})",
            f"- **Qualified Name**: `{entity.qualified_name}`",
            f"- **File**: `{entity.file_path}` (Lines {entity.start_line}-{entity.end_line})",
            f"- **Language**: {entity.language}",
            f"- **Signature**: `{entity.signature or 'N/A'}`",
            f"- **Content Hash**: `{entity.content_hash[:16]}...`",
            "",
            f"## Downstream Dependencies ({len(deps)})",
        ]
        for d in deps[:10]:
            out.append(f"- `{d['relationship']}` -> **{d['name']}** ({d['file']})")

        out.append("")
        out.append(f"## Upstream Callers & Dependents ({len(callers)})")
        for c in callers[:10]:
            out.append(f"- **{c['name']}** (`{c['file']}`) -> `{c['relationship']}`")

        return "\n".join(out)


@mcp_server.tool(
    name="graph_get_dependencies",
    description="Returns all downstream dependencies of an entity up to depth N.",
)
async def graph_get_dependencies(
    entity_name: str, project_id_or_path: str = ".", depth: int = 2
) -> str:
    """List downstream dependencies for an entity."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        deps = await graph_service.get_dependencies(
            session, project.id, entity_name, depth=depth
        )
        if not deps:
            return f"No dependencies found for entity '{entity_name}'."

        lines = [f"# Dependencies for {entity_name} (depth <= {depth})"]
        for d in deps:
            lines.append(f"- [Depth {d['depth']}] `{d['relationship']}` -> **{d['name']}** ({d['type']} in `{d['file']}`)")
        return "\n".join(lines)


@mcp_server.tool(
    name="graph_get_dependents",
    description="Returns all upstream callers and consumers of an entity to evaluate blast radius.",
)
async def graph_get_dependents(
    entity_name: str, project_id_or_path: str = ".", depth: int = 2
) -> str:
    """List upstream dependents (blast radius) for an entity."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        callers = await graph_service.get_dependents(
            session, project.id, entity_name, depth=depth
        )
        if not callers:
            return f"No callers or dependents found for entity '{entity_name}'."

        lines = [f"# Callers & Dependents for {entity_name} (blast radius depth <= {depth})"]
        for c in callers:
            lines.append(f"- [Depth {c['depth']}] **{c['name']}** ({c['type']} in `{c['file']}`) -> `{c['relationship']}`")
        return "\n".join(lines)


@mcp_server.resource("cortex://project/architecture")
async def resource_architecture() -> str:
    """Resource returning the current project architecture."""
    return await project_get_architecture(".")


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp_server.run()


if __name__ == "__main__":
    main()

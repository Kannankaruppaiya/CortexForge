"""Full-Featured Model Context Protocol (MCP) Server for CortexForge."""

import os

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func, select

from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import CodeEntity, Memory, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.graph.service import GraphService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.provenance import ProvenanceEngine
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine

mcp_server = MCPServer(
    name="cortexforge",
    instructions=(
        "CortexForge: Continuously evolving, verified project memory layer for AI coding agents. "
        "Query architecture, retrieve decisions/constraints/failures, record outcomes, "
        "and inspect blast radius before taking high-risk code modifications."
    ),
)

scanner = RepositoryScanner()
graph_service = GraphService()
memory_service = MemoryService()
verification_engine = MemoryVerificationEngine()
consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)
retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
context_composer = ContextComposer(retrieval_engine=retrieval_engine, graph_service=graph_service)
change_propagator = SemanticChangePropagator(graph_service=graph_service)
invariant_engine = ArchitectureInvariantEngine()
provenance_engine = ProvenanceEngine()
snapshot_engine = CognitiveSnapshotEngine()
orchestrator = AgentWorkflowOrchestrator(
    memory_service=memory_service,
    composer=context_composer,
    propagator=change_propagator,
    verifier=verification_engine,
    consolidator=consolidation_engine,
)



async def _resolve_project(session, project_id_or_path: str) -> Project | None:
    """Resolve project by ID or local filesystem path, automatically initializing and scanning if needed."""
    project = await session.get(Project, project_id_or_path)
    if project:
        return project

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


# ==================== 1. PROJECT ARCHITECTURE & CONTEXT TOOLS ====================

@mcp_server.tool(
    name="project_get_context",
    description="Returns structured, token-budget-aware project context (architecture, active decisions, constraints, previous failures, and warnings) for a planned task.",
)
async def project_get_context(
    task_text: str,
    profile: str = "medium",
    target_files: list[str] | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Build structured context block for coding agent prompt injection."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        return await context_composer.build_context(
            session,
            project_id=project.id,
            task_text=task_text,
            profile=profile,
            target_files=target_files,
        )


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

        arch = await graph_service.get_project_architecture(session, project.id, depth=depth)
        if not arch:
            return f"Error: Architecture not found for project '{project.name}'."

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
    description="Inspects detailed AST symbol definition, signature, location, dependencies, callers, and linked constraints.",
)
async def project_get_component(
    qualified_name: str, project_id_or_path: str = "."
) -> str:
    """Retrieve detailed AST component definition and graph connections."""
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


# ==================== 2. MEMORY EXPLORATION & RETRIEVAL TOOLS ====================

@mcp_server.tool(
    name="memory_search",
    description="Performs multi-signal hybrid search across project memories (decisions, constraints, failures, lessons).",
)
async def memory_search(
    query: str,
    memory_type: str | None = None,
    limit: int = 5,
    project_id_or_path: str = ".",
) -> str:
    """Hybrid search across memories with provenance."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        results = await memory_service.search_memories(
            session, project.id, query=query, memory_type=memory_type, limit=limit
        )
        if not results:
            return f"No memories found matching query '{query}'."

        lines = [f"# Memory Search Results for '{query}' ({len(results)})"]
        for r in results:
            m = r["memory"]
            score = r["combined_score"]
            status_tag = f"[{m.status}]" if m.status != "ACTIVE" else ""
            lines.append(f"### {status_tag} [{m.memory_type}] {m.title} (Score: {score:.2f})")
            lines.append(f"**Summary**: {m.summary}")
            lines.append(f"**Content**: {m.content}")
            if m.evidences:
                ev_str = ", ".join(f"`{e.file_path}:{e.line_start or 1}`" for e in m.evidences)
                lines.append(f"**Evidence Grounding**: {ev_str}")
            lines.append(f"**ID**: `{m.id}` | **Version**: v{m.version}")
            lines.append("")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get",
    description="Fetches full details of a memory by ID, including evidence citations, relation links, and historical version changelog.",
)
async def memory_get(memory_id: str) -> str:
    """Retrieve full memory record."""
    await init_db()
    async with session_scope() as session:
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"Memory with ID '{memory_id}' not found."

        lines = [
            f"# [{mem.memory_type}] {mem.title}",
            f"- **Status**: `{mem.status}` | **Confidence**: {mem.confidence:.2f} | **Importance**: {mem.importance:.2f}",
            f"- **Version**: v{mem.version} | **Created**: {mem.created_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            "## Summary",
            mem.summary,
            "",
            "## Content",
            mem.content,
            "",
            "## Evidence Citations",
        ]
        if mem.evidences:
            for ev in mem.evidences:
                lines.append(f"- `{ev.file_path}` (Lines {ev.line_start or 1}-{ev.line_end or 1}) [Commit: {ev.commit_sha or 'N/A'}]")
        else:
            lines.append("- None attached.")

        lines.append("")
        lines.append("## Version History")
        if mem.versions:
            for v in mem.versions:
                lines.append(f"- **v{v.version}** ({v.created_at.strftime('%Y-%m-%d %H:%M')}): {v.change_reason}")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_create",
    description="Registers a new durable project memory (DECISION, CONSTRAINT, FAILURE, LESSON, etc.) with evidence grounding.",
)
async def memory_create(
    title: str,
    content: str,
    summary: str,
    memory_type: str = "LESSON",
    evidence_file: str | None = None,
    evidence_line_start: int | None = None,
    evidence_line_end: int | None = None,
    importance: float = 0.6,
    project_id_or_path: str = ".",
) -> str:
    """Store a durable project memory."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        evidence_list = []
        if evidence_file:
            evidence_list.append(
                MemoryEvidenceCreate(
                    file_path=evidence_file,
                    line_start=evidence_line_start,
                    line_end=evidence_line_end,
                )
            )

        payload = MemoryCreate(
            memory_type=memory_type.upper(),
            title=title,
            content=content,
            summary=summary,
            importance=importance,
            evidence=evidence_list if evidence_list else None,
        )
        mem = await memory_service.create_memory(session, project.id, payload)
        return f"Successfully created memory '{mem.title}' (ID: `{mem.id}`, Status: `{mem.status}`, Version: v{mem.version})."


@mcp_server.tool(
    name="memory_update",
    description="Updates existing memory content, automatically incrementing version and recording change rationale in audit trail.",
)
async def memory_update(
    memory_id: str,
    content: str,
    change_reason: str,
    title: str | None = None,
    summary: str | None = None,
) -> str:
    """Update memory with audit trail."""
    await init_db()
    async with session_scope() as session:
        updated = await memory_service.update_memory(
            session,
            memory_id=memory_id,
            content=content,
            change_reason=change_reason,
            title=title,
            summary=summary,
        )
        if not updated:
            return f"Memory with ID '{memory_id}' not found."
        return f"Successfully updated memory '{updated.title}' to version v{updated.version}."


@mcp_server.tool(
    name="memory_deprecate",
    description="Deprecates a memory when an approach or pattern has become obsolete, optionally linking a superseding memory.",
)
async def memory_deprecate(
    memory_id: str,
    reason: str = "Deprecated by agent",
    superseded_by_id: str | None = None,
) -> str:
    """Mark memory as deprecated."""
    await init_db()
    async with session_scope() as session:
        dep = await memory_service.deprecate_memory(
            session, memory_id=memory_id, superseded_by_id=superseded_by_id, reason=reason
        )
        if not dep:
            return f"Memory with ID '{memory_id}' not found."
        return f"Memory '{dep.title}' is now DEPRECATED."


@mcp_server.tool(
    name="memory_verify",
    description="Verifies whether a memory's grounded code files and symbols still exist and are valid in the current working tree.",
)
async def memory_verify(memory_id: str) -> str:
    """Trigger active verification on a memory."""
    await init_db()
    async with session_scope() as session:
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"Memory with ID '{memory_id}' not found."

        project = await session.get(Project, mem.project_id)
        st = await verification_engine.verify_single_memory(session, mem, project.local_path)
        await session.commit()
        return f"Memory '{mem.title}' verified. Verification status: `{st}`."


@mcp_server.tool(
    name="memory_get_decisions",
    description="Returns all active architectural decisions (ADRs) and trade-off rationales.",
)
async def memory_get_decisions(project_id_or_path: str = ".") -> str:
    """Fetch architectural decisions."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        decisions = await memory_service.get_decisions(session, project.id)
        if not decisions:
            return f"No active architectural decisions recorded for project '{project.name}'."

        lines = [f"# Architectural Decisions for {project.name} ({len(decisions)})"]
        for d in decisions:
            lines.append(f"- **{d.title}**: {d.summary}")
            lines.append(f"  *Rationale*: {d.content}")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get_failures",
    description="Returns previous bug post-mortems, failed attempts, and anti-patterns to prevent repeating past mistakes.",
)
async def memory_get_failures(project_id_or_path: str = ".") -> str:
    """Fetch known failure post-mortems."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        failures = await memory_service.get_failures(session, project.id)
        if not failures:
            return f"No failure post-mortems recorded for project '{project.name}'."

        lines = [f"# Historical Failures & Anti-Patterns for {project.name} ({len(failures)})"]
        for f in failures:
            lines.append(f"### [FAILURE] {f.title}")
            lines.append(f"**Problem**: {f.summary}")
            lines.append(f"**Root Cause & Fix**: {f.content}")
            lines.append("")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get_constraints",
    description="Returns operational invariants, safety guidelines, and architectural constraints that must not be broken.",
)
async def memory_get_constraints(project_id_or_path: str = ".") -> str:
    """Fetch project constraints and invariants."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        constraints = await memory_service.get_constraints(session, project.id)
        if not constraints:
            return f"No operational constraints recorded for project '{project.name}'."

        lines = [f"# Active Architectural Constraints for {project.name} ({len(constraints)})"]
        for c in constraints:
            lines.append(f"- **{c.title}**: {c.summary}")
            lines.append(f"  *Invariant Requirement*: {c.content}")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get_lessons",
    description="Returns all active durable lessons, conventions, and engineering rules (L5).",
)
async def memory_get_lessons(project_id_or_path: str = ".") -> str:
    """Fetch durable lessons and engineering principles."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        lessons = await memory_service.list_memories(session, project.id, layer="L5", status="ACTIVE")
        # Everything at L5 that is not yet established knowledge: LLM proposals
        # awaiting review, agent observations with no code grounding, and raw
        # candidates. They are useful leads, so they are shown -- but separately,
        # and labelled, so an agent cannot mistake one for a project rule.
        proposed: list = []
        for pending_status in ("REVIEW_REQUIRED", "UNVERIFIED", "CANDIDATE"):
            proposed.extend(
                await memory_service.list_memories(
                    session, project.id, layer="L5", status=pending_status
                )
            )
        if not lessons and not proposed:
            return f"No durable lessons recorded for project '{project.name}'."

        lines: list[str] = []
        if lessons:
            lines.append(f"# Durable Lessons for {project.name} ({len(lessons)})")
            for lesson in lessons:
                lines.append(f"- **{lesson.title}**: {lesson.summary}")
                lines.append(f"  *Lesson Details*: {lesson.content}")

        # Proposals are listed separately and labelled. An agent may find them
        # useful as leads, but must not mistake an unreviewed synthesis for an
        # established project rule (specification sections 23 and 43).
        if proposed:
            lines.append("")
            lines.append(
                f"# Unverified Lessons ({len(proposed)}) "
                "-- NOT established project knowledge"
            )
            for lesson in proposed:
                lines.append(
                    f"- **{lesson.title}** [{lesson.status}, {lesson.authority}, "
                    f"confidence {lesson.confidence:.2f}]: {lesson.summary}"
                )
                lines.append(f"  *Details*: {lesson.content}")
        return "\n".join(lines)


# ==================== 3. GRAPH & CHANGE IMPACT TOOLS ====================

@mcp_server.tool(
    name="graph_get_dependencies",
    description="Returns all downstream dependencies of an entity symbol or file up to depth N.",
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

        deps = await graph_service.get_dependencies(session, project.id, entity_name, depth=depth)
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

        callers = await graph_service.get_dependents(session, project.id, entity_name, depth=depth)
        if not callers:
            return f"No callers or dependents found for entity '{entity_name}'."

        lines = [f"# Callers & Dependents for {entity_name} (blast radius depth <= {depth})"]
        for c in callers:
            lines.append(f"- [Depth {c['depth']}] **{c['name']}** ({c['type']} in `{c['file']}`) -> `{c['relationship']}`")
        return "\n".join(lines)


@mcp_server.tool(
    name="change_get_impact",
    description="Pre-action governance check: analyzes the blast radius of modifying a given set of files and surfaces linked constraints and past failures.",
)
async def change_get_impact(
    modified_files: list[str], project_id_or_path: str = "."
) -> str:
    """Pre-action governance check for proposed modifications."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        report = await change_propagator.propagate_changes(
            session, project.id, modified_files, mark_stale=False
        )

        lines = [
            "# Pre-Action Blast Radius & Impact Report",
            f"- **Target Files**: {', '.join(report.modified_files)}",
            f"- **Directly Changed Entities**: {len(report.directly_changed_entities)}",
            f"- **Affected Downstream Callers**: {len(report.affected_dependents)}",
            "",
        ]

        if report.warnings:
            lines.append("## [HIGH RISK WARNINGS]")
            for w in report.warnings:
                lines.append(f"- {w}")
            lines.append("")

        if report.critical_constraints:
            lines.append("## [INVARIANT CONSTRAINTS TO PRESERVE]")
            for c in report.critical_constraints:
                lines.append(f"- {c}")
            lines.append("")

        if report.affected_dependents:
            lines.append("## Affected Consumers")
            for dep in report.affected_dependents[:8]:
                lines.append(f"- {dep}")
            lines.append("")

        return "\n".join(lines)


# ==================== 4. SHORTCUT ACTIONS & MAINTENANCE TOOLS ====================

@mcp_server.tool(
    name="task_record_decision",
    description="Convenience shortcut for an agent to record an architectural decision made during a task.",
)
async def task_record_decision(
    title: str,
    rationale: str,
    component: str | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record an architectural decision."""
    return await memory_create(
        title=title,
        content=rationale,
        summary=f"Decision made regarding {component or 'architecture'}",
        memory_type="DECISION",
        evidence_file=component,
        project_id_or_path=project_id_or_path,
    )


@mcp_server.tool(
    name="task_record_failure",
    description="Convenience shortcut for an agent to record an obstacle or failed approach to prevent recurrence.",
)
async def task_record_failure(
    title: str,
    error_description: str,
    attempted_fix: str,
    component: str | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record a failure post-mortem."""
    content = f"Error: {error_description}\nAttempted Fix / Prevention: {attempted_fix}"
    return await memory_create(
        title=title,
        content=content,
        summary=f"Failed approach in {component or 'subsystem'}",
        memory_type="FAILURE",
        evidence_file=component,
        project_id_or_path=project_id_or_path,
    )


@mcp_server.tool(
    name="task_start",
    description="Initiates an AI agent task session, generates token-budgeted cognitive context, and begins event tracking.",
)
async def task_start(
    task_text: str,
    agent_id: str = "generic_agent",
    profile: str = "medium",
    target_files: list[str] | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Start task and generate project cognitive context packet."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        task, context = await orchestrator.start_task(
            session=session,
            project_id=project.id,
            task_text=task_text,
            agent_id=agent_id,
            profile=profile,
            target_files=target_files,
        )
        return f"Task started: `{task.id}` (Status: {task.status})\n\n{context}"


@mcp_server.tool(
    name="task_record_event",
    description="Records an agent event (tool call, file change, test result) during task execution.",
)
async def task_record_event(
    task_id: str,
    event_type: str,
    tool_name: str | None = None,
    file_path: str | None = None,
    status: str | None = None,
    details: str | None = None,
) -> str:
    """Record agent activity event."""
    await init_db()
    async with session_scope() as session:
        ev_upper = event_type.upper()
        if "TOOL" in ev_upper and tool_name:
            await orchestrator.record_tool_call(session, task_id=task_id, tool_name=tool_name, tool_result=details)
            return f"Recorded tool call '{tool_name}' for task '{task_id}'."
        elif "FILE" in ev_upper and file_path:
            impact = await orchestrator.record_file_change(session, task_id=task_id, file_path=file_path)
            return f"Recorded file change '{file_path}'. Flagged {len(impact.memories_flagged_stale)} stale memories."
        elif "TEST" in ev_upper:
            await orchestrator.record_test_result(
                session, task_id=task_id, test_name=tool_name or "test", status=status or "PASSED", error_text=details
            )
            return f"Recorded test result for task '{task_id}'."
        else:
            await orchestrator.record_tool_call(session, task_id=task_id, tool_name=event_type, tool_result=details)
            return f"Recorded event '{event_type}' for task '{task_id}'."


@mcp_server.tool(
    name="task_complete",
    description="Concludes an agent task, reverifies memories against updated files, and records durable lessons.",
)
async def task_complete(
    task_id: str,
    success: bool = True,
    lesson_learned: str | None = None,
    token_input: int = 0,
    token_output: int = 0,
) -> str:
    """Complete agent task and update project cognitive model."""
    await init_db()
    async with session_scope() as session:
        task = await orchestrator.complete_task(
            session=session,
            task_id=task_id,
            success=success,
            lesson_learned=lesson_learned,
            token_input=token_input,
            token_output=token_output,
        )
        if not task:
            return f"Task with ID '{task_id}' not found."
        return f"Task '{task_id}' marked {task.status} (Success: {task.success}). Memory model verified."


@mcp_server.tool(
    name="memory_consolidate",
    description="Runs the memory consolidation engine to cluster episodic events into durable knowledge principles.",
)
async def memory_consolidate(project_id_or_path: str = ".") -> str:
    """Trigger memory consolidation."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        res = await consolidation_engine.consolidate_project(session, project.id)
        return (
            f"Consolidation complete for '{project.name}': "
            f"{res['clusters_consolidated']} clusters consolidated, "
            f"{res['durable_memories_created']} durable memories synthesized, "
            f"{res['memories_archived']} episodes archived."
        )


@mcp_server.tool(
    name="memory_health",
    description="Returns diagnostic statistics on memory health, active vs stale memories, and graph density.",
)
async def memory_health(project_id_or_path: str = ".") -> str:
    """Report memory health metrics."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        total_mems = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == project.id))
        active_mems = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == project.id, Memory.status == "ACTIVE"))
        stale_mems = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == project.id, Memory.status == "STALE"))
        conflicted_mems = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == project.id, Memory.status == "CONFLICTED"))
        archived_mems = await session.scalar(select(func.count(Memory.id)).where(Memory.project_id == project.id, Memory.status == "ARCHIVED"))
        entities_count = await session.scalar(select(func.count(CodeEntity.id)).where(CodeEntity.project_id == project.id))

        stale_rate = (stale_mems / max(1, total_mems)) * 100

        lines = [
            f"# Memory Health Diagnostic: {project.name}",
            f"- **Total Memories**: {total_mems}",
            f"- **Active**: {active_mems} | **Stale**: {stale_mems} ({stale_rate:.1f}%) | **Conflicted**: {conflicted_mems} | **Archived**: {archived_mems}",
            f"- **Code Graph Entities**: {entities_count}",
            f"- **Overall Health Score**: {'EXCELLENT' if stale_rate < 10 else ('NEEDS_ATTENTION' if stale_rate < 30 else 'DEGRADED')}",
        ]
        return "\n".join(lines)


@mcp_server.tool(
    name="architecture_check_rules",
    description="Evaluates all active architecture boundary invariant rules and reports any forbidden relationships or layer violations.",
)
async def architecture_check_rules(project_id_or_path: str = ".") -> str:
    """Evaluate architectural boundary invariants."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        violations = await invariant_engine.check_project_invariants(session, project_id=project.id)
        if not violations:
            return f"Architecture Invariant Check: All boundary rules passed for '{project.name}'. Zero violations detected."

        lines = [f"# Architecture Invariant Violations for {project.name} ({len(violations)})"]
        for v in violations:
            lines.append(
                f"- **[{v.severity}] {v.rule_id}**: Relationship `{v.relationship_type}` from `{v.source_entity_id}` to `{v.target_entity_id}` is forbidden."
            )
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get_provenance",
    description="Answers 'Why does CortexForge believe this?' by returning the complete causal provenance chain: Memory -> Evidence -> Symbol -> File -> Commit -> Versions -> Tests.",
)
async def memory_get_provenance(memory_id: str) -> str:
    """Inspect full causal provenance graph for a memory."""
    await init_db()
    async with session_scope() as session:
        trace = await provenance_engine.trace_memory(session, memory_id=memory_id)
        if not trace:
            return f"Error: Memory with ID '{memory_id}' not found."

        lines = [
            f"# Provenance Trace: {trace['title']} (`{trace['memory_id']}`)",
            f"- **Layer**: {trace['layer']} | **Type**: {trace['memory_type']} | **Status**: {trace['status']} | **Confidence**: {trace['confidence']:.2f}",
            "",
            "## Why CortexForge Believes This",
            trace["why_cortexforge_believes_this"],
            "",
            f"## Grounding Evidences ({len(trace['evidences'])})",
        ]
        for ev in trace["evidences"]:
            lines.append(f"- `{ev['file_path']}:{ev.get('line_start') or 1}` [Type: {ev['source_type']}, Confidence: {ev.get('confidence', 1.0):.2f}]")

        if trace["symbols"]:
            lines.append("")
            lines.append(f"## Anchored Symbols ({len(trace['symbols'])})")
            for sym in trace["symbols"]:
                lines.append(f"- **{sym.get('name')}** (`{sym.get('qualified_name')}`) in `{sym.get('file_path')}`")

        if trace["commits"]:
            lines.append("")
            lines.append(f"## Associated Commits: {', '.join(trace['commits'])}")

        if trace["versions"]:
            lines.append("")
            lines.append(f"## Revision History ({len(trace['versions'])})")
            for v in trace["versions"]:
                lines.append(f"- v{v.get('version')}: {v.get('change_reason')}")

        return "\n".join(lines)


@mcp_server.tool(
    name="project_take_snapshot",
    description="Captures a deterministic project-wide cognitive snapshot identifying exact commit SHA, generation counters, and retrieval version.",
)
async def project_take_snapshot(commit_sha: str, project_id_or_path: str = ".") -> str:
    """Capture a cognitive snapshot of project state."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        snap = await snapshot_engine.take_snapshot(session, project_id=project.id, commit_sha=commit_sha)
        return (
            f"Cognitive snapshot captured: ID `{snap.id}` for commit `{snap.commit_sha}`\n"
            f"- Cognitive Gen: {snap.cognitive_generation} | Memory Gen: {snap.memory_generation} | Graph Gen: {snap.graph_generation}"
        )


@mcp_server.tool(
    name="task_find_similar",
    description="Retrieves historically similar engineering tasks, approaches attempted, failure post-mortems, and successful fixes.",
)
async def task_find_similar(task_text: str, project_id_or_path: str = ".") -> str:
    """Find similar previous tasks and their outcomes."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        similar_tasks = await orchestrator.find_similar_tasks(session, project.id, task_text=task_text)
        if not similar_tasks:
            return f"No similar historical engineering tasks found for query '{task_text}'."

        lines = [f"# Similar Historical Tasks for '{task_text}' ({len(similar_tasks)})"]
        for st in similar_tasks:
            t = st["task"]
            lines.append(f"### Task: {t.task_text} (Status: {t.status}, Success: {t.success})")
            if st["failure_episodes"]:
                lines.append("  **Past Failures in Similar Tasks**:")
                for fe in st["failure_episodes"]:
                    lines.append(f"  - [{fe.error_class}] {fe.error_message} (Fix Status: {fe.fix_status})")
            if st["fix_attempts"]:
                lines.append("  **Successful Fixes & Approaches**:")
                for fa in st["fix_attempts"]:
                    lines.append(f"  - Approach: {fa.approach_description} (Outcome: {fa.outcome})")
            lines.append("")
        return "\n".join(lines)


# ==================== 5. RESOURCES ====================


@mcp_server.resource("cortex://project/architecture")
async def resource_architecture() -> str:
    return await project_get_architecture(".")


@mcp_server.resource("cortex://project/decisions")
async def resource_decisions() -> str:
    return await memory_get_decisions(".")


@mcp_server.resource("cortex://project/constraints")
async def resource_constraints() -> str:
    return await memory_get_constraints(".")


def main() -> None:
    """Run MCP server over stdio transport."""
    mcp_server.run()


if __name__ == "__main__":
    main()

"""Full-Featured Model Context Protocol (MCP) Server for CortexForge."""

import os

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func, select

from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.agent.success_intelligence import SuccessIntelligence
from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import CodeEntity, Memory, MemoryDecision, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.graph.service import GraphService
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.provenance import ProvenanceEngine
from cortexforge.memory.service import ConcurrentModificationError, MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine
from cortexforge.verification.engine import ClaimVerificationEngine

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
claim_service = ClaimService()
# Two verification layers: memory-level (derives a memory's state from its
# claims) and claim-level (evaluates the propositions themselves).
verification_engine = MemoryVerificationEngine()
claim_verification_engine = ClaimVerificationEngine()
success_intelligence = SuccessIntelligence()
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

        status, entity, candidates = await graph_service.resolve_entity(session, project.id, qualified_name)
        if status == "AMBIGUOUS":
            cand_list = "\n".join(f"- `{c}`" for c in candidates)
            return (
                f"Error: Symbol '{qualified_name}' is ambiguous in project '{project.name}'. "
                f"Multiple matching components found:\n{cand_list}\n"
                f"Please specify the full qualified name."
            )
        if status == "NOT_FOUND" or not entity:
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
    description="Registers a new project memory (DECISION, CONSTRAINT, FAILURE, LESSON, etc.) with evidence grounding. Verifies agent observations before promotion to active truth.",
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
    trusted_user_confirmed: bool = False,
) -> str:
    """Store a project memory with validation, trust classification, and verification pipeline."""
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

        authority = "USER_CONFIRMED" if trusted_user_confirmed else "AGENT_OBSERVED"
        source_type = "user" if trusted_user_confirmed else "agent_observation"

        payload = MemoryCreate(
            memory_type=memory_type.upper(),
            title=title,
            content=content,
            summary=summary,
            importance=importance,
            evidence=evidence_list if evidence_list else None,
            authority=authority,
            source_type=source_type,
        )
        mem = await memory_service.create_memory(session, project.id, payload)

        # Verification pipeline for agent observations (§29, §32)
        if not trusted_user_confirmed:
            if evidence_list:
                verdict = await verification_engine.verify_single_memory(
                    session, mem, project.local_path
                )
                await session.commit()
                if verdict == "ACTIVE":
                    return f"[VERIFIED_ACTIVE] Memory '{mem.title}' verified against code grounding (ID: `{mem.id}`, Status: `{verdict}`, Version: v{mem.version})."
                return f"[VERIFICATION_REQUIRED] Memory '{mem.title}' recorded with unverified grounding (ID: `{mem.id}`, Status: `{verdict}`). Evidence check did not confirm active state."
            else:
                return f"[CANDIDATE_CREATED] Candidate memory '{mem.title}' recorded without code evidence (ID: `{mem.id}`, Status: `{mem.status}`). Requires verification or human approval."

        return f"Successfully created memory '{mem.title}' (ID: `{mem.id}`, Status: `{mem.status}`, Version: v{mem.version})."


@mcp_server.tool(
    name="memory_update",
    description="Updates existing memory content with optimistic locking, automatically incrementing version and recording change rationale in audit trail.",
)
async def memory_update(
    memory_id: str,
    content: str,
    change_reason: str,
    title: str | None = None,
    summary: str | None = None,
    expected_version: int | None = None,
) -> str:
    """Update memory with audit trail and optimistic locking."""
    await init_db()
    async with session_scope() as session:
        try:
            updated = await memory_service.update_memory(
                session,
                memory_id=memory_id,
                content=content,
                change_reason=change_reason,
                title=title,
                summary=summary,
                expected_version=expected_version,
            )
            if not updated:
                return f"Memory with ID '{memory_id}' not found."
            return f"Successfully updated memory '{updated.title}' to version v{updated.version}."
        except ConcurrentModificationError as e:
            return f"Conflict Error: {e}"


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
    workspace_id: str | None = None,
    session_id: str | None = None,
    parent_task_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    model_version: str | None = None,
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
            workspace_id=workspace_id,
            session_id=session_id,
            parent_task_id=parent_task_id,
            provider=provider,
            model=model,
            model_version=model_version,
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
async def task_find_similar(
    task_text: str,
    project_id_or_path: str = ".",
    files: list[str] | None = None,
    symbols: list[str] | None = None,
    failure_signature: str | None = None,
) -> str:
    """Find similar previous tasks and their outcomes."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        similar_tasks = await orchestrator.find_similar_tasks(
            session,
            project.id,
            task_text=task_text,
            files=files,
            symbols=symbols,
            failure_signature=failure_signature,
        )
        if not similar_tasks:
            return f"No similar historical engineering tasks found for query '{task_text}'."

        lines = [f"# Similar Historical Tasks for '{task_text}' ({len(similar_tasks)})"]
        for st in similar_tasks:
            t = st["task"]
            score = st.get("similarity_score", 0.0)
            breakdown = st.get("score_breakdown", {})
            lines.append(f"### Task: {t.task_text} (Score: {score}, Status: {t.status}, Success: {t.success})")
            if breakdown:
                lines.append(
                    f"  **Signals**: text={breakdown.get('text_score')}, "
                    f"files={breakdown.get('file_score')}, "
                    f"symbols={breakdown.get('symbol_score')}, "
                    f"failure={breakdown.get('failure_score')}"
                )
            if st.get("failure_episodes"):
                lines.append("  **Past Failures in Similar Tasks**:")
                for fe in st["failure_episodes"]:
                    err_cls = fe.get("error_class") if isinstance(fe, dict) else getattr(fe, "error_class", "Error")
                    err_msg = fe.get("error_message") if isinstance(fe, dict) else getattr(fe, "error_message", "")
                    fix_stat = fe.get("fix_status") if isinstance(fe, dict) else getattr(fe, "fix_status", "UNRESOLVED")
                    lines.append(f"  - [{err_cls}] {err_msg} (Fix Status: {fix_stat})")
            if st.get("fix_attempts"):
                lines.append("  **Successful Fixes & Approaches**:")
                for fa in st["fix_attempts"]:
                    appr = fa.get("approach_description") if isinstance(fa, dict) else getattr(fa, "approach_description", "")
                    outc = fa.get("outcome") if isinstance(fa, dict) else getattr(fa, "outcome", "")
                    lines.append(f"  - Approach: {appr} (Outcome: {outc})")
            lines.append("")
        return "\n".join(lines)


# ==================== 5. RESOURCES ====================


# ==================== 8. SAFE MEMORY OPERATIONS ====================
#
# Specification section 32 asks for observe / propose / verify / approve rather
# than unrestricted durable writes. `memory_create` remains for compatibility, but
# these express intent explicitly: an agent that is reporting what it saw should
# not have to decide whether that becomes project truth, and should not be able to.


@mcp_server.tool(
    name="memory_observe",
    description=(
        "Record something the agent observed. Observations are stored as "
        "unverified working knowledge, never as established project truth."
    ),
)
async def memory_observe(
    title: str,
    content: str,
    summary: str | None = None,
    evidence_file: str | None = None,
    evidence_line_start: int | None = None,
    evidence_line_end: int | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record an agent observation without asserting that it is true."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        evidence = None
        if evidence_file:
            evidence = [
                MemoryEvidenceCreate(
                    file_path=evidence_file,
                    source_type="agent_observation",
                    line_start=evidence_line_start,
                    line_end=evidence_line_end,
                )
            ]

        memory = await memory_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                memory_type="EPISODE",
                layer="L6",
                title=title,
                content=content,
                summary=summary or content[:150],
                source_type="agent_observation",
                importance=0.5,
                evidence=evidence,
            ),
        )
        return (
            f"Recorded observation '{memory.title}' (ID: `{memory.id}`, "
            f"status `{memory.status}`, authority `{memory.authority}`). "
            "It is not project truth until verified."
        )


@mcp_server.tool(
    name="memory_propose",
    description=(
        "Propose durable project knowledge (a decision, constraint or lesson). "
        "Proposals wait for verification or human approval before being believed."
    ),
)
async def memory_propose(
    title: str,
    content: str,
    summary: str | None = None,
    memory_type: str = "LESSON",
    evidence_file: str | None = None,
    evidence_line_start: int | None = None,
    evidence_line_end: int | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Propose knowledge for review rather than writing it in as fact."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        evidence = None
        if evidence_file:
            evidence = [
                MemoryEvidenceCreate(
                    file_path=evidence_file,
                    source_type="code",
                    line_start=evidence_line_start,
                    line_end=evidence_line_end,
                )
            ]

        memory = await memory_service.create_memory(
            session,
            project.id,
            MemoryCreate(
                memory_type=memory_type.upper(),
                title=title,
                content=content,
                summary=summary or content[:150],
                source_type="agent_observation",
                importance=0.75,
                evidence=evidence,
            ),
        )
        claims = await claim_service.get_claims_for_memory(session, memory.id)
        return (
            f"Proposed '{memory.title}' (ID: `{memory.id}`, status `{memory.status}`).\n"
            f"Extracted {len(claims)} verifiable claim(s):\n"
            + "\n".join(f"  - {claim.text}" for claim in claims[:5])
        )


@mcp_server.tool(
    name="memory_verify_claims",
    description=(
        "Verify a project's claims against the current repository and report each "
        "outcome with the reason behind it, including UNKNOWN where evidence is "
        "insufficient."
    ),
)
async def memory_verify_claims(
    project_id_or_path: str = ".", commit_sha: str | None = None
) -> str:
    """Run claim verification and report what was found."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        run = await claim_verification_engine.verify_project(
            session, project.id, commit_sha=commit_sha, verifier="mcp"
        )
        return (
            f"# Verification run `{run.id}`\n"
            f"- Claims evaluated: {run.claims_evaluated}\n"
            f"- Verified: {run.verified_count}\n"
            f"- Partially verified: {run.partially_verified_count}\n"
            f"- Failed: {run.failed_count}\n"
            f"- Conflicted: {run.conflicted_count}\n"
            f"- Unknown (insufficient evidence): {run.unknown_count}\n"
            f"- Not applicable: {run.not_applicable_count}"
        )


@mcp_server.tool(
    name="memory_get_claims",
    description="Lists the individually verifiable claims inside a memory and their verification state.",
)
async def memory_get_claims(memory_id: str) -> str:
    """Show what a memory actually asserts, claim by claim."""
    await init_db()
    async with session_scope() as session:
        claims = await claim_service.get_claims_for_memory(session, memory_id)
        if not claims:
            return f"No claims recorded for memory `{memory_id}`."

        lines = [f"# Claims in memory `{memory_id}` ({len(claims)})"]
        for claim in claims:
            lines.append(f"- **{claim.text}**")
            lines.append(
                f"  status `{claim.status}` - authority `{claim.authority}` - "
                f"confidence {claim.confidence:.2f}"
            )
            explanation = (claim.confidence_components or {}).get("explanation")
            if explanation:
                lines.append(f"  {explanation}")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_pending_approvals",
    description="Lists memories awaiting human approval before they may be treated as project truth.",
)
async def memory_pending_approvals(project_id_or_path: str = ".") -> str:
    """Show what is waiting on a decision."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        pending: list = []
        for state in ("REVIEW_REQUIRED", "CANDIDATE"):
            pending.extend(
                await memory_service.list_memories(session, project.id, status=state)
            )
        if not pending:
            return f"Nothing is awaiting approval in '{project.name}'."

        lines = [f"# Awaiting approval in {project.name} ({len(pending)})"]
        for memory in pending:
            lines.append(
                f"- `{memory.id}` **{memory.title}** [{memory.status}, "
                f"{memory.authority}]: {memory.summary}"
            )
        return "\n".join(lines)


@mcp_server.tool(
    name="task_find_successful_approaches",
    description=(
        "Retrieves approaches that previously worked on similar tasks, with the "
        "reason each was considered relevant."
    ),
)
async def task_find_successful_approaches(
    task_text: str, project_id_or_path: str = ".", target_files: str | None = None
) -> str:
    """Surface what worked before, so the agent does not rediscover it."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        files = [f.strip() for f in target_files.split(",")] if target_files else None
        successes = await success_intelligence.find_similar_successes(
            session, project.id, task_text, target_files=files
        )
        if not successes:
            return (
                "No comparable successful approach has been recorded for this project "
                "yet. That is an absence of evidence, not evidence that none exists."
            )

        lines = [f"# Approaches that worked on similar tasks ({len(successes)})"]
        for item in successes:
            lines.append(f"- **{item['title']}** (relevance {item['score']:.2f})")
            lines.append(f"  *Why surfaced*: {item['why_selected']}")
            lines.append(f"  *Approach*: {item['approach']}")
            if item.get("why_it_worked"):
                lines.append(f"  *Why it worked*: {item['why_it_worked']}")
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_get_decisions_log",
    description=(
        "Shows the reconciliation decision log: what each code change caused "
        "CortexForge to conclude about its memories, and why."
    ),
)
async def memory_get_decisions_log(project_id_or_path: str = ".", limit: int = 20) -> str:
    """Explain how the project's beliefs got to their current state."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        result = await session.execute(
            select(MemoryDecision)
            .where(MemoryDecision.project_id == project.id)
            .order_by(MemoryDecision.created_at.desc())
            .limit(limit)
        )
        decisions = list(result.scalars().all())
        if not decisions:
            return f"No reconciliation decisions recorded for '{project.name}' yet."

        lines = [f"# Reconciliation decisions for {project.name} ({len(decisions)})"]
        for decision in decisions:
            lines.append(
                f"- **{decision.decision}** ({decision.reason_code}) on memory "
                f"`{decision.memory_id}`"
            )
            lines.append(f"  {decision.reason}")
            if decision.previous_status != decision.new_status:
                lines.append(
                    f"  status: {decision.previous_status} -> {decision.new_status}"
                )
        return "\n".join(lines)


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

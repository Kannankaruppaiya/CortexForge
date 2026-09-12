"""Full-Featured Model Context Protocol (MCP) Server for CortexForge."""

import logging
import os
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func, select

from cortexforge.agent.failure_intelligence import FailureIntelligenceEngine
from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.agent.success_intelligence import SuccessIntelligence
from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.code_intelligence.source_adapter import get_repository_source
from cortexforge.cognition.authority import Authority
from cortexforge.cognition.epistemics import ClaimStatus
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import (
    Agent,
    AgentProjectPermission,
    AgentTask,
    Claim,
    CodeEntity,
    FailureEpisode,
    FixAttempt,
    Memory,
    MemoryDecision,
    Project,
    ProjectMembership,
    User,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.graph.service import GraphService
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.provenance import ProvenanceEngine
from cortexforge.memory.service import ConcurrentModificationError, MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.memory.verification import MemoryVerificationEngine
from cortexforge.observability.audit import AuditAction, record_audit
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine
from cortexforge.security.approval import ApprovalService
from cortexforge.security.auth import (
    Principal,
    resolve_principal_from_token,
    validate_local_registration_path,
)
from cortexforge.security.policy import (
    Permission,
)

DEFAULT_AGENT_ONBOARD_SCOPES = [
    "project:read",
    "context:read",
    "code:read",
    "memory:read",
    "memory:write",
    "graph:read",
    "scan:trigger",
]
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
verification_engine = MemoryVerificationEngine()
claim_verification_engine = ClaimVerificationEngine()
failure_intelligence = FailureIntelligenceEngine()
success_intelligence = SuccessIntelligence()
consolidation_engine = MemoryConsolidationEngine(memory_service=memory_service)
retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
context_composer = ContextComposer(
    retrieval_engine=retrieval_engine, graph_service=graph_service
)
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

_CURRENT_MCP_CALLER: ContextVar[dict[str, Any] | None] = ContextVar(
    "_CURRENT_MCP_CALLER", default=None
)


def set_mcp_caller(
    user_id: str | None = None,
    agent_id: str | None = None,
    token: str | None = None,
) -> None:
    """Set ambient caller credentials for MCP request evaluation."""
    _CURRENT_MCP_CALLER.set({"user_id": user_id, "agent_id": agent_id, "token": token})


async def _resolve_project(
    session,
    project_id_or_path: str,
    required_permission: Permission | None = None,
) -> Project | None:
    """Resolve project by ID or local filesystem path, enforcing user ownership and agent permissions."""
    project = await session.get(Project, project_id_or_path)

    if not project:
        is_hosted = (
            os.environ.get("CORTEX_HOSTED") == "true"
            or os.environ.get("CORTEX_ENV") == "production"
        )
        if not is_hosted:
            canonical_path = os.path.realpath(project_id_or_path)
            stmt = select(Project).where(Project.local_path == canonical_path)
            res = await session.execute(stmt)
            project = res.scalars().first()

    if not project:
        return None

    # Enforce Project Authorization in MCP using canonical Principal
    caller_ctx = _CURRENT_MCP_CALLER.get() or {}
    caller_token = (
        caller_ctx.get("token")
        or os.environ.get("CORTEX_MCP_TOKEN")
        or os.environ.get("CORTEX_AGENT_KEY")
    )
    if not caller_token:
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token

            access_tok = get_access_token()
            if access_tok:
                caller_token = access_tok.token
        except (ImportError, Exception) as exc:
            logging.getLogger("cortexforge.mcp").debug(
                "Could not retrieve ambient MCP access token: %s", exc
            )

    principal: Principal | None = None
    if caller_token:
        principal = await resolve_principal_from_token(session, caller_token)
        if not principal:
            # Token provided but failed verification -> Fail Closed (§28)
            return None
    else:
        # In non-production only, allow ambient caller ID if explicitly configured
        caller_user_id = caller_ctx.get("user_id") or os.environ.get(
            "CORTEX_CALLER_USER_ID"
        )
        caller_agent_id = caller_ctx.get("agent_id") or os.environ.get(
            "CORTEX_CALLER_AGENT_ID"
        )
        is_prod = (
            os.environ.get("CORTEX_ENV") == "production"
            or os.environ.get("CORTEX_HOSTED") == "true"
        )
        if is_prod and not caller_agent_id and not caller_user_id:
            return None
        if caller_agent_id:
            agent = await session.get(Agent, caller_agent_id)
            if not agent or agent.status != "ACTIVE":
                return None
            now = datetime.now(UTC)
            perm_res = await session.execute(
                select(AgentProjectPermission).where(
                    AgentProjectPermission.agent_id == agent.id,
                    AgentProjectPermission.revoked_at.is_(None),
                    (AgentProjectPermission.expires_at.is_(None))
                    | (AgentProjectPermission.expires_at > now),
                )
            )
            perms = perm_res.scalars().all()
            allowed = {p.project_id for p in perms}
            scopes_map = {p.project_id: p.scopes for p in perms}
            principal = Principal(
                principal_id=agent.id,
                actor_type="AGENT",
                agent_id=agent.id,
                user_id=agent.owner_user_id,
                role="agent",
                allowed_project_ids=allowed,
                agent_scopes=scopes_map,
            )
        elif caller_user_id:
            user = await session.get(User, caller_user_id)
            if not user or user.status != "ACTIVE":
                return None
            is_admin = bool(getattr(user, "is_admin", False))
            proj_res = await session.execute(
                select(Project.id).where(Project.owner_user_id == user.id)
            )
            owned_ids = set(proj_res.scalars().all())
            mem_res = await session.execute(
                select(ProjectMembership.project_id).where(
                    ProjectMembership.user_id == user.id
                )
            )
            owned_ids.update(mem_res.scalars().all())
            principal = Principal(
                principal_id=user.id,
                actor_type="USER",
                user_id=user.id,
                email=user.email,
                role="admin" if is_admin else "user",
                allowed_project_ids=owned_ids,
                is_admin=is_admin,
            )


    if not principal and (
        os.environ.get("CORTEX_ENV") == "production"
        or os.environ.get("CORTEX_HOSTED") == "true"
        or (
            os.environ.get("CORTEX_ENV") != "development"
            and not os.environ.get("PYTEST_CURRENT_TEST")
        )
    ):
        return None

    if principal:
        if not principal.can_access_project(project.id):
            return None
        if required_permission is not None and not principal.has_permission(project.id, required_permission):
            return None

    return project


# ==================== 0. CODING AGENT WORKFLOW & DISCOVERY TOOLS ====================


@mcp_server.tool(
    name="resolve_project",
    description=(
        "Resolves and binds the CortexForge project for a working directory or project ID. "
        "Verifies agent authorization, automatically onboards/indexes new repositories when requested, "
        "and returns current project status, Git branch/commit, and cognition summary."
    ),
)
async def resolve_project(
    project_id_or_path: str = ".",
    auto_onboard: bool = True,
    project_name: str | None = None,
) -> str:
    """Resolve and optionally onboard a project for the authenticated agent."""
    await init_db()
    async with session_scope() as session:
        canonical_path = os.path.realpath(project_id_or_path)

        # Check if project exists in database
        stmt = select(Project).where(
            (Project.id == project_id_or_path) | (Project.local_path == canonical_path)
        )
        res = await session.execute(stmt)
        existing_proj = res.scalars().first()

        # Attempt authenticated resolution
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.PROJECT_READ
        )

        if existing_proj and not project:
            return (
                f"Access Denied: The authenticated agent/user does not have permission "
                f"to access project '{existing_proj.name}' ({existing_proj.id})."
            )

        if project:
            # Check if project has been indexed
            entities_count = await session.scalar(
                select(func.count(CodeEntity.id)).where(
                    CodeEntity.project_id == project.id
                )
            )
            if entities_count == 0 and not project.last_indexed_commit:
                source = get_repository_source(project)
                await scanner.scan_project(
                    session, project.id, repository_source=source
                )
                await session.commit()
                entities_count = await session.scalar(
                    select(func.count(CodeEntity.id)).where(
                        CodeEntity.project_id == project.id
                    )
                )

            total_mems = await session.scalar(
                select(func.count(Memory.id)).where(Memory.project_id == project.id)
            )
            active_decisions = await session.scalar(
                select(func.count(Memory.id)).where(
                    Memory.project_id == project.id,
                    Memory.memory_type == "DECISION",
                    Memory.status == "ACTIVE",
                )
            )
            active_failures = await session.scalar(
                select(func.count(Memory.id)).where(
                    Memory.project_id == project.id,
                    Memory.memory_type == "FAILURE",
                    Memory.status == "ACTIVE",
                )
            )

            source = get_repository_source(project)
            branch = source.get_current_branch()
            head = source.get_head_commit()

            return (
                f"# Project Resolved: {project.name}\n"
                f"- **Project ID**: `{project.id}`\n"
                f"- **Local Path**: `{project.local_path or 'N/A'}`\n"
                f"- **Source Type**: `{project.source_type}`\n"
                f"- **Git Branch**: `{branch}` | **HEAD Commit**: `{head[:8] if head else 'N/A'}`\n"
                f"- **Cognition Index**: {entities_count} code entities, {total_mems} memories "
                f"({active_decisions} active decisions, {active_failures} known failures)\n"
                f"- **Status**: Ready for agent queries."
            )

        # Project does not exist yet
        if not auto_onboard:
            return (
                f"Project not found for '{project_id_or_path}'. "
                f"Call resolve_project with auto_onboard=True to link and index this repository."
            )

        # First connection / onboarding workflow
        caller_ctx = _CURRENT_MCP_CALLER.get() or {}
        caller_token = (
            caller_ctx.get("token")
            or os.environ.get("CORTEX_MCP_TOKEN")
            or os.environ.get("CORTEX_AGENT_KEY")
        )

        principal: Principal | None = None
        if caller_token:
            principal = await resolve_principal_from_token(session, caller_token)
        else:
            caller_user_id = caller_ctx.get("user_id") or os.environ.get(
                "CORTEX_CALLER_USER_ID"
            )
            caller_agent_id = caller_ctx.get("agent_id") or os.environ.get(
                "CORTEX_CALLER_AGENT_ID"
            )
            if caller_agent_id:
                agent = await session.get(Agent, caller_agent_id)
                if agent and agent.status == "ACTIVE":
                    principal = Principal(
                        principal_id=agent.id,
                        actor_type="AGENT",
                        agent_id=agent.id,
                        user_id=agent.owner_user_id,
                        role="agent",
                    )
            elif caller_user_id:
                user = await session.get(User, caller_user_id)
                if user:
                    principal = Principal(
                        principal_id=user.id,
                        actor_type="USER",
                        user_id=user.id,
                        email=user.email,
                        role="admin" if getattr(user, "is_admin", False) else "user",
                        is_admin=bool(getattr(user, "is_admin", False)),
                    )

        # Fail closed: No unauthenticated onboarding in ANY environment (§28, Zero-Trust)
        if not principal:
            return "Error: Authentication required to onboard a new project. Provide a valid agent token or session."

        # Derive owner_user_id authoritatively from authenticated principal (no bootstrap UUID!)
        owner_user_id = principal.user_id
        if not owner_user_id:
            return "Error: Could not derive authoritative project owner from authenticated principal."

        # Hosted boundary check for local filesystem paths
        is_hosted = (
            os.environ.get("CORTEX_HOSTED", "").strip().lower() in ("1", "true", "yes")
            or os.environ.get("CORTEX_ENV") == "production"
        )
        is_local_path = (
            project_id_or_path.startswith((".", "/", "\\"))
            or (len(project_id_or_path) >= 2 and project_id_or_path[1] == ":")
        )
        if is_hosted and is_local_path:
            bridge_url = os.environ.get("CORTEX_BRIDGE_URL")
            if not bridge_url:
                return (
                    f"Error: Hosted CortexForge cannot access local path '{project_id_or_path}' "
                    "without an active Local Bridge connection. Connect CortexForge Local Bridge "
                    "or register via GitHub repository identity."
                )

        # Validate local path
        try:
            canonical = validate_local_registration_path(project_id_or_path)
        except Exception as e:
            return f"Error: Invalid local repository path '{project_id_or_path}': {e}"

        proj_id = str(uuid.uuid4())
        pname = project_name or os.path.basename(canonical) or "Project"

        new_project = Project(
            id=proj_id,
            name=pname,
            local_path=canonical,
            owner_user_id=owner_user_id,
            source_type="LOCAL",
        )
        session.add(new_project)
        await session.flush()

        # Link agent permission with bounded least-privilege scopes (NEVER wildcard '*')
        if principal.actor_type == "AGENT" and principal.agent_id:
            perm = AgentProjectPermission(
                id=str(uuid.uuid4()),
                agent_id=principal.agent_id,
                project_id=proj_id,
                scopes=DEFAULT_AGENT_ONBOARD_SCOPES,
            )
            session.add(perm)

        await record_audit(
            session=session,
            action=AuditAction.PROJECT_CONFIGURED,
            resource_type="project",
            resource_id=proj_id,
            actor=principal.principal_id,
            project_id=proj_id,
            after={
                "auto_onboard": True,
                "local_path": canonical,
                "scopes": DEFAULT_AGENT_ONBOARD_SCOPES if principal.actor_type == "AGENT" else ["owner"],
            },
        )

        # Initial indexing scan via RepositorySource
        source = get_repository_source(new_project)
        scan_res = await scanner.scan_project(
            session, new_project.id, repository_source=source
        )
        await session.commit()

        granted_scopes_desc = (
            f"scoped access ({', '.join(DEFAULT_AGENT_ONBOARD_SCOPES)})"
            if principal.actor_type == "AGENT"
            else "owner access"
        )
        return (
            f"# Project Onboarded: {new_project.name}\n"
            f"- **Project ID**: `{new_project.id}`\n"
            f"- **Local Path**: `{canonical}`\n"
            f"- **Owner User**: `{owner_user_id}`\n"
            f"- **Initial Indexing**: {scan_res.files_scanned} files scanned, "
            f"{scan_res.entities_extracted} entities indexed\n"
            f"- **Agent Permission**: Granted {granted_scopes_desc}\n"
            f"- **Status**: CortexForge memory and code intelligence active."
        )


@mcp_server.tool(
    name="get_project_context",
    description="Returns structured, token-budget-aware project context (architecture, active decisions, constraints, previous failures, and warnings) for a planned task.",
)
async def get_project_context(
    task_text: str,
    profile: str = "medium",
    target_files: list[str] | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Build structured context block for coding agent prompt injection."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.PROJECT_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        return await context_composer.build_context(
            session,
            project_id=project.id,
            task_text=task_text,
            profile=profile,
            target_files=target_files,
        )


@mcp_server.tool(
    name="search_project_memory",
    description="Performs multi-signal hybrid search across project memories (decisions, constraints, failures, lessons).",
)
async def search_project_memory(
    query: str,
    memory_type: str | None = None,
    limit: int = 5,
    project_id_or_path: str = ".",
) -> str:
    """Hybrid search across memories with provenance."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.MEMORY_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        results = await memory_service.search_memories(
            session, project.id, query=query, memory_type=memory_type, limit=limit
        )
        if not results:
            return f"No memories matching '{query}' found for project '{project.name}'."

        lines = [f"# Project Memories matching '{query}' ({len(results)})"]
        for item in results:
            m = item["memory"] if isinstance(item, dict) and "memory" in item else item
            score = (
                item.get("combined_score", 1.0)
                if isinstance(item, dict)
                else getattr(item, "confidence", 1.0)
            )
            mtype = getattr(m, "memory_type", "MEMORY")
            title = getattr(m, "title", "Untitled")
            status = getattr(m, "status", "ACTIVE")
            confidence = getattr(m, "confidence", 1.0)
            importance = getattr(m, "importance", 1.0)
            authority = getattr(m, "authority", "UNKNOWN")
            source_type = getattr(m, "source_type", "LOCAL")
            summary = getattr(m, "summary", "")
            content = getattr(m, "content", "")
            mid = getattr(m, "id", "")
            version = getattr(m, "version", 1)
            evidences = getattr(m, "evidences", [])

            lines.append(f"### [{mtype}] {title} (Score: {score:.2f})")
            lines.append(
                f"- **Status**: `{status}` | **Confidence**: {confidence:.2f} | **Importance**: {importance:.2f}"
            )
            lines.append(
                f"- **Authority**: `{authority}` | **Source**: `{source_type}`"
            )
            lines.append(f"**Summary**: {summary}")
            lines.append(f"**Content**: {content}")
            if evidences:
                ev_str = ", ".join(
                    f"`{getattr(e, 'file_path', '')}:{getattr(e, 'line_start', 1)}`"
                    for e in evidences
                )
                lines.append(f"**Evidence Grounding**: {ev_str}")
            lines.append(f"**ID**: `{mid}` | **Version**: v{version}")
            lines.append("")
        return "\n".join(lines)


@mcp_server.tool(
    name="get_relevant_memories",
    description="Retrieves memories most relevant to a specific task and set of target files, ranked by hybrid relevance.",
)
async def get_relevant_memories(
    task_text: str,
    target_files: list[str] | None = None,
    limit: int = 5,
    project_id_or_path: str = ".",
) -> str:
    """Retrieve memories prioritized by hybrid scoring against the planned task."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.MEMORY_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        scored = await retrieval_engine.retrieve(
            session,
            project_id=project.id,
            query=task_text,
            target_files=target_files,
            limit=limit,
        )
        memory_items = [s for s in scored if s.item_type == "memory"]
        if not memory_items:
            return f"No relevant memories found for task '{task_text}' in project '{project.name}'."

        lines = [f"# Relevant Memories for Task ({len(memory_items)})"]
        for item in memory_items:
            lines.append(
                f"### [{item.memory_type or 'MEMORY'}] {item.title} (Score: {item.score:.2f})"
            )
            lines.append(
                f"- **Status**: `{item.status}` | **Layer**: `{item.layer or 'N/A'}`"
            )
            lines.append(f"**Summary**: {item.summary}")
            lines.append(f"**Content**: {item.content}")
            if item.provenance:
                lines.append(f"**Provenance**: {item.provenance}")
            lines.append("")
        return "\n".join(lines)


@mcp_server.tool(
    name="get_architecture_context",
    description="Returns high-level structural architecture of the project (modules, primary APIs, models, and dependencies).",
)
async def get_architecture_context(
    project_id_or_path: str = ".", depth: int = 2
) -> str:
    """Retrieve synthesized structural architecture of the project."""
    return await project_get_architecture(
        project_id_or_path=project_id_or_path, depth=depth
    )


@mcp_server.tool(
    name="get_decisions",
    description="Returns all active architectural decisions (ADRs) and trade-off rationales.",
)
async def get_decisions(project_id_or_path: str = ".") -> str:
    """Fetch architectural decisions."""
    return await memory_get_decisions(project_id_or_path=project_id_or_path)


@mcp_server.tool(
    name="get_known_failures",
    description="Returns previous bug post-mortems, failed attempts, and anti-patterns to prevent repeating past mistakes.",
)
async def get_known_failures(project_id_or_path: str = ".") -> str:
    """Fetch known failure post-mortems."""
    return await memory_get_failures(project_id_or_path=project_id_or_path)


@mcp_server.tool(
    name="search_code_knowledge",
    description=(
        "Searches indexed AST symbols (classes, functions, methods, modules) without dumping full file contents. "
        "Returns qualified names, signatures, file paths, line ranges, and docstrings."
    ),
)
async def search_code_knowledge(
    query: str,
    entity_type: str | None = None,
    limit: int = 10,
    project_id_or_path: str = ".",
) -> str:
    """Search code entity index without exposing raw repository contents."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.GRAPH_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        stmt = select(CodeEntity).where(CodeEntity.project_id == project.id)
        if entity_type:
            stmt = stmt.where(CodeEntity.entity_type == entity_type.upper())
        q = f"%{query}%"
        stmt = stmt.where(
            (CodeEntity.name.ilike(q))
            | (CodeEntity.qualified_name.ilike(q))
            | (CodeEntity.signature.ilike(q))
        ).limit(limit)

        res = await session.execute(stmt)
        entities = res.scalars().all()
        if not entities:
            return f"No code entities matching '{query}' found in project '{project.name}'."

        lines = [f"# Code Intelligence Search: '{query}' ({len(entities)} symbols)"]
        for e in entities:
            lines.append(f"### `{e.qualified_name}` ({e.entity_type})")
            lines.append(
                f"- **File**: `{e.file_path}` (Lines {e.start_line}-{e.end_line})"
            )
            lines.append(
                f"- **Language**: {e.language} | **Hash**: `{e.content_hash[:12]}...`"
            )
            if e.signature:
                lines.append(f"- **Signature**: `{e.signature}`")
            doc = (
                (e.entity_metadata or {}).get("docstring")
                if hasattr(e, "entity_metadata")
                else None
            )
            if doc:
                lines.append(f"- **Docstring**: {str(doc)[:150]}...")
            lines.append("")
        return "\n".join(lines)


@mcp_server.tool(
    name="get_symbol_context",
    description="Inspects detailed AST symbol definition, signature, location, dependencies, callers, and linked constraints.",
)
async def get_symbol_context(qualified_name: str, project_id_or_path: str = ".") -> str:
    """Retrieve detailed AST component definition and graph connections."""
    return await project_get_component(
        qualified_name=qualified_name, project_id_or_path=project_id_or_path
    )


@mcp_server.tool(
    name="get_related_components",
    description="Returns both upstream callers (blast radius) and downstream dependencies for an entity or file.",
)
async def get_related_components(
    entity_name: str, project_id_or_path: str = ".", depth: int = 2
) -> str:
    """Get complete relational neighborhood (callers + dependencies) for an entity."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.GRAPH_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        deps = await graph_service.get_dependencies(
            session, project.id, entity_name, depth=depth
        )
        callers = await graph_service.get_dependents(
            session, project.id, entity_name, depth=depth
        )

        lines = [f"# Related Components for '{entity_name}'"]
        lines.append(f"## Downstream Dependencies ({len(deps)})")
        for d in deps[:10]:
            lines.append(
                f"- [Depth {d['depth']}] `{d['relationship']}` -> **{d['name']}** ({d['type']} in `{d['file']}`)"
            )

        lines.append("")
        lines.append(f"## Upstream Callers / Blast Radius ({len(callers)})")
        for c in callers[:10]:
            lines.append(
                f"- [Depth {c['depth']}] **{c['name']}** ({c['type']} in `{c['file']}`) -> `{c['relationship']}`"
            )
        return "\n".join(lines)


@mcp_server.tool(
    name="get_dependency_context",
    description="Returns downstream dependencies and imports of an entity up to depth N.",
)
async def get_dependency_context(
    entity_name: str, project_id_or_path: str = ".", depth: int = 2
) -> str:
    """List downstream dependencies for an entity."""
    return await graph_get_dependencies(
        entity_name=entity_name, project_id_or_path=project_id_or_path, depth=depth
    )


@mcp_server.tool(
    name="get_git_context",
    description="Returns current Git context (HEAD commit, active branch, remote, and unindexed modified files).",
)
async def get_git_context(project_id_or_path: str = ".") -> str:
    """Returns real Git state without relying solely on equality of commits."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.PROJECT_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        source = get_repository_source(project)
        branch = source.get_current_branch()
        head = source.get_head_commit()
        modified = (
            source.get_modified_files(project.last_indexed_commit)
            if project.last_indexed_commit
            else []
        )

        lines = [
            f"# Git Context for {project.name}",
            f"- **Active Branch**: `{branch}`",
            f"- **HEAD Commit**: `{head or 'N/A'}`",
            f"- **Last Indexed Commit**: `{project.last_indexed_commit or 'Never'}`",
            f"- **Source Type**: `{project.source_type}`",
            f"- **Repository URL**: `{project.repository_url or project.clone_url or 'Local'}`",
            "",
            f"## Unindexed / Changed Files Since Last Scan ({len(modified)})",
        ]
        if modified:
            for f in modified[:15]:
                p = f.file_path if hasattr(f, "file_path") else str(f)
                lines.append(f"- `{p}`")
            if len(modified) > 15:
                lines.append(f"- ... and {len(modified) - 15} more")
        else:
            lines.append("- Working tree is fully synchronized with CortexForge index.")
        return "\n".join(lines)


@mcp_server.tool(
    name="record_memory",
    description="Records project knowledge from an AI agent. Enforces epistemic authority: agent observations are stored as AGENT_OBSERVED and cannot self-assert USER_CONFIRMED authority.",
)
async def record_memory(
    title: str,
    content: str,
    summary: str | None = None,
    memory_type: str = "LESSON",
    evidence_file: str | None = None,
    evidence_line_start: int | None = None,
    evidence_line_end: int | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Store agent observation with enforced non-inflated authority."""
    return await memory_create(
        title=title,
        content=content,
        summary=summary or content[:150],
        memory_type=memory_type,
        evidence_file=evidence_file,
        evidence_line_start=evidence_line_start,
        evidence_line_end=evidence_line_end,
        project_id_or_path=project_id_or_path,
        approval_token=None,
    )


@mcp_server.tool(
    name="record_decision",
    description="Records an architectural decision made during coding. Stored with AGENT_OBSERVED authority for verification.",
)
async def record_decision(
    title: str,
    rationale: str,
    component: str | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record an architectural decision."""
    return await task_record_decision(
        title=title,
        rationale=rationale,
        component=component,
        project_id_or_path=project_id_or_path,
    )


@mcp_server.tool(
    name="record_failure",
    description="Records a structured failure episode with error details, attempted fix, and affected component to prevent future regressions.",
)
async def record_failure(
    title: str,
    error_description: str,
    attempted_fix: str,
    component: str | None = None,
    stack_trace: str | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record a failure episode."""
    return await task_record_failure(
        title=title,
        error_description=error_description,
        attempted_fix=attempted_fix,
        component=component,
        stack_trace=stack_trace,
        project_id_or_path=project_id_or_path,
    )


@mcp_server.tool(
    name="record_lesson",
    description="Records a durable engineering lesson learned or convention.",
)
async def record_lesson(
    title: str,
    lesson: str,
    context: str | None = None,
    evidence_file: str | None = None,
    project_id_or_path: str = ".",
) -> str:
    """Record a durable engineering lesson."""
    content = f"{lesson}\n\nContext: {context}" if context else lesson
    return await record_memory(
        title=title,
        content=content,
        summary=lesson[:150],
        memory_type="LESSON",
        evidence_file=evidence_file,
        project_id_or_path=project_id_or_path,
    )


@mcp_server.tool(
    name="trigger_project_scan",
    description="Triggers AST repository scanning and code-intelligence indexing via the appropriate RepositorySource adapter (local or remote).",
)
async def trigger_project_scan(
    project_id_or_path: str = ".", force_full: bool = False
) -> str:
    """Scan and index project source code."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.PROJECT_SCAN
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        source = get_repository_source(project)
        res = await scanner.scan_project(
            session, project.id, repository_source=source, force_full=force_full
        )
        await session.commit()
        head_sha = source.get_head_commit()
        return (
            f"Project scan complete for '{project.name}':\n"
            f"- Files scanned: {res.files_scanned}\n"
            f"- Entities extracted: {res.entities_extracted}\n"
            f"- Relationships identified: {res.relationships_extracted}\n"
            f"- Commit: {head_sha or 'N/A'}"
        )


@mcp_server.tool(
    name="get_project_status",
    description="Returns comprehensive status of project cognition, entity counts, memory counts, indexing state, and health.",
)
async def get_project_status(project_id_or_path: str = ".") -> str:
    """Report project cognition and indexing status."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(
            session, project_id_or_path, required_permission=Permission.PROJECT_READ
        )
        if not project:
            return f"Error: Project could not be resolved or access denied for '{project_id_or_path}'."

        source = get_repository_source(project)
        branch = source.get_current_branch()
        head = source.get_head_commit()

        total_mems = await session.scalar(
            select(func.count(Memory.id)).where(Memory.project_id == project.id)
        )
        total_entities = await session.scalar(
            select(func.count(CodeEntity.id)).where(CodeEntity.project_id == project.id)
        )
        active_decisions = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id,
                Memory.memory_type == "DECISION",
                Memory.status == "ACTIVE",
            )
        )
        active_failures = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id,
                Memory.memory_type == "FAILURE",
                Memory.status == "ACTIVE",
            )
        )

        return (
            f"# Project Status: {project.name}\n"
            f"- **Project ID**: `{project.id}`\n"
            f"- **Source**: `{project.source_type}` (`{project.local_path or project.repository_url}`)\n"
            f"- **Git**: Branch `{branch}`, HEAD `{head[:8] if head else 'N/A'}`\n"
            f"- **Last Scanned**: `{project.updated_at.strftime('%Y-%m-%d %H:%M') if project.updated_at else 'Never'}` (Commit: `{project.last_indexed_commit or 'N/A'}`)\n"
            f"- **Code Entities**: {total_entities}\n"
            f"- **Memories**: {total_mems} ({active_decisions} active decisions, {active_failures} active failures)\n"
            f"- **Cognition Status**: READY"
        )


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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.PROJECT_READ)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.ARCHITECTURE_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        arch = await graph_service.get_project_architecture(
            session, project.id, depth=depth
        )
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
            lines.append(
                f"### {mod.module_path} ({mod.file_count} files, {mod.entity_count} symbols)"
            )
            for comp in mod.top_level_components[:8]:
                deps_str = (
                    f" -> [{', '.join(comp.dependencies[:3])}]"
                    if comp.dependencies
                    else ""
                )
                lines.append(
                    f"  - `{comp.entity_type}` **{comp.name}** ({comp.file_path}:{comp.line_range[0]}-{comp.line_range[1]}){deps_str}"
                )
            lines.append("")

        if arch.primary_apis:
            lines.append("## Primary APIs / Entrypoints")
            for api in arch.primary_apis:
                lines.append(
                    f"- **{api.name}** (`{api.file_path}`): {api.signature or ''}"
                )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.ARCHITECTURE_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        status, entity, candidates = await graph_service.resolve_entity(
            session, project.id, qualified_name
        )
        if status == "AMBIGUOUS":
            cand_list = "\n".join(f"- `{c}`" for c in candidates)
            return (
                f"Error: Symbol '{qualified_name}' is ambiguous in project '{project.name}'. "
                f"Multiple matching components found:\n{cand_list}\n"
                f"Please specify the full qualified name."
            )
        if status == "NOT_FOUND" or not entity:
            return (
                f"Component '{qualified_name}' not found in project '{project.name}'."
            )

        deps = await graph_service.get_dependencies(
            session, project.id, entity.id, depth=2
        )
        callers = await graph_service.get_dependents(
            session, project.id, entity.id, depth=2
        )

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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
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
            lines.append(
                f"### {status_tag} [{m.memory_type}] {m.title} (Score: {score:.2f})"
            )
            lines.append(f"**Summary**: {m.summary}")
            lines.append(f"**Content**: {m.content}")
            if m.evidences:
                ev_str = ", ".join(
                    f"`{e.file_path}:{e.line_start or 1}`" for e in m.evidences
                )
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

        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_READ)
        if not project:
            return (
                f"Access denied: you are not authorized to view memory '{memory_id}'."
            )

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
                lines.append(
                    f"- `{ev.file_path}` (Lines {ev.line_start or 1}-{ev.line_end or 1}) [Commit: {ev.commit_sha or 'N/A'}]"
                )
        else:
            lines.append("- None attached.")

        lines.append("")
        lines.append("## Version History")
        if mem.versions:
            for v in mem.versions:
                lines.append(
                    f"- **v{v.version}** ({v.created_at.strftime('%Y-%m-%d %H:%M')}): {v.change_reason}"
                )
        return "\n".join(lines)


@mcp_server.tool(
    name="memory_create",
    description="Registers a new project memory (DECISION, CONSTRAINT, FAILURE, LESSON, etc.) with evidence grounding. Verifies agent observations before promotion to active truth. Human authorization requires a valid server-side approval token.",
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
    approval_token: str | None = None,
) -> str:
    """Store a project memory with validation, trust classification, and verification pipeline."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
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

        authority = Authority.AGENT_OBSERVED.value
        source_type = "agent_observation"

        if approval_token:
            _record, err = await ApprovalService.validate_and_consume_token(
                session=session,
                token=approval_token,
                project_id=project.id,
                title=title,
                content=content,
                memory_type=memory_type.upper(),
            )
            if err:
                return f"Error: Invalid or unapproved human confirmation token: {err}"
            authority = Authority.USER_CONFIRMED.value
            source_type = "user"

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
        if authority != Authority.USER_CONFIRMED.value:
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
    name="memory_request_human_approval",
    description="Requests human operator approval for promoting a high-authority memory (e.g. DECISION, CONSTRAINT). Generates a server-managed approval record that must be approved via trusted API/UI before applying.",
)
async def memory_request_human_approval(
    title: str,
    content: str,
    memory_type: str = "LESSON",
    requested_by: str = "mcp_agent",
    reason: str = "",
    project_id_or_path: str = ".",
) -> str:
    """Submit a pending human approval request."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        record = await ApprovalService.create_approval_request(
            session=session,
            project_id=project.id,
            title=title,
            content=content,
            memory_type=memory_type.upper(),
            requested_by=requested_by,
        )
        await session.commit()
        return (
            f"Created pending human approval request (ID: `{record.id}`, Token: `{record.token}`). "
            f"A human operator must approve this request via trusted application boundary before it can be committed with USER_CONFIRMED authority."
        )


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
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"Memory with ID '{memory_id}' not found."
        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_UPDATE)
        if not project:
            return f"Access denied: unauthorized to update memory '{memory_id}'."

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
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"Memory with ID '{memory_id}' not found."
        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_UPDATE)
        if not project:
            return f"Access denied: unauthorized to deprecate memory '{memory_id}'."

        dep = await memory_service.deprecate_memory(
            session,
            memory_id=memory_id,
            superseded_by_id=superseded_by_id,
            reason=reason,
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

        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_VERIFY)
        if not project:
            return f"Access denied: unauthorized to verify memory '{memory_id}'."

        st = await verification_engine.verify_single_memory(
            session, mem, project.local_path
        )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        failures = await memory_service.get_failures(session, project.id)
        if not failures:
            return f"No failure post-mortems recorded for project '{project.name}'."

        lines = [
            f"# Historical Failures & Anti-Patterns for {project.name} ({len(failures)})"
        ]
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        constraints = await memory_service.get_constraints(session, project.id)
        if not constraints:
            return f"No operational constraints recorded for project '{project.name}'."

        lines = [
            f"# Active Architectural Constraints for {project.name} ({len(constraints)})"
        ]
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        lessons = await memory_service.list_memories(
            session, project.id, layer="L5", status="ACTIVE"
        )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.GRAPH_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        deps = await graph_service.get_dependencies(
            session, project.id, entity_name, depth=depth
        )
        if not deps:
            return f"No dependencies found for entity '{entity_name}'."

        lines = [f"# Dependencies for {entity_name} (depth <= {depth})"]
        for d in deps:
            lines.append(
                f"- [Depth {d['depth']}] `{d['relationship']}` -> **{d['name']}** ({d['type']} in `{d['file']}`)"
            )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.GRAPH_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        callers = await graph_service.get_dependents(
            session, project.id, entity_name, depth=depth
        )
        if not callers:
            return f"No callers or dependents found for entity '{entity_name}'."

        lines = [
            f"# Callers & Dependents for {entity_name} (blast radius depth <= {depth})"
        ]
        for c in callers:
            lines.append(
                f"- [Depth {c['depth']}] **{c['name']}** ({c['type']} in `{c['file']}`) -> `{c['relationship']}`"
            )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.GRAPH_READ)
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
    description="Records a structured failure episode with normalized fingerprint, root-cause claim tracking, and fix attempt history.",
)
async def task_record_failure(
    title: str,
    error_description: str,
    attempted_fix: str,
    task_id: str | None = None,
    component: str | None = None,
    stack_trace: str | None = None,
    root_cause_proposal: str | None = None,
    fix_worked: bool = False,
    project_id_or_path: str = ".",
) -> str:
    """Record a failure post-mortem with actual FailureEpisode and FixAttempt entities."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        norm_trace = failure_intelligence.normalizer.normalize_stack_trace(
            stack_trace or error_description
        )
        sig = failure_intelligence.normalizer.compute_signature(
            "TaskFailure", norm_trace
        )

        root_cause_claim_id = None
        if root_cause_proposal:
            rc_claim = Claim(
                project_id=project.id,
                claim_type="ROOT_CAUSE",
                claim_key=f"failure_root_cause:{sig}",
                proposition=root_cause_proposal,
                status=ClaimStatus.UNVERIFIED.value,
                confidence=0.5,
                authority=Authority.AGENT_OBSERVED.value,
            )
            session.add(rc_claim)
            await session.flush()
            root_cause_claim_id = rc_claim.id

        episode = FailureEpisode(
            project_id=project.id,
            task_id=task_id,
            failure_signature=sig,
            error_class="TaskFailure",
            error_message=error_description[:500],
            normalized_trace=norm_trace,
            attempted_approach=title,
            root_cause=root_cause_proposal,
            root_cause_claim_id=root_cause_claim_id,
            affected_files=[component] if component else [],
        )
        session.add(episode)
        await session.flush()

        fix = FixAttempt(
            failure_episode_id=episode.id,
            attempted_fix=attempted_fix,
            success=fix_worked,
            why_worked_or_failed=(
                f"Attempted fix: {attempted_fix}. Worked: {fix_worked}"
            ),
        )
        session.add(fix)
        await session.flush()

        mem_res = await memory_create(
            title=title,
            content=f"Error: {error_description}\nAttempted Fix: {attempted_fix}\nSignature: {sig}",
            summary=f"Failure episode {sig} in {component or 'system'}",
            memory_type="FAILURE",
            evidence_file=component,
            project_id_or_path=project_id_or_path,
        )
        await session.commit()
        return (
            f"Recorded FailureEpisode `{episode.id}` (Signature: `{sig}`, RootCauseClaim: `{root_cause_claim_id or 'NULL'}`). "
            f"FixAttempt `{fix.id}` (Success: {fix_worked}).\nMemory: {mem_res}"
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
    caller_ctx = _CURRENT_MCP_CALLER.get() or {}
    caller_agent_id = caller_ctx.get("agent_id") or os.environ.get(
        "CORTEX_CALLER_AGENT_ID"
    )
    caller_user_id = caller_ctx.get("user_id")

    if caller_agent_id:
        if agent_id and agent_id != "generic_agent" and agent_id != caller_agent_id:
            return f"Error: Authenticated agent cannot impersonate different agent '{agent_id}'."
        effective_agent_id = caller_agent_id
    else:
        effective_agent_id = agent_id

    await init_db()
    async with session_scope() as session:
        if (
            effective_agent_id
            and effective_agent_id != "generic_agent"
            and not caller_agent_id
        ):
            ag = await session.get(Agent, effective_agent_id)
            if not ag or ag.status != "ACTIVE":
                return f"Error: Agent '{effective_agent_id}' is invalid or inactive."
            if caller_user_id and ag.owner_user_id != caller_user_id:
                return f"Error: Agent '{effective_agent_id}' does not belong to authenticated caller."

        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.JOB_CREATE)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        task, context = await orchestrator.start_task(
            session=session,
            project_id=project.id,
            task_text=task_text,
            agent_id=effective_agent_id,
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
        task = await session.get(AgentTask, task_id)
        if not task:
            return f"Task with ID '{task_id}' not found."
        project = await _resolve_project(session, task.project_id, required_permission=Permission.JOB_CREATE)
        if not project:
            return f"Access denied: unauthorized for task '{task_id}'."

        ev_upper = event_type.upper()
        if "TOOL" in ev_upper and tool_name:
            await orchestrator.record_tool_call(
                session, task_id=task_id, tool_name=tool_name, tool_result=details
            )
            return f"Recorded tool call '{tool_name}' for task '{task_id}'."
        elif "FILE" in ev_upper and file_path:
            impact = await orchestrator.record_file_change(
                session, task_id=task_id, file_path=file_path
            )
            return f"Recorded file change '{file_path}'. Flagged {len(impact.memories_flagged_stale)} stale memories."
        elif "TEST" in ev_upper:
            await orchestrator.record_test_result(
                session,
                task_id=task_id,
                test_name=tool_name or "test",
                status=status or "PASSED",
                error_text=details,
            )
            return f"Recorded test result for task '{task_id}'."
        else:
            await orchestrator.record_tool_call(
                session, task_id=task_id, tool_name=event_type, tool_result=details
            )
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
        task_row = await session.get(AgentTask, task_id)
        if not task_row:
            return f"Task with ID '{task_id}' not found."
        project = await _resolve_project(session, task_row.project_id, required_permission=Permission.JOB_CREATE)
        if not project:
            return f"Access denied: unauthorized for task '{task_id}'."

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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        total_mems = await session.scalar(
            select(func.count(Memory.id)).where(Memory.project_id == project.id)
        )
        active_mems = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id, Memory.status == "ACTIVE"
            )
        )
        stale_mems = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id, Memory.status == "STALE"
            )
        )
        conflicted_mems = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id, Memory.status == "CONFLICTED"
            )
        )
        archived_mems = await session.scalar(
            select(func.count(Memory.id)).where(
                Memory.project_id == project.id, Memory.status == "ARCHIVED"
            )
        )
        entities_count = await session.scalar(
            select(func.count(CodeEntity.id)).where(CodeEntity.project_id == project.id)
        )

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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.ARCHITECTURE_READ)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        violations = await invariant_engine.check_project_invariants(
            session, project_id=project.id
        )
        if not violations:
            return f"Architecture Invariant Check: All boundary rules passed for '{project.name}'. Zero violations detected."

        lines = [
            f"# Architecture Invariant Violations for {project.name} ({len(violations)})"
        ]
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
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"Error: Memory with ID '{memory_id}' not found."

        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Access denied: you are not authorized to view provenance for memory '{memory_id}'."

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
            lines.append(
                f"- `{ev['file_path']}:{ev.get('line_start') or 1}` [Type: {ev['source_type']}, Confidence: {ev.get('confidence', 1.0):.2f}]"
            )

        if trace["symbols"]:
            lines.append("")
            lines.append(f"## Anchored Symbols ({len(trace['symbols'])})")
            for sym in trace["symbols"]:
                lines.append(
                    f"- **{sym.get('name')}** (`{sym.get('qualified_name')}`) in `{sym.get('file_path')}`"
                )

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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.SNAPSHOT_CREATE)
        if not project:
            return f"Error: Project could not be resolved for '{project_id_or_path}'."

        snap = await snapshot_engine.take_snapshot(
            session, project_id=project.id, commit_sha=commit_sha
        )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.PROJECT_READ)
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
            lines.append(
                f"### Task: {t.task_text} (Score: {score}, Status: {t.status}, Success: {t.success})"
            )
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
                    err_cls = (
                        fe.get("error_class")
                        if isinstance(fe, dict)
                        else getattr(fe, "error_class", "Error")
                    )
                    err_msg = (
                        fe.get("error_message")
                        if isinstance(fe, dict)
                        else getattr(fe, "error_message", "")
                    )
                    fix_stat = (
                        fe.get("fix_status")
                        if isinstance(fe, dict)
                        else getattr(fe, "fix_status", "UNRESOLVED")
                    )
                    lines.append(f"  - [{err_cls}] {err_msg} (Fix Status: {fix_stat})")
            if st.get("fix_attempts"):
                lines.append("  **Successful Fixes & Approaches**:")
                for fa in st["fix_attempts"]:
                    appr = (
                        fa.get("approach_description")
                        if isinstance(fa, dict)
                        else getattr(fa, "approach_description", "")
                    )
                    outc = (
                        fa.get("outcome")
                        if isinstance(fa, dict)
                        else getattr(fa, "outcome", "")
                    )
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_CREATE)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_VERIFY)
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
        mem = await memory_service.get_memory(session, memory_id)
        if not mem:
            return f"No claims recorded for memory `{memory_id}`."

        project = await _resolve_project(session, mem.project_id, required_permission=Permission.MEMORY_READ)
        if not project:
            return f"Access denied: you are not authorized to view claims for memory '{memory_id}'."

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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
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
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.PROJECT_READ)
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
async def memory_get_decisions_log(
    project_id_or_path: str = ".", limit: int = 20
) -> str:
    """Explain how the project's beliefs got to their current state."""
    await init_db()
    async with session_scope() as session:
        project = await _resolve_project(session, project_id_or_path, required_permission=Permission.MEMORY_READ)
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


def create_mcp_streamable_app(
    base_url: str | None = None,
    public_mcp_path: str = "/mcp",
):
    """Factory creating the Streamable HTTP Starlette app, session manager, and OAuth provider.

    Exposes:
    - /mcp (Streamable HTTP transport)
    - /.well-known/oauth-authorization-server
    - /.well-known/oauth-protected-resource/mcp
    - /authorize, /token, /register, /revoke
    """
    import urllib.parse

    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
    from mcp.server.streamable_http import TransportSecuritySettings
    from pydantic import AnyHttpUrl

    from cortexforge.security.mcp_auth import (
        CortexForgeMCPContextMiddleware,
        CortexForgeOAuthProvider,
        CortexForgeTokenVerifier,
    )

    if not base_url:
        base_url = os.environ.get("CORTEX_PUBLIC_URL", "http://localhost:8000").rstrip("/")

    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = f"https://{base_url}"

    mcp_url = f"{base_url}{public_mcp_path}"

    provider = CortexForgeOAuthProvider()
    verifier = CortexForgeTokenVerifier(provider)

    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(base_url),
        resource_server_url=AnyHttpUrl(mcp_url),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        validate_token_resource=False,
        required_scopes=None,
    )

    # Build transport security with explicit origin/host allowlists
    allowed_hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    ]
    allowed_origins = [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
    ]

    parsed_base = urllib.parse.urlparse(base_url)
    if parsed_base.netloc:
        allowed_hosts.append(parsed_base.netloc)
        if ":" not in parsed_base.netloc:
            allowed_hosts.append(f"{parsed_base.netloc}:*")
        allowed_origins.append(f"{parsed_base.scheme}://{parsed_base.netloc}")
        allowed_origins.append(f"{parsed_base.scheme}://{parsed_base.netloc}:*")

    extra_origins = os.environ.get("CORTEX_ALLOWED_ORIGINS", "")
    if extra_origins:
        for o in extra_origins.split(","):
            o = o.strip()
            if o and o != "*":
                allowed_origins.append(o)
                p = urllib.parse.urlparse(o)
                if p.netloc:
                    allowed_hosts.append(p.netloc)

    extra_hosts = os.environ.get("CORTEX_MCP_ALLOWED_HOSTS", "")
    if extra_hosts:
        for h in extra_hosts.split(","):
            h = h.strip()
            if h:
                allowed_hosts.append(h)

    transport_sec = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(set(allowed_hosts)),
        allowed_origins=list(set(allowed_origins)),
    )

    starlette_app = mcp_server._lowlevel_server.streamable_http_app(
        streamable_http_path=public_mcp_path,
        auth=auth_settings,
        token_verifier=verifier,
        auth_server_provider=provider,
        transport_security=transport_sec,
    )

    starlette_app.add_middleware(CortexForgeMCPContextMiddleware)

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    async def revoke_endpoint(request: Request) -> Response:
        token_val = None
        try:
            form = await request.form()
            token_val = form.get("token")
        except Exception:
            token_val = None
        if not token_val:
            try:
                data = await request.json()
                token_val = data.get("token")
            except Exception:
                token_val = None
        if token_val:
            await provider.revoke_token(str(token_val))
        return Response(status_code=200)

    starlette_app.routes.append(Route("/revoke", endpoint=revoke_endpoint, methods=["POST"]))

    session_manager = mcp_server._lowlevel_server._session_manager
    return starlette_app, session_manager, provider


def main() -> None:
    """Run MCP server over stdio transport."""
    mcp_server.run()


if __name__ == "__main__":
    main()

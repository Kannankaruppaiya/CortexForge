"""REST API routes for Projects and Repository Scanning."""

import logging
import os
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.git_service import (
    inspect_local_repository,
    validate_git_url,
)
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import get_db_session
from cortexforge.core.models import CodeEntity, Memory, Project, ProjectMembership, User
from cortexforge.core.schemas import (
    ArchitectureResponse,
    DirectoryBrowseResponse,
    DirectoryEntry,
    LocalRepoValidationRequest,
    LocalRepoValidationResponse,
    ProjectCreate,
    ProjectMembershipCreate,
    ProjectMembershipRead,
    ProjectMembershipUpdate,
    ProjectRead,
    ScanRequest,
    ScanResponse,
)
from cortexforge.evaluation.runner import EvaluationRunner
from cortexforge.graph.service import GraphService
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine
from cortexforge.security.audit import AuditService
from cortexforge.security.auth import (
    Principal,
    RequireProjectAccess,
    get_current_principal,
    validate_local_registration_path,
)
from cortexforge.security.managed_workspace import get_managed_workspace_service
from cortexforge.security.policy import Permission

router = APIRouter(prefix="/projects", tags=["projects"])
scanner = RepositoryScanner()
graph_service = GraphService()
retrieval_engine = HybridRetrievalEngine(graph_service=graph_service)
context_composer = ContextComposer(
    retrieval_engine=retrieval_engine, graph_service=graph_service
)
evaluation_runner = EvaluationRunner(
    retrieval_engine=retrieval_engine,
    context_composer=context_composer,
    scanner=scanner,
)


@router.post("/validate-local", response_model=LocalRepoValidationResponse)
async def validate_local_project_path(
    payload: LocalRepoValidationRequest,
    principal: Principal = Depends(get_current_principal),
) -> LocalRepoValidationResponse:
    """Validate a candidate local repository path across the host filesystem."""
    try:
        canonical = validate_local_registration_path(payload.path)
    except HTTPException as exc:
        return LocalRepoValidationResponse(
            valid=False,
            is_git=False,
            path=payload.path,
            error=exc.detail,
        )
    info = inspect_local_repository(canonical)
    return LocalRepoValidationResponse(**info)


@router.get("/browse-directories", response_model=DirectoryBrowseResponse)
async def browse_workspace_directories(
    path: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> DirectoryBrowseResponse:
    """List subdirectories across the host filesystem for interactive local repository selection."""
    import string
    from pathlib import Path

    is_windows = os.name == "nt"

    # Handle special Windows root listing
    if is_windows and path in ("__DRIVES__", "DRIVES"):
        entries: list[DirectoryEntry] = []
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                entries.append(
                    DirectoryEntry(
                        name=f"{letter}:",
                        path=drive_path,
                        is_dir=True,
                        is_git=(Path(drive_path) / ".git").exists(),
                    )
                )
        return DirectoryBrowseResponse(
            current_path="DRIVES",
            parent_path=None,
            workspace_root="DRIVES",
            directories=entries,
            is_windows=True,
            is_drive_root=True,
        )

    # Normalize Windows drive letter only (e.g. "C:" -> "C:\")
    clean_path = path.strip() if path else None
    if (
        clean_path
        and is_windows
        and len(clean_path) == 2
        and clean_path[1] == ":"
        and clean_path[0].isalpha()
    ):
        clean_path = f"{clean_path}\\"

    # Determine starting/target directory
    if clean_path:
        try:
            target = Path(clean_path).resolve()
            if not target.exists() or not target.is_dir():
                target = Path.home().resolve()
        except Exception:
            target = Path.home().resolve()
    else:
        # Default to user home or current working dir if home fails
        try:
            target = Path.home().resolve()
        except Exception:
            target = Path(os.getcwd()).resolve()

    # Determine parent path and drive root status
    parent_path: str | None = None
    is_drive_root = False
    if is_windows:
        if target.parent == target or str(target) == target.anchor:
            parent_path = "DRIVES"
            is_drive_root = True
        else:
            parent_path = str(target.parent)
    else:
        if target.parent != target and str(target) != "/":
            parent_path = str(target.parent)

    entries: list[DirectoryEntry] = []
    scan_error: str | None = None
    try:
        with os.scandir(target) as it:
            for entry in it:
                if entry.name.startswith((".", "$")):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        is_git = (Path(entry.path) / ".git").exists()
                        entries.append(
                            DirectoryEntry(
                                name=entry.name,
                                path=str(Path(entry.path).resolve()),
                                is_dir=True,
                                is_git=is_git,
                            )
                        )
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError) as e:
        logger.warning("Error scanning directory %s: %s", target, e)
        scan_error = f"Inaccessible directory: {e}"

    entries.sort(key=lambda d: (not d.is_git, d.name.lower()))

    return DirectoryBrowseResponse(
        current_path=str(target),
        parent_path=parent_path,
        workspace_root=str(target),
        directories=entries,
        is_windows=is_windows,
        is_drive_root=is_drive_root,
        error=scan_error,
    )


@router.post("/pick-directory")
async def open_os_directory_picker(
    principal: Principal = Depends(get_current_principal),
) -> dict:
    """Attempt to open native OS directory picker dialog on the host machine."""
    import asyncio

    gui_error: str | None = None

    def _pick():
        nonlocal gui_error
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder_selected = filedialog.askdirectory(
                title="Select Local Repository Folder for CortexForge"
            )
            root.destroy()
            return folder_selected
        except Exception as exc:
            logger.warning("Native directory picker failed: %s", exc)
            gui_error = str(exc)
            return None

    try:
        selected = await asyncio.to_thread(_pick)
        if selected:
            try:
                canonical = validate_local_registration_path(selected)
                info = inspect_local_repository(canonical)
                return {
                    "path": canonical,
                    "valid": info.get("valid", True),
                    "info": info,
                    "gui_available": True,
                }
            except HTTPException as he:
                return {
                    "path": selected,
                    "valid": False,
                    "error": he.detail,
                    "gui_available": True,
                }
        if gui_error is not None:
            return {
                "path": None,
                "canceled": False,
                "gui_available": False,
                "error": f"Native folder dialog unavailable ({gui_error}). Please use the folder browser below.",
            }
        return {"path": None, "canceled": True, "gui_available": True}
    except Exception as exc:
        return {"path": None, "error": str(exc), "gui_available": False}


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> ProjectRead:
    """Register a new repository with CortexForge across LOCAL, GITHUB, or GIT_URL sources."""
    if principal.actor_type != "USER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only authenticated human users can create projects.",
        )

    env = (
        os.environ.get("CORTEX_ENV", os.environ.get("ENVIRONMENT", "development"))
        .strip()
        .lower()
    )
    if env in ("production", "prod", "staging") and not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user identity is required to create projects in production.",
        )

    owner_id = principal.user_id or "00000000-0000-0000-0000-000000000001"
    proj_id = str(uuid.uuid4())
    src_type = payload.source_type.upper()

    if src_type == "LOCAL":
        if not payload.local_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="local_path is required when source_type is LOCAL",
            )
        # Securely validate local registration path
        canonical_path = validate_local_registration_path(payload.local_path)

        existing_stmt = select(Project).where(Project.local_path == canonical_path)
        existing_res = await session.execute(existing_stmt)
        if existing_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Project already registered at {canonical_path}",
            )

        managed_ws = False
        clone_url = None
        gh_owner = None
        gh_repo = None
        gh_id = None
        repo_url = payload.repository_url
    elif src_type in ("GITHUB", "GIT_URL"):
        ws_service = get_managed_workspace_service()
        target_dir = ws_service.prepare_project_workspace(owner_id, proj_id)
        canonical_path = str(target_dir)
        managed_ws = True

        if src_type == "GIT_URL":
            if not payload.clone_url or not validate_git_url(payload.clone_url):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid or unsafe Git clone URL: {payload.clone_url}",
                )
            clone_url = payload.clone_url.strip()
            repo_url = clone_url
            gh_owner = None
            gh_repo = None
            gh_id = None
        else:  # GITHUB
            clone_url = payload.clone_url or payload.repository_url
            if not clone_url and payload.github_owner and payload.github_repo:
                clone_url = f"https://github.com/{payload.github_owner}/{payload.github_repo}.git"
            if not clone_url or not validate_git_url(clone_url):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or missing GitHub repository clone URL",
                )
            repo_url = clone_url
            gh_owner = payload.github_owner
            gh_repo = payload.github_repo
            gh_id = payload.github_repository_id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported source_type: '{payload.source_type}'",
        )

    project = Project(
        id=proj_id,
        name=payload.name,
        source_type=src_type,
        repository_url=repo_url,
        clone_url=clone_url,
        github_owner=gh_owner,
        github_repo=gh_repo,
        github_repository_id=gh_id,
        managed_workspace=managed_ws,
        local_path=canonical_path,
        default_branch=payload.default_branch,
        language=payload.language,
        owner_user_id=owner_id,
        status="INITIALIZING",
    )
    session.add(project)
    await session.flush()

    # Create explicit OWNER membership for creator
    membership = ProjectMembership(
        user_id=owner_id,
        project_id=project.id,
        role="OWNER",
    )
    session.add(membership)

    # Submit background scan job
    from cortexforge.apps.api.routes.jobs import _dispatch_runner_background, job_store

    job_type = "IMPORT_AND_SCAN" if managed_ws else "SCAN"
    bg_job, _ = await job_store.submit(
        session=session,
        job_type=job_type,
        project_id=project.id,
        parameters={"source_type": src_type, "incremental": False},
        user_id=owner_id,
        actor_type=principal.actor_type,
        actor_id=principal.agent_id,
    )

    await session.commit()
    await session.refresh(project)

    _dispatch_runner_background()

    if principal.allowed_project_ids is not None and principal.allowed_project_ids != {
        "*"
    }:
        principal.allowed_project_ids.add(project.id)
        principal.project_roles[project.id] = "OWNER"

    await AuditService.record(
        db_session=session,
        action="PROJECT_CREATE",
        target_type="project",
        target_id=project.id,
        user_id=principal.user_id,
        actor_type=principal.actor_type,
        actor_id=principal.agent_id,
        project_id=project.id,
        details={
            "name": project.name,
            "source_type": src_type,
            "path": canonical_path,
            "managed_workspace": managed_ws,
        },
    )

    resp = ProjectRead.model_validate(project)
    resp.initial_job_id = bg_job.id
    return resp


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> list[ProjectRead]:
    """List registered projects belonging to the authenticated user."""
    if principal.is_admin or principal.allowed_project_ids == {"*"}:
        stmt = select(Project).order_by(Project.created_at.desc())
    elif principal.actor_type == "AGENT":
        allowed_list = (
            list(principal.allowed_project_ids)
            if principal.allowed_project_ids
            else ["__none__"]
        )
        stmt = (
            select(Project)
            .where(Project.id.in_(allowed_list))
            .order_by(Project.created_at.desc())
        )
    else:
        # Individual User: list owned projects OR membership projects
        allowed_list = (
            list(principal.allowed_project_ids) if principal.allowed_project_ids else []
        )
        stmt = (
            select(Project)
            .where(
                (Project.owner_user_id == principal.user_id)
                | (Project.id.in_(allowed_list) if allowed_list else False)
            )
            .order_by(Project.created_at.desc())
        )

    res = await session.execute(stmt)
    projects = res.scalars().all()
    accessible_projects = [p for p in projects if principal.can_access_project(p.id)]
    results: list[ProjectRead] = []

    if accessible_projects:
        p_ids = [p.id for p in accessible_projects]

        # Batch count entities in a single aggregate query (solves N+1 problem §30)
        ent_rows = await session.execute(
            select(CodeEntity.project_id, func.count(CodeEntity.id))
            .where(CodeEntity.project_id.in_(p_ids))
            .group_by(CodeEntity.project_id)
        )
        entity_counts = dict(ent_rows.all())

        # Batch count memories in a single aggregate query (solves N+1 problem §30)
        mem_rows = await session.execute(
            select(Memory.project_id, func.count(Memory.id))
            .where(Memory.project_id.in_(p_ids))
            .group_by(Memory.project_id)
        )
        memory_counts = dict(mem_rows.all())

        for p in accessible_projects:
            read_obj = ProjectRead.model_validate(p)
            read_obj.entity_count = entity_counts.get(p.id, 0)
            read_obj.memory_count = memory_counts.get(p.id, 0)
            results.append(read_obj)

    return results


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_READ)
    ),
) -> ProjectRead:
    """Retrieve details for a registered project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    entity_count = await session.scalar(
        select(func.count(CodeEntity.id)).where(CodeEntity.project_id == project.id)
    )
    memory_count = await session.scalar(
        select(func.count(Memory.id)).where(Memory.project_id == project.id)
    )
    read_obj = ProjectRead.model_validate(project)
    read_obj.entity_count = entity_count or 0
    read_obj.memory_count = memory_count or 0
    return read_obj


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_DELETE)
    ),
) -> None:
    """Unregister and remove a project and all associated entities (Owner/Admin only)."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    owner_user_id = project.owner_user_id
    is_managed = project.managed_workspace

    await session.delete(project)
    await session.commit()

    if is_managed and owner_user_id:
        from cortexforge.security.managed_workspace import get_managed_workspace_service

        try:
            get_managed_workspace_service().cleanup_project_workspace(
                owner_user_id, project_id
            )
        except Exception:
            logger.warning(
                "Failed to clean up managed workspace for project %s",
                project_id,
                exc_info=True,
            )


@router.get("/{project_id}/members", response_model=list[ProjectMembershipRead])
async def list_project_members(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_READ)
    ),
) -> list[ProjectMembershipRead]:
    """List all members and roles for a project."""
    res = await session.execute(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id)
    )
    members = res.scalars().all()
    return [ProjectMembershipRead.model_validate(m) for m in members]


@router.post(
    "/{project_id}/members",
    response_model=ProjectMembershipRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_member(
    project_id: str,
    payload: ProjectMembershipCreate,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_UPDATE)
    ),
) -> ProjectMembershipRead:
    """Add a member to a project with a specific role."""
    target_user = await session.get(User, payload.user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{payload.user_id}' not found.",
        )
    existing = await session.execute(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == payload.user_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this project.",
        )
    membership = ProjectMembership(
        user_id=payload.user_id,
        project_id=project_id,
        role=payload.role.upper(),
    )
    session.add(membership)
    await session.commit()
    await session.refresh(membership)
    return ProjectMembershipRead.model_validate(membership)


@router.put("/{project_id}/members/{user_id}", response_model=ProjectMembershipRead)
async def update_project_member(
    project_id: str,
    user_id: str,
    payload: ProjectMembershipUpdate,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_UPDATE)
    ),
) -> ProjectMembershipRead:
    """Update a project member's role."""
    existing = await session.execute(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    membership = existing.scalars().first()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in project.",
        )
    project = await session.get(Project, project_id)
    if project and project.owner_user_id == user_id and payload.role.upper() != "OWNER":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote project owner.",
        )
    membership.role = payload.role.upper()
    await session.commit()
    await session.refresh(membership)
    return ProjectMembershipRead.model_validate(membership)


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_project_member(
    project_id: str,
    user_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_UPDATE)
    ),
) -> None:
    """Remove a member from a project."""
    project = await session.get(Project, project_id)
    if project and project.owner_user_id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove project owner from members.",
        )
    existing = await session.execute(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    membership = existing.scalars().first()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in project.",
        )
    await session.delete(membership)
    await session.commit()


@router.post("/{project_id}/scan", response_model=ScanResponse)
async def scan_project(
    project_id: str,
    payload: ScanRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_SCAN)
    ),
) -> ScanResponse:
    """Trigger AST scan of the project repository."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    incremental = payload.incremental if payload else True
    max_files = payload.max_files if payload else None

    return await scanner.scan_project(
        session, project, incremental=incremental, max_files=max_files
    )


@router.get("/{project_id}/architecture", response_model=ArchitectureResponse)
async def get_project_architecture(
    project_id: str,
    depth: int = 2,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.ARCHITECTURE_READ)
    ),
) -> ArchitectureResponse:
    """Retrieve synthesized structural architecture of the project."""
    arch = await graph_service.get_project_architecture(
        session, project_id, depth=depth
    )
    if not arch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return arch


@router.post("/{project_id}/benchmark")
async def run_project_benchmark(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_SCAN)
    ),
) -> list[dict]:
    """Run real empirical benchmark suite on project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    scorecards = await evaluation_runner.run_benchmark(session, project.id)
    out = []
    for sc in scorecards:
        results_dict = {}
        for mode, res in sc.results.items():
            results_dict[mode] = {
                "mode": res.mode,
                # Measured from the configuration that actually ran.
                "context_items": res.context_items,
                "files_referenced": res.files_referenced,
                "files_inspected": res.files_inspected,
                "input_tokens": res.input_tokens,
                "latency_ms": round(res.latency_ms, 2),
                "duration_ms": round(res.duration_ms, 2),
                "retrieval_precision": res.retrieval_precision,
                "retrieval_recall": res.retrieval_recall,
                "relevance_basis": res.relevance_basis,
                "stale_retrieval_rate": res.stale_retrieval_rate,
                "conflicted_retrieval_rate": res.conflicted_retrieval_rate,
                "context_redundancy": res.context_redundancy,
                "provenance_coverage": res.provenance_coverage,
                # Null means "not measured by this harness", which the client must
                # render as such rather than as a zero (specification section 49).
                "task_success": res.task_success,
                "tests_passed": res.tests_passed,
                "repeated_failures": res.repeated_failures,
                "output_tokens": res.output_tokens,
                "estimated_cost_usd": res.estimated_cost_usd,
                "unmeasured_reason": res.unmeasured_reason,
            }

        out.append(
            {
                "task_id": sc.task_id,
                "task_name": sc.task_name,
                "results": results_dict,
                "token_reduction_pct": sc.token_reduction_pct,
                "exploration_reduction_pct": sc.exploration_reduction_pct,
                "tool_calls_saved": sc.tool_calls_saved,
                "measurement_notes": sc.measurement_notes,
                "metadata": {
                    "repository_commit": sc.metadata.repository_commit,
                    "benchmark_suite_version": sc.metadata.benchmark_suite_version,
                    "embedding_model": sc.metadata.embedding_model,
                    "embedding_quality_class": sc.metadata.embedding_quality_class,
                    "retrieval_config": sc.metadata.retrieval_config,
                    "project_memory_count": sc.metadata.project_memory_count,
                    "environment": sc.metadata.environment,
                    "timestamp": sc.metadata.timestamp,
                },
                "raw_log_path": sc.raw_log_path,
            }
        )
    return out


@router.get("/{project_id}/economics")
async def get_project_economics(
    project_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(
        RequireProjectAccess("project_id", permission=Permission.PROJECT_READ)
    ),
) -> dict:
    """Compute live token economics, context budget allocation, and cost savings for the project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    ent_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
    ent_res = await session.execute(ent_stmt)
    entities = list(ent_res.scalars().all())

    mem_stmt = select(Memory).where(Memory.project_id == project_id)
    mem_res = await session.execute(mem_stmt)
    memories = list(mem_res.scalars().all())

    # Count tokens per layer from real memories and entities
    l0_tokens = 250
    l1_tokens = min(4000, max(400, len(entities) * 15))
    l2_tokens = (
        sum(
            max(50, len(m.content.split()))
            for m in memories
            if m.memory_type == "CONVENTION"
        )
        or 250
    )
    l3_tokens = (
        sum(
            max(80, len(m.content.split()))
            for m in memories
            if m.memory_type == "DECISION"
        )
        or 350
    )
    l4_tokens = (
        sum(
            max(100, len(m.content.split()))
            for m in memories
            if m.memory_type in ("FAILURE", "FIX")
        )
        or 300
    )
    l5_tokens = (
        sum(
            max(60, len(m.content.split()))
            for m in memories
            if m.memory_type in ("LESSON", "CONSTRAINT")
        )
        or 250
    )

    total_project_code_tokens = max(12000, len(entities) * 45)

    profiles = {}
    multiplier_map = {"small": 0.4, "medium": 1.0, "large": 2.2}
    labels_map = {
        "small": (
            "Small Budget (Fast / Latency-Optimized)",
            "Optimized for quick bug fixes and targeted symbol lookups.",
        ),
        "medium": (
            "Medium Budget (Standard Balanced Task)",
            "Standard working context for feature additions and refactoring.",
        ),
        "large": (
            "Large Budget (Deep Cross-Subsystem Audit)",
            "Maximum depth for complex multi-module redesigns and audits.",
        ),
    }

    for prof_key, mult in multiplier_map.items():
        layer_items = [
            {
                "name": "L0 Project Identity & Framework",
                "tokens": int(l0_tokens * mult),
                "color": "bg-indigo-500",
            },
            {
                "name": "L1 Primary Architecture Graph",
                "tokens": int(l1_tokens * mult),
                "color": "bg-blue-500",
            },
            {
                "name": "L2 Code Conventions & Standards",
                "tokens": int(l2_tokens * mult),
                "color": "bg-teal-500",
            },
            {
                "name": "L3 Active Architectural Decisions",
                "tokens": int(l3_tokens * mult),
                "color": "bg-emerald-500",
            },
            {
                "name": "L4 Failure Post-Mortems",
                "tokens": int(l4_tokens * mult),
                "color": "bg-red-500",
            },
            {
                "name": "L5 Durable Lessons Learned",
                "tokens": int(l5_tokens * mult),
                "color": "bg-purple-500",
            },
        ]
        total_tokens = sum(x["tokens"] for x in layer_items)
        for item in layer_items:
            item["pct"] = round((item["tokens"] / max(1, total_tokens)) * 100, 1)

        lbl, desc = labels_map[prof_key]
        profiles[prof_key] = {
            "totalTokens": total_tokens,
            "label": lbl,
            "description": desc,
            "layers": layer_items,
        }

    cortex_avg_tokens = profiles["medium"]["totalTokens"]
    baseline_avg_tokens = total_project_code_tokens
    savings_pct = round(
        ((baseline_avg_tokens - cortex_avg_tokens) / baseline_avg_tokens) * 100, 1
    )

    cost_per_task_cortex = (cortex_avg_tokens / 1000.0) * 0.003
    cost_per_task_base = (baseline_avg_tokens / 1000.0) * 0.003
    cost_per_1k_cortex = round(cost_per_task_cortex * 1000.0, 2)
    cost_per_1k_base = round(cost_per_task_base * 1000.0, 2)

    # Query actual agent tasks to derive live measured metrics where available
    from cortexforge.core.models import AgentEvent, AgentTask

    tasks_stmt = select(AgentTask).where(AgentTask.project_id == project_id)
    tasks_res = await session.execute(tasks_stmt)
    project_tasks = tasks_res.scalars().all()

    has_measured_data = len(project_tasks) > 0
    if has_measured_data:
        task_ids = [t.id for t in project_tasks]
        events_stmt = select(AgentEvent).where(AgentEvent.task_id.in_(task_ids))
        events_res = await session.execute(events_stmt)
        all_events = events_res.scalars().all()

        tool_events = [
            e for e in all_events if (e.event_type or "").upper() == "TOOL_CALL"
        ]
        measured_tools_per_task = round(
            len(tool_events) / max(1, len(project_tasks)), 1
        )

        files_set = set()
        for e in all_events:
            p = e.payload or {}
            for k in ("path", "file_path", "target_path"):
                if k in p and isinstance(p[k], str):
                    files_set.add(p[k])
        measured_files_per_task = round(len(files_set) / max(1, len(project_tasks)), 1)

        files_explored_cortex = max(1.0, measured_files_per_task)
        tool_calls_cortex = max(1.0, measured_tools_per_task)
        metric_mode = "measured"
    else:
        # Explicit modeled benchmark reference estimate when no live task executions exist yet
        files_explored_cortex = 1.2
        tool_calls_cortex = 1.0
        metric_mode = "modelled_estimate"

    files_explored_base = max(8.0, round(min(25.0, len(entities) / 8.0), 1))
    files_reduction_pct = round(
        ((files_explored_base - files_explored_cortex) / files_explored_base) * 100, 1
    )

    tool_calls_base = round(files_explored_base * 0.75 + 1.5, 1)
    tool_calls_reduction_pct = round(
        ((tool_calls_base - tool_calls_cortex) / tool_calls_base) * 100, 1
    )

    return {
        "metric_mode": metric_mode,
        "has_measured_data": has_measured_data,
        "is_modelled_estimate": not has_measured_data,
        "estimation_methodology": (
            "Live task execution telemetry from AgentEvent records."
            if has_measured_data
            else (
                "Modelled reference estimate based on token character heuristics, assumed "
                "model price ($0.003 / 1k tokens), and typical agent context retrieval profiles. "
                "No live agent task executions recorded yet for this project."
            )
        ),
        "savings_pct": savings_pct,
        "avg_context_tokens_cortex": cortex_avg_tokens,
        "avg_context_tokens_baseline": baseline_avg_tokens,
        "tokens_reduction_pct": savings_pct,
        "modelled_files_explored_cortex": files_explored_cortex,
        "files_explored_cortex": files_explored_cortex,
        "files_explored_baseline": files_explored_base,
        "files_reduction_pct": files_reduction_pct,
        "modelled_tool_calls_cortex": tool_calls_cortex,
        "tool_calls_cortex": tool_calls_cortex,
        "tool_calls_baseline": tool_calls_base,
        "tool_calls_reduction_pct": tool_calls_reduction_pct,
        "cost_per_1k_cortex": cost_per_1k_cortex,
        "cost_per_1k_baseline": cost_per_1k_base,
        "cost_saved_per_1k": round(cost_per_1k_base - cost_per_1k_cortex, 2),
        "profiles": profiles,
    }

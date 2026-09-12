"""Central authorization policy and permission model for CortexForge.

Hierarchy:
User → Projects → AI Agents → Project Data

This module defines:
1. Granular permissions (Permission enum)
2. Roles (OWNER, ADMIN, MEMBER, VIEWER)
3. Role-to-permission mappings
4. ResourceScope abstraction (enabling future organization tenancy without cognition engine rewrites)
5. Central permission evaluation functions and FastAPI dependencies
"""

from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, status


class Permission(str, Enum):
    """Central permission definitions for CortexForge."""

    PROJECT_READ = "project.read"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"
    PROJECT_SCAN = "project.scan"
    PROJECT_MEMBERS_MANAGE = "project.members.manage"

    MEMORY_READ = "memory.read"
    MEMORY_CREATE = "memory.create"
    MEMORY_UPDATE = "memory.update"
    MEMORY_DELETE = "memory.delete"
    MEMORY_VERIFY = "memory.verify"

    ARCHITECTURE_READ = "architecture.read"
    ARCHITECTURE_WRITE = "architecture.write"

    GRAPH_READ = "graph.read"

    SNAPSHOT_READ = "snapshot.read"
    SNAPSHOT_CREATE = "snapshot.create"

    JOB_READ = "job.read"
    JOB_CREATE = "job.create"

    AGENT_MANAGE = "agent.manage"
    WEBHOOK_MANAGE = "webhook.manage"
    SETTINGS_MANAGE = "settings.manage"


class ProjectRole(str, Enum):
    """Explicit project membership roles."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


# Role to permissions mapping
ROLE_PERMISSIONS: dict[ProjectRole, set[Permission]] = {
    ProjectRole.OWNER: set(Permission),  # All permissions
    ProjectRole.ADMIN: {
        # Admins can do everything except delete the project or transfer ownership
        Permission.PROJECT_READ,
        Permission.PROJECT_UPDATE,
        Permission.PROJECT_SCAN,
        Permission.MEMORY_READ,
        Permission.MEMORY_CREATE,
        Permission.MEMORY_UPDATE,
        Permission.MEMORY_DELETE,
        Permission.MEMORY_VERIFY,
        Permission.ARCHITECTURE_READ,
        Permission.ARCHITECTURE_WRITE,
        Permission.GRAPH_READ,
        Permission.SNAPSHOT_READ,
        Permission.SNAPSHOT_CREATE,
        Permission.JOB_READ,
        Permission.JOB_CREATE,
        Permission.AGENT_MANAGE,
        Permission.WEBHOOK_MANAGE,
        Permission.SETTINGS_MANAGE,
        Permission.PROJECT_MEMBERS_MANAGE,
    },
    ProjectRole.MEMBER: {
        Permission.PROJECT_READ,
        Permission.PROJECT_SCAN,
        Permission.MEMORY_READ,
        Permission.MEMORY_CREATE,
        Permission.MEMORY_UPDATE,
        Permission.ARCHITECTURE_READ,
        Permission.ARCHITECTURE_WRITE,
        Permission.GRAPH_READ,
        Permission.SNAPSHOT_READ,
        Permission.SNAPSHOT_CREATE,
        Permission.JOB_READ,
        Permission.JOB_CREATE,
    },
    ProjectRole.VIEWER: {
        Permission.PROJECT_READ,
        Permission.MEMORY_READ,
        Permission.ARCHITECTURE_READ,
        Permission.GRAPH_READ,
        Permission.SNAPSHOT_READ,
        Permission.JOB_READ,
    },
}

# Scope mapping for AI Agents and MCP Bearer Tokens (least-privilege scopes to permissions)
AGENT_SCOPE_TO_PERMISSIONS: dict[str, set[Permission]] = {
    "read": {
        Permission.PROJECT_READ,
        Permission.MEMORY_READ,
        Permission.ARCHITECTURE_READ,
        Permission.GRAPH_READ,
        Permission.SNAPSHOT_READ,
        Permission.JOB_READ,
    },
    "write": {
        Permission.PROJECT_READ,
        Permission.MEMORY_READ,
        Permission.MEMORY_CREATE,
        Permission.MEMORY_UPDATE,
        Permission.ARCHITECTURE_READ,
        Permission.ARCHITECTURE_WRITE,
        Permission.GRAPH_READ,
        Permission.SNAPSHOT_READ,
        Permission.SNAPSHOT_CREATE,
        Permission.JOB_READ,
        Permission.JOB_CREATE,
    },
    "project:read": {Permission.PROJECT_READ},
    "project:scan": {Permission.PROJECT_SCAN, Permission.JOB_CREATE},
    "context:read": {
        Permission.PROJECT_READ,
        Permission.MEMORY_READ,
        Permission.ARCHITECTURE_READ,
        Permission.GRAPH_READ,
    },
    "code:read": {Permission.PROJECT_READ, Permission.GRAPH_READ},
    "cognition:write": {
        Permission.MEMORY_CREATE,
        Permission.MEMORY_UPDATE,
        Permission.ARCHITECTURE_WRITE,
    },
    "memory:read": {Permission.MEMORY_READ},
    "memory:write": {
        Permission.MEMORY_READ,
        Permission.MEMORY_CREATE,
        Permission.MEMORY_UPDATE,
    },
    "memory:verify": {Permission.MEMORY_VERIFY},
    "graph:read": {Permission.GRAPH_READ},
    "scan": {Permission.PROJECT_SCAN, Permission.JOB_CREATE},
    "scan:read": {Permission.PROJECT_READ, Permission.JOB_READ},
    "scan:trigger": {Permission.PROJECT_SCAN, Permission.JOB_CREATE},
    "snapshots:read": {Permission.SNAPSHOT_READ},
    "snapshots:create": {Permission.SNAPSHOT_CREATE},
}


@dataclass(frozen=True)
class ResourceScope:
    """Abstract resource-owner boundary.

    Enables adding Organization / Workspace / Team layers in the future
    without rewriting cognition services or database querying logic.
    """

    user_id: str | None = None
    project_id: str | None = None
    organization_id: str | None = None  # Reserved for future tenancy

    def matches_project(self, target_project_id: str) -> bool:
        """Check if this scope encompasses the target project."""
        if not self.project_id:
            return False
        return self.project_id == target_project_id


def evaluate_agent_permission(
    scopes: list[str], required_permission: Permission
) -> bool:
    """Evaluate whether an agent's assigned scopes satisfy the required permission."""
    for scope in scopes:
        scope_clean = scope.strip()
        if scope_clean == "*":
            return True
        if scope_clean == required_permission.value:
            return True
        mapped_perms = AGENT_SCOPE_TO_PERMISSIONS.get(scope_clean, set())
        if required_permission in mapped_perms:
            return True
    return False


def evaluate_role_permission(
    role: str | ProjectRole, required_permission: Permission
) -> bool:
    """Evaluate whether a role grants the required permission."""
    try:
        r = ProjectRole(role) if isinstance(role, str) else role
    except ValueError:
        return False
    return required_permission in ROLE_PERMISSIONS.get(r, set())


def check_project_permission(
    role: str | ProjectRole | None,
    required_permission: Permission,
    is_admin: bool = False,
    is_owner: bool = False,
) -> bool:
    """Check if the caller has the required permission for the project.

    Admins and Project Owners have full access.
    Otherwise, checks the assigned role against ROLE_PERMISSIONS.
    """
    if is_admin:
        return True
    if is_owner:
        return True
    if not role:
        return False
    return evaluate_role_permission(role, required_permission)


def enforce_project_permission(
    role: str | ProjectRole | None,
    required_permission: Permission,
    is_admin: bool = False,
    is_owner: bool = False,
    project_id: str = "",
) -> None:
    """Enforce permission, raising HTTP 403 Forbidden if denied."""
    if not check_project_permission(
        role=role,
        required_permission=required_permission,
        is_admin=is_admin,
        is_owner=is_owner,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{required_permission.value}' denied for project '{project_id}'.",
        )

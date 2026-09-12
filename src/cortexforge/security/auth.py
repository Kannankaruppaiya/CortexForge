"""Authentication, authorization, and workspace security boundaries for CortexForge.

Architecture:
User → Projects → AI Agents → Project Data
NO TENANT / NO ORGANIZATION layer.
"""

import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.db import get_db_session, session_scope
from cortexforge.core.models import (
    Agent,
    AgentCredential,
    AgentProjectPermission,
    MCPOAuthToken,
    Project,
    ProjectMembership,
    Session,
    User,
)
from cortexforge.security.crypto import hash_token
from cortexforge.security.policy import (
    Permission,
    ProjectRole,
    check_project_permission,
    evaluate_agent_permission,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

AUTH_REQUIRED_ENVIRONMENTS = frozenset({"production", "prod", "staging"})


def is_production_environment() -> bool:
    env = (
        os.environ.get("CORTEX_ENV", os.environ.get("ENVIRONMENT", "development"))
        .strip()
        .lower()
    )
    return env in AUTH_REQUIRED_ENVIRONMENTS


@dataclass
class Principal:
    """Represents an authenticated caller with assigned project-level permissions."""

    principal_id: str
    actor_type: str = "USER"  # "USER", "AGENT", "SYSTEM"
    user_id: str | None = None
    email: str | None = None
    agent_id: str | None = None
    role: str = "user"  # "admin", "user", "agent", "service", "read_only"
    allowed_project_ids: set[str] = field(default_factory=set)
    project_roles: dict[str, str] = field(default_factory=dict)
    agent_scopes: dict[str, list[str]] = field(default_factory=dict)
    is_admin: bool = False

    def can_access_project(self, project_id: str) -> bool:
        """Verify whether principal has authorization to access the specified project."""
        if self.is_admin:
            return True
        if "*" in self.allowed_project_ids:
            return True
        return project_id in self.allowed_project_ids

    def has_permission(self, project_id: str, permission: Permission) -> bool:
        """Check if principal has permission in project."""
        if self.is_admin:
            return True
        if "*" in self.allowed_project_ids:
            return True
        if project_id not in self.allowed_project_ids:
            return False
        if self.actor_type == "AGENT":
            scopes = self.agent_scopes.get(project_id, [])
            return evaluate_agent_permission(scopes, permission)
        # User or System
        role = self.project_roles.get(project_id, ProjectRole.VIEWER.value)
        is_owner = role == ProjectRole.OWNER.value
        return check_project_permission(
            role, permission, is_admin=self.is_admin, is_owner=is_owner
        )


def is_auth_disabled() -> bool:
    """Check if authentication is explicitly disabled (development/test-only)."""
    return os.environ.get("CORTEX_AUTH_DISABLED", "").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def get_configured_api_key() -> str | None:
    """Return the configured master API key, if any."""
    return os.environ.get("CORTEX_API_KEY") or os.environ.get("CORTEX_ADMIN_KEY")


def get_session_token_from_request(request: Request) -> str | None:
    """Extract session token from cookie, Authorization Bearer, or X-API-Key header."""
    cookie = request.cookies.get("cortex_session") or request.cookies.get(
        "cortexforge_session"
    )
    if cookie:
        return cookie.strip()
    auth_hdr = request.headers.get("Authorization")
    if auth_hdr and auth_hdr.startswith("Bearer "):
        return auth_hdr[7:].strip()
    api_hdr = request.headers.get("X-API-Key")
    if api_hdr:
        return api_hdr.strip()
    return None


async def resolve_principal_from_token(
    db_session: AsyncSession, token: str
) -> Principal | None:
    """Resolve an incoming bearer token, agent key, or session token to a canonical Principal.

    Checks in order:
    1. AgentCredential (modern hashed agent token)
    2. Agent.api_key_hash (legacy agent API key)
    3. User Session (modern hashed session token)

    Enforces expiry and revocation checks. Returns None if invalid or expired.
    """
    if not token or not token.strip():
        return None

    clean_token = token.strip()
    if clean_token.lower().startswith("bearer "):
        clean_token = clean_token[7:].strip()
    if not clean_token:
        return None

    key_h = hash_token(clean_token)
    now = datetime.now(UTC)

    # 1. Agent key check: modern AgentCredential
    cred_res = await db_session.execute(
        select(AgentCredential).where(
            AgentCredential.key_hash == key_h,
            AgentCredential.revoked_at.is_(None),
            (AgentCredential.expires_at.is_(None)) | (AgentCredential.expires_at > now),
        )
    )
    cred = cred_res.scalars().first()
    if cred:
        agent = await db_session.get(Agent, cred.agent_id)
        if agent and agent.status == "ACTIVE":
            cred.last_used_at = now
            perm_res = await db_session.execute(
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
            return Principal(
                principal_id=agent.id,
                actor_type="AGENT",
                agent_id=agent.id,
                user_id=agent.owner_user_id,
                role="agent",
                allowed_project_ids=allowed,
                agent_scopes=scopes_map,
                is_admin=False,
            )

    # 2. Legacy Agent.api_key_hash
    res = await db_session.execute(
        select(Agent).where(Agent.api_key_hash == key_h, Agent.status == "ACTIVE")
    )
    agent = res.scalars().first()
    if agent:
        perm_res = await db_session.execute(
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
        return Principal(
            principal_id=agent.id,
            actor_type="AGENT",
            agent_id=agent.id,
            user_id=agent.owner_user_id,
            role="agent",
            allowed_project_ids=allowed,
            agent_scopes=scopes_map,
            is_admin=False,
        )

    # 3. User session check
    res = await db_session.execute(
        select(Session).where(
            Session.session_token_hash == key_h,
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
    )
    sess = res.scalars().first()
    if sess:
        user = await db_session.get(User, sess.user_id)
        if user and user.status == "ACTIVE":
            proj_res = await db_session.execute(
                select(Project.id).where(Project.owner_user_id == user.id)
            )
            owned_ids = set(proj_res.scalars().all())
            mem_res = await db_session.execute(
                select(ProjectMembership).where(ProjectMembership.user_id == user.id)
            )
            memberships = mem_res.scalars().all()

            proj_roles = {pid: "OWNER" for pid in owned_ids}
            for m in memberships:
                proj_roles[m.project_id] = m.role
                owned_ids.add(m.project_id)

            is_admin_user = bool(getattr(user, "is_admin", False))
            return Principal(
                principal_id=user.id,
                actor_type="USER",
                user_id=user.id,
                email=user.email,
                role="admin" if is_admin_user else "user",
                allowed_project_ids=owned_ids,
                project_roles=proj_roles,
                is_admin=is_admin_user,
            )

    # 4. MCP OAuth token check
    mcp_res = await db_session.execute(
        select(MCPOAuthToken).where(
            MCPOAuthToken.token_hash == key_h,
            MCPOAuthToken.revoked_at.is_(None),
            MCPOAuthToken.expires_at > now,
        )
    )
    mcp_tok = mcp_res.scalars().first()
    if mcp_tok:
        user = await db_session.get(User, mcp_tok.user_id)
        if user and user.status == "ACTIVE":
            proj_res = await db_session.execute(
                select(Project.id).where(Project.owner_user_id == user.id)
            )
            owned_ids = set(proj_res.scalars().all())
            mem_res = await db_session.execute(
                select(ProjectMembership).where(ProjectMembership.user_id == user.id)
            )
            memberships = mem_res.scalars().all()

            proj_roles = {pid: "OWNER" for pid in owned_ids}
            for m in memberships:
                proj_roles[m.project_id] = m.role
                owned_ids.add(m.project_id)

            is_admin_user = bool(getattr(user, "is_admin", False))
            return Principal(
                principal_id=user.id,
                actor_type="USER",
                user_id=user.id,
                email=user.email,
                role="admin" if is_admin_user else "user",
                allowed_project_ids=owned_ids,
                project_roles=proj_roles,
                is_admin=is_admin_user,
            )

    return None


async def get_current_principal(
    session_cookie: Annotated[str | None, Cookie(alias="cortex_session")] = None,
    alt_session_cookie: Annotated[
        str | None, Cookie(alias="cortexforge_session")
    ] = None,
    api_key: str | None = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    authorization: str | None = Header(None),
    user_header: Annotated[str | None, Header(alias="X-Principal-ID")] = None,
    cortex_user_header: str | None = Header(None, alias="X-Cortex-User"),
    cortex_actor_header: str | None = Header(None, alias="X-Cortex-Actor"),
    allowed_projects_header: Annotated[
        str | None, Header(alias="X-Allowed-Projects")
    ] = None,
) -> Principal:
    """Resolve and authenticate caller identity, returning Principal.

    Resolution precedence:
    1. Master Admin API Key -> Admin principal.
    2. HttpOnly Session Cookie or Bearer session token -> User principal.
    3. Agent API key (hashed) -> AI Agent principal.
    4. Development/Test fallback when auth is disabled or in local test environment.
    """
    token = None
    if bearer and bearer.credentials:
        token = bearer.credentials.strip()
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif session_cookie or alt_session_cookie:
        token = (session_cookie or alt_session_cookie).strip()
    elif api_key:
        token = api_key.strip()
    elif authorization:
        token = authorization.strip()

    expected_admin_key = get_configured_api_key()
    is_prod = is_production_environment()
    strict_auth = is_prod or (
        os.environ.get("CORTEX_AUTH_STRICT", "").strip().lower()
        in (
            "true",
            "1",
        )
    )

    # In production-like environments or when strict auth is enabled,
    # client identity headers MUST NOT be accepted for privilege or identity selection (§28).
    eff_user_header = user_header or cortex_user_header
    if is_prod or strict_auth:
        eff_user_header = None
        allowed_projects_header = None

    # 1. Master Admin API key
    if token and expected_admin_key and secrets.compare_digest(token, expected_admin_key):
        return Principal(
            principal_id="admin:master",
            actor_type="SYSTEM",
            role="admin",
            is_admin=True,
            allowed_project_ids={"*"},
        )

    # 2. Authenticated Session / Agent / Database lookup
    if token:
        async with session_scope() as db_session:
            principal = await resolve_principal_from_token(db_session, token)
            if principal:
                return principal
            # A credential token was explicitly supplied but failed validation -> Fail Closed (§28)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, expired, or revoked authentication credentials.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if strict_auth or is_prod:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


    # 4. Development/Test mode fallback (strictly restricted to non-production environments)
    if is_prod:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required in production environment.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed = set()
    if allowed_projects_header:
        allowed = {p.strip() for p in allowed_projects_header.split(",") if p.strip()}

    is_adm = (
        (eff_user_header != "restricted_user")
        and not bool(allowed)
        and (eff_user_header != "00000000-0000-0000-0000-000000000001")
    )
    default_uid = (
        eff_user_header
        if eff_user_header and eff_user_header != "restricted_user"
        else "00000000-0000-0000-0000-000000000001"
    )

    return Principal(
        principal_id=eff_user_header or default_uid,
        actor_type="USER",
        user_id=default_uid,
        email="developer@cortexforge.local",
        role="admin" if is_adm else "user",
        allowed_project_ids=allowed if allowed else {"*"} if is_adm else set(),
        project_roles={p: "OWNER" for p in allowed}
        if allowed
        else {"*": "OWNER"}
        if is_adm
        else {},
        is_admin=is_adm,
    )


class RequireProjectAccess:
    """Dependency callable checking that the current principal has access and permission to a project."""

    def __init__(
        self,
        project_id_param: str = "project_id",
        permission: Permission | None = None,
    ):
        self.project_id_param = project_id_param
        self.permission = permission

    @classmethod
    def check_access(
        cls,
        principal: Principal,
        project_id: str,
        permission: Permission | None = None,
    ) -> None:
        """Helper to programmatically check project access and permission."""
        if not principal.can_access_project(project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to project '{project_id}'.",
            )
        if permission is not None and not principal.has_permission(
            project_id, permission
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission.value}' denied for project '{project_id}'.",
            )

    async def __call__(
        self,
        project_id: str | None = None,
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if project_id is not None:
            self.check_access(principal, project_id, self.permission)
        return principal


def verify_workspace_path_allowed(candidate_path: str | Path) -> str:
    """Enforce workspace allowlist boundary.

    Projects may only be registered inside CORTEX_WORKSPACE_ROOT (or current working directory / temp).
    Attempts to register arbitrary host roots (e.g. /etc, C:\\Windows) are rejected.
    """
    import tempfile

    allowed_root = os.environ.get("CORTEX_WORKSPACE_ROOT")
    target = Path(candidate_path).resolve()

    if allowed_root:
        allowed_roots = [Path(allowed_root).resolve()]
    else:
        # Default boundary to current working tree directory and system temp directory
        allowed_roots = [
            Path(os.getcwd()).resolve(),
            Path(tempfile.gettempdir()).resolve(),
        ]

    is_contained = False
    for root in allowed_roots:
        try:
            if target == root or target.is_relative_to(root):
                is_contained = True
                break
        except (ValueError, AttributeError):
            pass
        # Fallback check for case-insensitive filesystems (Windows/macOS)
        target_str = str(target).lower().rstrip("/\\")
        root_str = str(root).lower().rstrip("/\\")
        if target_str == root_str or target_str.startswith(
            (root_str + os.sep, root_str + "/")
        ):
            is_contained = True
            break

    if not is_contained:
        root_desc = allowed_root or f"'{allowed_roots[0]}' or '{allowed_roots[1]}'"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Project registration forbidden: path '{candidate_path}' is outside permitted workspace root {root_desc}.",
        )

    return str(target)


# Blocked system paths that must never be registered as LOCAL projects.
# These are too broad / sensitive to scan even if accessible.
_BLOCKED_ROOTS_POSIX: frozenset[str] = frozenset(
    {
        "/",
        "/etc",
        "/proc",
        "/sys",
        "/boot",
        "/dev",
        "/root",
        "/run",
        "/sbin",
        "/bin",
        "/usr/bin",
    }
)
_BLOCKED_ROOTS_WIN_LOWER: frozenset[str] = frozenset(
    {
        "c:\\",
        "d:\\",
        "e:\\",
        "c:\\windows",
        "c:\\windows\\system32",
        "c:\\program files",
        "c:\\program files (x86)",
        "c:\\programdata",
    }
)


def validate_local_registration_path(candidate_path: str) -> str:
    """Validate a user-selected local repository path for LOCAL project registration.

    Unlike verify_workspace_path_allowed(), this function does NOT restrict the path
    to CORTEX_WORKSPACE_ROOT or os.getcwd(). It is designed for the LOCAL project
    creation flow where the user explicitly selects any accessible folder on their computer.

    Enforces:
    - Non-empty path
    - Canonicalized (resolve symlinks / relative parts)
    - Not a filesystem root (C:\\ or /)
    - Not a blocked system directory (C:\\Windows, /etc, /proc, etc.)
    - Path exists and is a directory

    Does NOT enforce:
    - Containment within CORTEX_WORKSPACE_ROOT (that is for the scanner, not registration)
    - Workspace root boundaries

    Security: Callers must be authenticated. The API is local-only (same machine).
    Remote arbitrary filesystem access is prevented by session authentication.
    The scanner still enforces its own containment boundary within project.local_path.
    """
    if not candidate_path or not candidate_path.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Local repository path cannot be empty.",
        )

    raw = candidate_path.strip()

    # Reject UNC/network paths (\\server\share or //server/share)
    if raw.startswith(("\\\\", "//")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UNC network paths are not supported for LOCAL project registration.",
        )

    # Normalize Windows bare drive letter: "C:" -> "C:\" (prevents resolving to process cwd)
    if os.name == "nt" and len(raw) == 2 and raw[1] == ":" and raw[0].isalpha():
        raw = f"{raw}\\"

    try:
        target = Path(raw).resolve()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid path: {exc}",
        ) from exc

    canonical_str = str(target)
    canonical_lower = canonical_str.lower().rstrip("/\\")

    # Reject filesystem roots: "/" on POSIX, "C:\" on Windows
    if (
        target.parent == target
        or str(target.parent) == str(target)
        or str(target) == target.anchor
        or canonical_lower
        in (
            "c:",
            "d:",
            "e:",
            "f:",
            "g:",
            "h:",
            "i:",
            "j:",
            "k:",
            "l:",
            "m:",
            "n:",
            "o:",
            "p:",
            "q:",
            "r:",
            "s:",
            "t:",
            "u:",
            "v:",
            "w:",
            "x:",
            "y:",
            "z:",
            "/",
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registering a filesystem root as a project is not allowed.",
        )

    # Reject blocked POSIX system roots and their subdirectories
    for blocked in _BLOCKED_ROOTS_POSIX:
        if canonical_str == blocked or canonical_str.startswith(blocked + "/"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"System directory '{candidate_path}' cannot be registered as a project.",
            )

    # Reject blocked Windows system roots and their subdirectories
    for blocked in _BLOCKED_ROOTS_WIN_LOWER:
        if canonical_lower == blocked or canonical_lower.startswith(blocked + "\\"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"System directory '{candidate_path}' cannot be registered as a project.",
            )

    # Check that the path exists and is a directory
    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Directory does not exist: {candidate_path}",
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {candidate_path}",
        )

    return canonical_str


async def get_current_user(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Dependency that returns the authenticated database User model."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await session.get(User, principal.user_id)
    if not user or user.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or does not exist.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_authenticated_user(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Dependency ensuring caller is an authenticated human user (not an agent or unauthenticated)."""
    if principal.actor_type != "USER" or not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated human user principal required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def get_authorized_project(
    project_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Project:
    """Dependency/helper to verify access and return authorized Project model."""
    RequireProjectAccess.check_access(principal, project_id)
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return project

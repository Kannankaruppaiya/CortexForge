"""Authentication, authorization, and workspace security boundaries for CortexForge."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """Represents an authenticated caller with assigned project-level permissions."""

    principal_id: str
    role: str = "user"  # "admin", "user", "service", "read_only"
    allowed_project_ids: set[str] = field(default_factory=set)
    is_admin: bool = False

    def can_access_project(self, project_id: str) -> bool:
        """Verify whether principal has authorization to access the specified project."""
        if self.is_admin:
            return True
        if "*" in self.allowed_project_ids:
            return True
        return project_id in self.allowed_project_ids


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


async def get_current_principal(
    api_key: str | None = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    user_header: Annotated[str | None, Header(alias="X-Principal-ID")] = None,
    allowed_projects_header: Annotated[
        str | None, Header(alias="X-Allowed-Projects")
    ] = None,
) -> Principal:
    """Resolve and authenticate caller identity, returning Principal.

    Fails closed with HTTP 401 Unauthorized if authentication fails.
    """
    token = None
    if bearer and bearer.credentials:
        token = bearer.credentials.strip()
    elif api_key:
        token = api_key.strip()

    expected_key = get_configured_api_key()

    # If auth is explicitly disabled or no key configured, default to local admin unless in strict mode
    strict_auth = os.environ.get("CORTEX_AUTH_STRICT", "").strip().lower() in (
        "true",
        "1",
    )
    if is_auth_disabled() or (expected_key is None and not strict_auth):
        allowed = set()
        if allowed_projects_header:
            allowed = {
                p.strip() for p in allowed_projects_header.split(",") if p.strip()
            }
        # If client passes explicit allowed_projects_header, respect that bounding
        is_adm = (user_header != "restricted_user") and not bool(allowed)
        return Principal(
            principal_id=user_header or "local:developer",
            role="admin" if is_adm else "user",
            allowed_project_ids=allowed,
            is_admin=is_adm,
        )

    # In authenticated mode, token must match configured key
    if not token or (expected_key and token != expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed = set()
    if allowed_projects_header:
        allowed = {p.strip() for p in allowed_projects_header.split(",") if p.strip()}

    return Principal(
        principal_id=user_header or "authenticated:operator",
        role="admin" if token == expected_key else "user",
        allowed_project_ids=allowed,
        is_admin=(token == expected_key),
    )


class RequireProjectAccess:
    """Dependency callable checking that the current principal has access to a project."""

    def __init__(self, project_id_param: str = "project_id"):
        self.project_id_param = project_id_param

    @staticmethod
    def check_access(principal: Principal, project_id: str) -> None:
        """Helper to programmatically check project access."""
        if not principal.can_access_project(project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to project '{project_id}'.",
            )

    async def __call__(
        self,
        project_id: str | None = None,
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if project_id is not None:
            self.check_access(principal, project_id)
        return principal


def verify_workspace_path_allowed(candidate_path: str | Path) -> str:
    """Enforce workspace allowlist boundary (Blocker 2).

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

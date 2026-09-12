"""Managed workspace service for GitHub and Git URL projects (§14, §16).

All cloned projects are contained strictly within CORTEX_MANAGED_ROOT:
    <managed_root>/<user_id>/<project_id>/repo

Arbitrary clone destinations and directory escapes are strictly forbidden.
"""

import os
import shutil
from pathlib import Path

from cortexforge.security.path_safety import PathSecurity, PathSecurityError


class ManagedWorkspaceService:
    """Manages isolated workspace directories for remote/cloned projects."""

    def __init__(self, managed_root: str | Path | None = None) -> None:
        if managed_root is None:
            env_root = os.environ.get("CORTEX_MANAGED_ROOT")
            if env_root:
                self.root = Path(env_root).resolve()
            else:
                self.root = (Path(os.getcwd()) / ".cortex_workspaces").resolve()
        else:
            self.root = Path(managed_root).resolve()

        self.root.mkdir(parents=True, exist_ok=True)

    def get_project_workspace_path(self, user_id: str, project_id: str) -> Path:
        """Compute the canonical, isolated workspace directory for a project.

        Validates against directory traversal and returns the 'repo' directory.
        """
        if not user_id or not project_id:
            raise PathSecurityError("user_id and project_id cannot be empty.")

        # Reject path separators and traversal attempts explicitly
        if any(c in user_id for c in ("/\\:")) or ".." in user_id:
            raise PathSecurityError(
                f"Directory traversal detected in user_id: '{user_id}'"
            )
        if any(c in project_id for c in ("/\\:")) or ".." in project_id:
            raise PathSecurityError(
                f"Directory traversal detected in project_id: '{project_id}'"
            )

        clean_user = "".join(
            c for c in user_id if c.isalnum() or c in ("-", "_")
        ).strip()
        clean_proj = "".join(
            c for c in project_id if c.isalnum() or c in ("-", "_")
        ).strip()

        if (
            not clean_user
            or not clean_proj
            or clean_user != user_id
            or clean_proj != project_id
        ):
            raise PathSecurityError(
                f"Invalid user_id '{user_id}' or project_id '{project_id}' for workspace path."
            )

        subpath = os.path.join(clean_user, clean_proj, "repo")
        resolved_str = PathSecurity.safe_resolve(self.root, subpath)
        resolved_path = Path(resolved_str)
        return resolved_path

    def prepare_project_workspace(self, user_id: str, project_id: str) -> Path:
        """Create and ensure empty project workspace directory."""
        workspace_path = self.get_project_workspace_path(user_id, project_id)
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)
        workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path

    def cleanup_project_workspace(self, user_id: str, project_id: str) -> bool:
        """Safely delete project workspace directory within managed root."""
        try:
            workspace_path = self.get_project_workspace_path(user_id, project_id)
            project_dir = workspace_path.parent  # <managed_root>/<user_id>/<project_id>
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
                return True
            return False
        except Exception:
            return False


_default_workspace_service = ManagedWorkspaceService()


def get_managed_workspace_service() -> ManagedWorkspaceService:
    return _default_workspace_service

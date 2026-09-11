"""Local CortexForge Bridge client for Cloud-Hosted Mode (§13).

Architecture:
    CortexForge Cloud (Hosted)
            |
            | Authenticated via scoped project token / API key
            | References project_id only (zero host filesystem access)
            v
    Local CortexForge Bridge (Developer Machine)
            |
            +-- Local Filesystem (validated by WorkspacePolicy / PathSecurity)
            +-- Local Git Repository
            +-- Local stdio MCP
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortexforge.security.path_safety import (
    SafeFileReader,
    WorkspacePolicy,
)


@dataclass
class LocalBridgeConfig:
    """Configuration for connecting local workspace to CortexForge cloud."""

    server_url: str
    auth_token: str
    project_id: str
    local_path: str
    policy: WorkspacePolicy | None = None

    def __post_init__(self):
        self.server_url = self.server_url.rstrip("/")
        if not self.policy:
            self.policy = WorkspacePolicy.default()


class LocalBridgeClient:
    """Secure bridge running on local machine, mediating access between cloud and local repo."""

    def __init__(self, config: LocalBridgeConfig):
        self.config = config
        self.canonical_root = Path(config.local_path).resolve()
        self.file_reader = SafeFileReader(policy=config.policy)

        if not self.canonical_root.is_dir():
            raise ValueError(
                f"Local bridge repository path is not a directory: '{config.local_path}'"
            )

    @property
    def project_id(self) -> str:
        """Cloud references this project strictly by ID."""
        return self.config.project_id

    def read_local_file(self, relative_path: str) -> str:
        """Safely read file within local repository boundary."""
        return self.file_reader.read_text(self.canonical_root, relative_path)

    def get_git_head_commit(self) -> str | None:
        """Get current HEAD commit sha from local repository."""
        git_dir = self.canonical_root / ".git"
        if not git_dir.exists():
            return None
        try:
            head_file = git_dir / "HEAD"
            if not head_file.is_file():
                return None
            ref = head_file.read_text(encoding="utf-8").strip()
            if ref.startswith("ref: "):
                ref_path = git_dir / ref[5:]
                if ref_path.is_file():
                    return ref_path.read_text(encoding="utf-8").strip()
            return ref if len(ref) == 40 else None
        except Exception:
            return None

    def get_headers(self) -> dict[str, str]:
        """Generate authenticated headers for communicating with CortexForge Cloud."""
        token = self.config.auth_token.strip()
        if token.startswith("cortex_agent_"):
            return {"X-API-Key": token}
        return {"Authorization": f"Bearer {token}"}

    def get_bridge_status(self) -> dict[str, Any]:
        """Summarize bridge health and local repository synchronization status."""
        return {
            "status": "CONNECTED",
            "project_id": self.config.project_id,
            "server_url": self.config.server_url,
            "local_root_canonical": str(self.canonical_root),
            "head_commit": self.get_git_head_commit(),
        }

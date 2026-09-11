"""Repository source adapter abstraction.

Decouples repository scanning, AST parsing, and code intelligence from hardcoded
local filesystem paths, supporting:
- LocalRepositorySource (direct local filesystem for local mode)
- LocalBridgeRepositorySource (Local CortexForge Bridge for hosted cloud mode)
- GitHubRepositorySource (GitHub API / Git remote tree for cloud-connected GitHub repos)
"""

import os
from abc import ABC, abstractmethod

from cortexforge.bridge.client import LocalBridgeClient, LocalBridgeConfig
from cortexforge.code_intelligence.git_provider import GitDiffFile, GitProvider
from cortexforge.core.models import Project
from cortexforge.security.path_safety import (
    PathSecurity,
    SafeFileReader,
    WorkspacePolicy,
)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB per file
MAX_TOTAL_SCAN_BYTES = 100 * 1024 * 1024  # 100 MB total per scan
MAX_ALLOWED_FILES = 20_000

DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "dist",
        "build",
        ".idea",
        ".vscode",
        ".gemini",
        "coverage",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

DEFAULT_IGNORED_EXTS = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".pyc",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
    }
)


class RepositorySource(ABC):
    """Abstract interface for accessing project repository files and Git history."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Type identifier: 'LOCAL', 'BRIDGE', or 'GITHUB'."""

    @abstractmethod
    def is_accessible(self) -> bool:
        """Check if source repository is reachable and ready for operations."""

    @abstractmethod
    def get_head_commit(self) -> str | None:
        """Return the current 40-character Git HEAD commit SHA."""

    @abstractmethod
    def get_current_branch(self) -> str | None:
        """Return current branch name, or None if detached."""

    @abstractmethod
    def discover_files(self, max_files: int | None = None) -> list[str]:
        """Discover source files relative to repository root."""

    @abstractmethod
    def read_bytes(self, relative_path: str) -> bytes:
        """Safely read binary content of a relative file."""

    @abstractmethod
    def read_text(self, relative_path: str) -> str:
        """Safely read UTF-8 text content of a relative file."""

    @abstractmethod
    def get_modified_files(
        self, base_commit: str, target_commit: str = "HEAD"
    ) -> list[GitDiffFile]:
        """List modified, added, and deleted files between two commits."""

    @abstractmethod
    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        """Whether one commit is an ancestor of another in the repository graph."""


class LocalRepositorySource(RepositorySource):
    """Direct local filesystem repository source (used when running locally)."""

    def __init__(self, root_path: str, policy: WorkspacePolicy | None = None) -> None:
        self.root_path = os.path.realpath(root_path)
        self.policy = policy or WorkspacePolicy.default()
        self.safe_reader = SafeFileReader(policy=self.policy)
        self.git = GitProvider(self.root_path)

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        return self.git.is_ancestor(maybe_ancestor, descendant)

    @property
    def source_type(self) -> str:
        return "LOCAL"

    def is_accessible(self) -> bool:
        return os.path.isdir(self.root_path)

    def get_head_commit(self) -> str | None:
        return self.git.get_head_commit()

    def get_current_branch(self) -> str | None:
        return self.git.get_current_branch()

    def discover_files(self, max_files: int | None = None) -> list[str]:
        if not self.is_accessible():
            return []

        matched_files: list[str] = []
        effective_max = min(max_files or MAX_ALLOWED_FILES, MAX_ALLOWED_FILES)
        accumulated_bytes = 0

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in DEFAULT_IGNORED_DIRS and not d.startswith(".")
            ]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in DEFAULT_IGNORED_EXTS:
                    continue

                full_path = os.path.join(dirpath, fname)
                try:
                    fsize = os.path.getsize(full_path)
                except OSError:
                    continue

                if fsize > MAX_FILE_SIZE_BYTES:
                    continue
                if accumulated_bytes + fsize > MAX_TOTAL_SCAN_BYTES:
                    return matched_files
                accumulated_bytes += fsize

                rel_path = os.path.relpath(full_path, self.root_path).replace("\\", "/")
                if not PathSecurity.is_safe_subpath(self.root_path, rel_path):
                    continue
                matched_files.append(rel_path)

                if len(matched_files) >= effective_max:
                    return matched_files

        return matched_files

    def read_bytes(self, relative_path: str) -> bytes:
        return self.safe_reader.read_bytes(self.root_path, relative_path)

    def read_text(self, relative_path: str) -> str:
        return self.safe_reader.read_text(self.root_path, relative_path)

    def get_modified_files(
        self, base_commit: str, target_commit: str = "HEAD"
    ) -> list[GitDiffFile]:
        return self.git.get_modified_files(base_commit, target_commit)


class LocalBridgeRepositorySource(RepositorySource):
    """Mediates local filesystem access for hosted/cloud CortexForge via the Local Bridge."""

    def __init__(self, bridge_client: LocalBridgeClient) -> None:
        self.bridge = bridge_client

    @property
    def source_type(self) -> str:
        return "BRIDGE"

    def is_accessible(self) -> bool:
        status = self.bridge.get_bridge_status()
        return status.get("status") == "CONNECTED"

    def get_head_commit(self) -> str | None:
        return self.bridge.get_git_head_commit()

    def get_current_branch(self) -> str | None:
        return None

    def discover_files(self, max_files: int | None = None) -> list[str]:
        # Discovers files strictly within the local bridge root
        if not os.path.isdir(str(self.bridge.canonical_root)):
            return []
        local_src = LocalRepositorySource(
            str(self.bridge.canonical_root), policy=self.bridge.config.policy
        )
        return local_src.discover_files(max_files=max_files)

    def read_bytes(self, relative_path: str) -> bytes:
        text = self.bridge.read_local_file(relative_path)
        return text.encode("utf-8")

    def read_text(self, relative_path: str) -> str:
        return self.bridge.read_local_file(relative_path)

    def get_modified_files(
        self, base_commit: str, target_commit: str = "HEAD"
    ) -> list[GitDiffFile]:
        git = GitProvider(str(self.bridge.canonical_root))
        return git.get_modified_files(base_commit, target_commit)

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        git = GitProvider(str(self.bridge.canonical_root))
        return git.is_ancestor(maybe_ancestor, descendant)


class GitHubRepositorySource(RepositorySource):
    """Interacts with a GitHub repository using genuine API/remote access.

    Identifies repository using stable github_repository_id.
    Does NOT duplicate the full codebase into CortexForge database.
    """

    def __init__(
        self,
        github_repository_id: str | None,
        owner: str | None,
        repo: str | None,
        access_token: str | None = None,
        default_branch: str = "main",
        local_mirror_path: str | None = None,
    ) -> None:
        self.github_repository_id = github_repository_id
        self.owner = owner
        self.repo = repo
        self.access_token = access_token or os.environ.get("GITHUB_TOKEN")
        self.default_branch = default_branch
        self.local_mirror_path = local_mirror_path

    @property
    def source_type(self) -> str:
        return "GITHUB"

    def is_accessible(self) -> bool:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            return True
        return bool(self.owner and self.repo)

    def get_head_commit(self) -> str | None:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            return GitProvider(self.local_mirror_path).get_head_commit()
        return None

    def get_current_branch(self) -> str | None:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            return GitProvider(self.local_mirror_path).get_current_branch()
        return self.default_branch

    def discover_files(self, max_files: int | None = None) -> list[str]:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            local_src = LocalRepositorySource(self.local_mirror_path)
            return local_src.discover_files(max_files=max_files)
        return []

    def read_bytes(self, relative_path: str) -> bytes:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            local_src = LocalRepositorySource(self.local_mirror_path)
            return local_src.read_bytes(relative_path)
        raise FileNotFoundError(
            f"File '{relative_path}' cannot be read without active GitHub mirror."
        )

    def read_text(self, relative_path: str) -> str:
        return self.read_bytes(relative_path).decode("utf-8", errors="replace")

    def get_modified_files(
        self, base_commit: str, target_commit: str = "HEAD"
    ) -> list[GitDiffFile]:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            return GitProvider(self.local_mirror_path).get_modified_files(
                base_commit, target_commit
            )
        return []

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        if self.local_mirror_path and os.path.isdir(self.local_mirror_path):
            return GitProvider(self.local_mirror_path).is_ancestor(
                maybe_ancestor, descendant
            )
        return maybe_ancestor == descendant


def get_repository_source(project: Project) -> RepositorySource:
    """Factory creating appropriate RepositorySource based on project source_type and environment."""
    source_type = (project.source_type or "LOCAL").upper()

    if source_type == "GITHUB":
        return GitHubRepositorySource(
            github_repository_id=project.github_repository_id,
            owner=project.github_owner,
            repo=project.github_repo,
            default_branch=project.default_branch or "main",
            local_mirror_path=project.local_path
            if os.path.isdir(project.local_path)
            else None,
        )

    # In hosted mode where server cannot access client filesystem directly:
    server_env = os.environ.get("CORTEX_ENV", "development").lower()
    bridge_url = os.environ.get("CORTEX_BRIDGE_URL")
    if bridge_url and server_env in ("production", "prod", "staging"):
        bridge_token = os.environ.get("CORTEX_BRIDGE_TOKEN", "")
        config = LocalBridgeConfig(
            server_url=bridge_url,
            auth_token=bridge_token,
            project_id=project.id,
            local_path=project.local_path,
        )
        bridge_client = LocalBridgeClient(config)
        return LocalBridgeRepositorySource(bridge_client)

    return LocalRepositorySource(project.local_path)

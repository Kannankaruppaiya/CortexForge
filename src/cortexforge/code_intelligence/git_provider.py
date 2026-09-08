"""Secure native Git CLI abstraction."""

import os
import subprocess
from dataclasses import dataclass


@dataclass
class GitCommit:
    sha: str
    author: str
    date: str
    message: str


@dataclass
class GitDiffFile:
    file_path: str
    status: str  # "M" (modified), "A" (added), "D" (deleted), "R" (renamed)
    additions: int = 0
    deletions: int = 0


class GitProvider:
    """Safe wrapper over native Git CLI using argument arrays to prevent shell injection."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = os.path.realpath(repo_path)

    def _run_git(self, args: list[str], timeout: int = 15) -> str:
        """Execute git command safely with parameterized arguments."""
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
            return res.stdout.strip()
        except Exception:
            return ""

    def get_head_commit(self) -> str | None:
        """Return full 40-character SHA of current HEAD."""
        out = self._run_git(["rev-parse", "HEAD"])
        return out if len(out) == 40 else None

    def get_recent_commits(self, limit: int = 10) -> list[GitCommit]:
        """Fetch list of recent commits with metadata."""
        format_str = "%H|%an|%ad|%s"
        out = self._run_git(["log", f"-n{limit}", f"--pretty=format:{format_str}", "--date=iso"])
        if not out:
            return []

        commits = []
        for line in out.split("\n"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(
                    GitCommit(
                        sha=parts[0],
                        author=parts[1],
                        date=parts[2],
                        message=parts[3],
                    )
                )
        return commits

    def get_modified_files(
        self, base_commit: str | None = None, target_commit: str = "HEAD"
    ) -> list[GitDiffFile]:
        """Detect modified, added, and deleted files between commits."""
        if not base_commit:
            # Diff working tree against HEAD
            out = self._run_git(["status", "--porcelain"])
            diff_files = []
            for line in out.split("\n"):
                if not line.strip():
                    continue
                st = line[:2].strip()
                path = line[3:].strip().replace("\\", "/")
                diff_files.append(GitDiffFile(file_path=path, status=st or "M"))
            return diff_files

        out = self._run_git(["diff", "--name-status", base_commit, target_commit])
        diff_files = []
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                diff_files.append(
                    GitDiffFile(
                        status=parts[0][0],
                        file_path=parts[1].strip().replace("\\", "/"),
                    )
                )
        return diff_files

    def get_file_diff(self, file_path: str, base_commit: str | None = None) -> str:
        """Fetch unified diff for a single file."""
        args = ["diff"]
        if base_commit:
            args.append(base_commit)
        args.extend(["--", file_path])
        return self._run_git(args)

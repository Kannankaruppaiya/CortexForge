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
    old_path: str | None = None
    additions: int = 0
    deletions: int = 0


@dataclass
class GitDiffHunk:
    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str = ""


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
        """Detect modified, added, deleted, and renamed files between commits."""
        if not base_commit:
            # Diff working tree against HEAD
            out = self._run_git(["status", "--porcelain"])
            diff_files = []
            for line in out.split("\n"):
                if not line.strip():
                    continue
                st = line[:2].strip()
                path = line[3:].strip().replace("\\", "/")
                if " -> " in path:
                    old_p, new_p = path.split(" -> ", 1)
                    diff_files.append(
                        GitDiffFile(file_path=new_p.strip(), status="R", old_path=old_p.strip())
                    )
                else:
                    diff_files.append(GitDiffFile(file_path=path, status=st or "M"))
            return diff_files

        out = self._run_git(["diff", "-M", "--name-status", base_commit, target_commit])
        diff_files = []
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                diff_files.append(
                    GitDiffFile(
                        status=parts[0][0],
                        file_path=parts[1].strip().replace("\\", "/"),
                    )
                )
            elif len(parts) >= 3 and parts[0].startswith("R"):
                diff_files.append(
                    GitDiffFile(
                        status="R",
                        old_path=parts[1].strip().replace("\\", "/"),
                        file_path=parts[2].strip().replace("\\", "/"),
                    )
                )
        return diff_files

    def get_file_diff(self, file_path: str, base_commit: str | None = None) -> str:
        """Fetch unified diff for a single file."""
        args = ["diff"]
        if base_commit:
            args.append(base_commit)
        args.extend(["--", file_path.replace("\\", "/")])
        return self._run_git(args)

    def get_file_content_at_commit(self, commit_sha: str, file_path: str) -> bytes | None:
        """Safely fetch historical file content at a specific commit SHA using git show."""
        normalized_path = file_path.replace("\\", "/")
        try:
            res = subprocess.run(
                ["git", "show", f"{commit_sha}:{normalized_path}"],
                cwd=self.repo_path,
                capture_output=True,
                check=True,
                timeout=15,
            )
            return res.stdout
        except Exception:
            return None

    def get_diff_hunks(
        self, file_path: str, base_commit: str | None = None, target_commit: str = "HEAD"
    ) -> list[GitDiffHunk]:
        """Parse unified diff hunks with line numbers for a specific file."""
        import re

        args = ["diff", "-U0"]
        if base_commit:
            args.extend([base_commit, target_commit])
        args.extend(["--", file_path.replace("\\", "/")])

        out = self._run_git(args)
        if not out:
            return []

        hunks: list[GitDiffHunk] = []
        hunk_regex = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")

        for line in out.split("\n"):
            m = hunk_regex.match(line)
            if m:
                old_start = int(m.group(1))
                old_lines = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_lines = int(m.group(4)) if m.group(4) is not None else 1
                header = m.group(5).strip()
                hunks.append(
                    GitDiffHunk(
                        file_path=file_path,
                        old_start=old_start,
                        old_lines=old_lines,
                        new_start=new_start,
                        new_lines=new_lines,
                        header=header,
                    )
                )
        return hunks

"""Safe Git repository operations, URL validation, and language detection (§14, §16, §18)."""

import logging
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from cortexforge.security.path_safety import (
    WorkspacePolicy,
)

SAFE_GIT_URL_REGEX = re.compile(
    r"^(https?://[\w.\-]+(?:/[\w.\-]+)+(?:\.git)?|git@[\w.\-]+:[\w.\-]+/[\w.\-]+(?:\.git)?)$",
    re.IGNORECASE,
)

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".h": "C/C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
}


def validate_git_url(url: str) -> bool:
    """Validate Git clone URL to prevent SSRF and argument injection."""
    if not url or len(url) > 1024:
        return False
    stripped = url.strip()
    # Reject flag injection attempts
    if stripped.startswith("-") or "--" in stripped.split("/")[0]:
        return False
    # Prohibit file://, custom schemes, or unapproved protocols
    if any(
        stripped.lower().startswith(proto)
        for proto in ("file:", "ftp:", "smb:", "nfs:")
    ):
        return False
    return bool(SAFE_GIT_URL_REGEX.match(stripped))


def hardened_git_env() -> dict[str, str]:
    """Generate isolated environment variables for git subprocess."""
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "COMSPEC": os.environ.get("COMSPEC", ""),
        "TMP": os.environ.get("TMP", ""),
        "TEMP": os.environ.get("TEMP", ""),
    }


def clone_repository(
    url: str,
    target_dir: str | Path,
    branch: str | None = None,
    timeout: int = 120,
) -> None:
    """Safely clone a remote Git repository into target_dir."""
    if not validate_git_url(url):
        raise ValueError(f"Invalid or unsafe Git repository URL: '{url}'")

    target_path = Path(target_dir).resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git",
        "-c",
        "core.hooksPath=",
        "-c",
        "safe.directory=*",
        "clone",
        "--depth=1",
    ]
    if branch and branch.strip():
        cmd.extend(["-b", branch.strip()])

    cmd.extend(["--", url.strip(), str(target_path)])

    try:
        res = subprocess.run(
            cmd,
            env=hardened_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0:
            error_msg = res.stderr.strip() or res.stdout.strip() or "Git clone failed"
            # Redact any tokens or credentials that might be in stderr
            sanitized_err = re.sub(r"://[^@]+@", "://[REDACTED]@", error_msg)
            raise RuntimeError(f"Clone failed: {sanitized_err}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Git clone timed out after {timeout} seconds")
    except Exception as exc:
        raise RuntimeError(f"Git clone error: {exc}") from exc


def detect_languages(
    repo_path: str | Path, max_files: int = 2000
) -> tuple[str | None, dict[str, float]]:
    """Inspect repository file extensions to calculate language distribution."""
    canonical = Path(repo_path).resolve()
    if not canonical.is_dir():
        return None, {}

    counts: Counter[str] = Counter()
    scanned = 0

    for root, dirs, files in os.walk(canonical):
        # Skip dependency and hidden directories
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d
            not in (
                "node_modules",
                "venv",
                ".venv",
                "__pycache__",
                "target",
                "dist",
                "build",
                ".git",
            )
        ]
        for f in files:
            scanned += 1
            if scanned > max_files:
                break
            ext = os.path.splitext(f)[1].lower()
            if ext in EXTENSION_LANGUAGE_MAP:
                counts[EXTENSION_LANGUAGE_MAP[ext]] += 1
        if scanned > max_files:
            break

    total = sum(counts.values())
    if total == 0:
        return None, {}

    percentages = {
        lang: round((cnt / total) * 100, 1) for lang, cnt in counts.most_common(5)
    }
    primary = counts.most_common(1)[0][0]
    return primary, percentages


def inspect_local_repository(
    path_str: str, policy: WorkspacePolicy | None = None
) -> dict[str, Any]:
    """Validate and inspect a candidate local repository path across the host filesystem."""
    if not path_str or not path_str.strip():
        return {
            "valid": False,
            "is_git": False,
            "path": path_str,
            "error": "Path cannot be empty",
        }

    try:
        canonical = Path(path_str.strip()).resolve()
    except Exception as exc:
        return {
            "valid": False,
            "is_git": False,
            "path": path_str,
            "error": f"Invalid path: {exc}",
        }

    # Verify containment within allowed workspace policy ONLY if explicit policy provided
    if policy is not None and not policy.is_workspace_allowed(canonical):
        return {
            "valid": False,
            "is_git": False,
            "path": str(canonical),
            "error": "Repository path is outside the permitted workspace root boundary.",
        }

    if not canonical.is_dir():
        return {
            "valid": False,
            "is_git": False,
            "path": str(canonical),
            "error": "Directory does not exist.",
        }

    # Check for git repository
    git_dir = canonical / ".git"
    is_git = git_dir.exists()

    default_branch = "main"
    if is_git:
        detected = False
        try:
            cmd = [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "safe.directory=*",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
            ]
            res = subprocess.run(
                cmd,
                cwd=str(canonical),
                env=hardened_git_env(),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if (
                res.returncode == 0
                and res.stdout.strip()
                and res.stdout.strip() != "HEAD"
            ):
                default_branch = res.stdout.strip()
                detected = True
        except Exception as exc:
            logger.debug("Failed to detect git branch from HEAD: %s", exc)

        if not detected:
            try:
                head_file = git_dir / "HEAD"
                if head_file.is_file():
                    content = head_file.read_text(
                        encoding="utf-8", errors="ignore"
                    ).strip()
                    if content.startswith("ref: refs/heads/"):
                        branch_name = content.removeprefix("ref: refs/heads/").strip()
                        if branch_name:
                            default_branch = branch_name
            except Exception as exc:
                logger.debug("Failed to read .git/HEAD directly: %s", exc)

    primary_lang, lang_dict = detect_languages(canonical)

    return {
        "valid": True,
        "is_git": is_git,
        "path": str(canonical),
        "default_branch": default_branch,
        "detected_language": primary_lang,
        "languages": lang_dict,
        "error": None,
    }

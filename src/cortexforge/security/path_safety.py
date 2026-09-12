"""Centralized filesystem security abstraction for CortexForge (§12).

Every filesystem access must pass through this security layer:
- WorkspacePolicy: Enforces workspace allowlist, resource limits (file size, total bytes, depth, count).
- PathSecurity: Canonicalization, traversal prevention (POSIX, Windows drive, UNC, device paths, symlinks, junctions).
- SafeFileReader: Bounded, contained file reading.
- SafeDirectoryWalker: Depth-bounded, count-bounded, timeout/cancellation-aware directory traversal.
"""

import os
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when a path attempts to escape its designated root directory or violate security boundaries."""


class FilesystemLimitExceededError(RuntimeError):
    """Raised when scan limits (file count, total bytes, depth, or timeout) are exceeded."""


@dataclass
class WorkspacePolicy:
    """Enforces approved workspace containment and resource bounds."""

    allowed_roots: list[Path] = field(default_factory=list)
    max_file_size_bytes: int = 5 * 1024 * 1024  # 5 MB
    max_total_bytes: int = 150 * 1024 * 1024  # 150 MB
    max_file_count: int = 10_000
    max_directory_depth: int = 15
    scan_timeout_seconds: float = 60.0

    @classmethod
    def default(cls) -> "WorkspacePolicy":
        env_root = os.environ.get("CORTEX_WORKSPACE_ROOT")
        roots = []
        if env_root:
            roots.append(Path(env_root).resolve())
        else:
            roots.append(Path(os.getcwd()).resolve())
            import tempfile

            roots.append(Path(tempfile.gettempdir()).resolve())
        return cls(allowed_roots=roots)

    def is_workspace_allowed(self, path: str | Path) -> bool:
        """Check if path is contained within at least one approved workspace root."""
        try:
            target = Path(path).resolve()
        except Exception:
            return False
        for root in self.allowed_roots:
            try:
                if target == root or target.is_relative_to(root):
                    return True
            except (ValueError, AttributeError):
                pass
            # Fallback for Windows case-insensitive paths
            t_str = str(target).lower().rstrip("/\\")
            r_str = str(root).lower().rstrip("/\\")
            if t_str == r_str or t_str.startswith((r_str + os.sep, r_str + "/")):
                return True
        return False


class PathSecurity:
    """Validates and enforces filesystem isolation within project boundaries."""

    @staticmethod
    def safe_resolve(root_dir: str | Path, subpath: str | Path) -> str:
        """Resolve a candidate subpath and verify it does not escape root_dir.

        Raises PathSecurityError if the resolved path:
        - Escapes root_dir (POSIX ../, Windows drive C:\\, UNC \\\\server\\share, device \\\\.\\COM1)
        - Uses symlinks pointing outside root_dir
        - Uses Windows reparse points/junctions escaping root_dir
        Returns canonical absolute path as string.
        """
        canonical_root = Path(root_dir).resolve()

        raw_sub = str(subpath).strip()
        if not raw_sub:
            raise PathSecurityError("Empty subpath provided.")

        # Prohibit UNC network paths, Windows device namespaces, and NT device namespaces
        if raw_sub.startswith(("\\\\", "//", "\\??\\", "/??/")):
            raise PathSecurityError(
                f"UNC and extended device paths are prohibited: '{subpath}'"
            )

        # Prohibit Windows legacy DOS device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
        base_name = os.path.basename(raw_sub.replace("\\", "/")).split(".")[0].upper()
        if base_name in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        }:
            raise PathSecurityError(f"Device names are prohibited: '{subpath}'")

        # Normalize backslashes for cross-platform traversal detection
        clean_sub = raw_sub.replace("\\", "/")

        # Check if subpath is absolute, root-anchored, or drive-anchored
        is_abs_or_anchored = (
            raw_sub.startswith(("/", "\\"))
            or clean_sub.startswith("/")
            or (len(clean_sub) > 1 and clean_sub[1] == ":")
            or Path(raw_sub).is_absolute()
            or Path(clean_sub).is_absolute()
        )

        if is_abs_or_anchored:
            candidate = Path(raw_sub).resolve()
        else:
            sub_p = Path(clean_sub)
            candidate = (canonical_root / sub_p).resolve()

        # Check containment within canonical project root
        try:
            is_contained = candidate.is_relative_to(canonical_root)
        except (ValueError, AttributeError):
            is_contained = False

        if not is_contained or candidate == canonical_root:
            raise PathSecurityError(
                f"Path traversal detected: '{subpath}' escapes root directory '{root_dir}'"
            )

        # Symlink escape prevention: resolve fully and re-check containment
        real_candidate = Path(os.path.realpath(candidate))
        real_root = Path(os.path.realpath(canonical_root))
        try:
            is_real_contained = real_candidate.is_relative_to(real_root)
        except (ValueError, AttributeError):
            is_real_contained = False

        if not is_real_contained:
            raise PathSecurityError(
                f"Symlink escape detected: '{subpath}' points outside root directory"
            )

        return str(candidate)

    @staticmethod
    def is_safe_subpath(root_dir: str | Path, subpath: str | Path) -> bool:
        """Check if subpath is safely contained within root_dir without raising."""
        try:
            PathSecurity.safe_resolve(root_dir, subpath)
            return True
        except (PathSecurityError, ValueError):
            return False


class SafeFileReader:
    """Reads files strictly through the centralized security sandbox."""

    def __init__(self, policy: WorkspacePolicy | None = None):
        self.policy = policy or WorkspacePolicy.default()

    def read_text(
        self,
        root_dir: str | Path,
        subpath: str | Path,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> str:
        """Safely read text file within root_dir with size bounds."""
        canonical_path = PathSecurity.safe_resolve(root_dir, subpath)
        p = Path(canonical_path)

        if not p.is_file():
            raise FileNotFoundError(f"File not found: '{subpath}'")

        stat = p.stat()
        if stat.st_size > self.policy.max_file_size_bytes:
            raise FilesystemLimitExceededError(
                f"File '{subpath}' size {stat.st_size}B exceeds limit of {self.policy.max_file_size_bytes}B"
            )

        with open(canonical_path, encoding=encoding, errors=errors) as f:
            return f.read()

    def read_bytes(self, root_dir: str | Path, subpath: str | Path) -> bytes:
        """Safely read binary file within root_dir with size bounds."""
        canonical_path = PathSecurity.safe_resolve(root_dir, subpath)
        p = Path(canonical_path)

        if not p.is_file():
            raise FileNotFoundError(f"File not found: '{subpath}'")

        stat = p.stat()
        if stat.st_size > self.policy.max_file_size_bytes:
            raise FilesystemLimitExceededError(
                f"File '{subpath}' size {stat.st_size}B exceeds limit of {self.policy.max_file_size_bytes}B"
            )

        with open(canonical_path, "rb") as f:
            return f.read()


class SafeDirectoryWalker:
    """Safely iterates directory contents with resource limits and traversal defenses."""

    def __init__(self, policy: WorkspacePolicy | None = None):
        self.policy = policy or WorkspacePolicy.default()

    def walk(
        self,
        root_dir: str | Path,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Generator[str, None, None]:
        """Safely yield relative paths of contained files within resource limits.

        Enforces:
        - Max depth
        - Max file count
        - Max total bytes
        - Timeout
        - Cancellation
        - Symlink escapes
        """
        canonical_root = Path(root_dir).resolve()
        start_time = time.monotonic()
        files_count = 0
        total_bytes = 0

        for current_dir, dirs, files in os.walk(canonical_root, followlinks=False):
            # Check cancellation
            if is_cancelled and is_cancelled():
                break

            # Check timeout
            if time.monotonic() - start_time > self.policy.scan_timeout_seconds:
                raise FilesystemLimitExceededError(
                    f"Scan timeout exceeded ({self.policy.scan_timeout_seconds}s)"
                )

            # Check directory depth
            rel_dir = os.path.relpath(current_dir, canonical_root)
            depth = 0 if rel_dir == "." else len(Path(rel_dir).parts)
            if depth >= self.policy.max_directory_depth:
                dirs.clear()  # Do not descend deeper
                continue

            # Skip hidden and common build/dependency directories
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
                )
            ]

            for f in files:
                if is_cancelled and is_cancelled():
                    break
                if f.startswith("."):
                    continue

                full_path = os.path.join(current_dir, f)
                # Verify containment and symlink safety
                try:
                    rel_path = os.path.relpath(full_path, canonical_root)
                    PathSecurity.safe_resolve(canonical_root, rel_path)
                except PathSecurityError:
                    continue  # Skip unsafe paths / external symlinks

                try:
                    stat = os.stat(full_path)
                except OSError:
                    continue

                if stat.st_size > self.policy.max_file_size_bytes:
                    continue  # Skip oversized individual files

                files_count += 1
                total_bytes += stat.st_size

                if files_count > self.policy.max_file_count:
                    raise FilesystemLimitExceededError(
                        f"Max file count exceeded ({self.policy.max_file_count})"
                    )
                if total_bytes > self.policy.max_total_bytes:
                    raise FilesystemLimitExceededError(
                        f"Max scan byte limit exceeded ({self.policy.max_total_bytes} bytes)"
                    )

                yield rel_path.replace("\\", "/")

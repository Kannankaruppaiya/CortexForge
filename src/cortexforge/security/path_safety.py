"""Path traversal defense and filesystem isolation for CortexForge."""

from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when a path attempts to escape its designated root directory."""


class PathSecurity:
    """Validates and enforces filesystem isolation within project boundaries."""

    @staticmethod
    def safe_resolve(root_dir: str | Path, subpath: str | Path) -> str:
        """Resolve a candidate subpath and verify it does not escape root_dir.

        Raises PathSecurityError if the resolved path is outside root_dir, is
        a symlink pointing outside root_dir, or resolves to root_dir itself.
        Returns canonical absolute path.
        """
        canonical_root = Path(root_dir).resolve()

        raw_sub = str(subpath).strip()
        if not raw_sub:
            raise PathSecurityError("Empty subpath provided.")

        # Check if subpath is UNC network path
        if raw_sub.startswith(("\\\\", "//")):
            raise PathSecurityError(f"UNC network paths are prohibited: '{subpath}'")

        # Normalize backslashes for cross-platform traversal detection
        clean_sub = raw_sub.replace("\\", "/")

        # Check if subpath is absolute, root-anchored, or drive-anchored
        sub_p = Path(clean_sub)
        if (
            sub_p.is_absolute()
            or clean_sub.startswith("/")
            or (len(clean_sub) > 1 and clean_sub[1] == ":")
        ):
            candidate = Path(raw_sub).resolve()
        else:
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

        return str(candidate)

    @staticmethod
    def is_safe_subpath(root_dir: str | Path, subpath: str | Path) -> bool:
        """Check if subpath is safely contained within root_dir without raising."""
        try:
            PathSecurity.safe_resolve(root_dir, subpath)
            return True
        except (PathSecurityError, ValueError):
            return False

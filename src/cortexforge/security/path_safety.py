"""Path traversal defense and filesystem isolation for CortexForge."""

import os
from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when a path attempts to escape its designated root directory."""


class PathSecurity:
    """Validates and enforces filesystem isolation within project boundaries."""

    @staticmethod
    def safe_resolve(root_dir: str | Path, subpath: str | Path) -> str:
        """Resolve a candidate subpath and verify it does not escape root_dir.

        Raises PathSecurityError if the resolved path is outside root_dir.
        Returns canonical absolute path.
        """
        canonical_root = os.path.realpath(str(root_dir))
        
        # Prevent absolute paths from escaping root when joined
        raw_sub = str(subpath).strip()
        if os.path.isabs(raw_sub):
            # If absolute, it must already reside inside canonical_root
            candidate = os.path.realpath(raw_sub)
        else:
            # Strip leading slashes to prevent root-resetting in os.path.join
            clean_sub = raw_sub.lstrip("/\\")
            candidate = os.path.realpath(os.path.join(canonical_root, clean_sub))

        # Check containment
        common = os.path.commonpath([canonical_root, candidate])
        if common != canonical_root:
            raise PathSecurityError(
                f"Path traversal detected: '{subpath}' escapes root directory '{root_dir}'"
            )

        return candidate

    @staticmethod
    def is_safe_subpath(root_dir: str | Path, subpath: str | Path) -> bool:
        """Check if subpath is safely contained within root_dir without raising."""
        try:
            PathSecurity.safe_resolve(root_dir, subpath)
            return True
        except (PathSecurityError, ValueError):
            return False

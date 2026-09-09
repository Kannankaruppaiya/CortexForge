"""Generation-aware caching engine (specification section 40).

A cache must never become an alternate source of truth.

Entries are keyed on (project_id, generation, subkey). When a project's
generation advances -- via repository rescan, memory mutation or reconciliation --
entries from prior generations are rejected and evicted immediately.
A stale generation is never served as current truth.
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class GenerationCache:
    """In-memory cache enforcing generation-keyed invalidation per project."""

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[float, int, Any]] = {}
        self.default_ttl = default_ttl_seconds

    @staticmethod
    def _make_key(project_id: str, subkey: str) -> str:
        return f"{project_id}:{subkey}"

    def get(
        self,
        project_id: str,
        current_generation: int,
        subkey: str,
    ) -> Any | None:
        """Get cached value only if present, unexpired, and matching current generation."""
        composite = self._make_key(project_id, subkey)
        entry = self._cache.get(composite)
        if entry is None:
            return None

        expires_at, gen, val = entry
        now = time.time()
        if now > expires_at:
            del self._cache[composite]
            return None

        if gen != current_generation:
            logger.debug(
                "Cache entry %s rejected: entry generation %d != requested %d",
                composite,
                gen,
                current_generation,
            )
            del self._cache[composite]
            return None

        return val

    def set(
        self,
        project_id: str,
        generation: int,
        subkey: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store value anchored to a specific project generation."""
        composite = self._make_key(project_id, subkey)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = time.time() + ttl
        self._cache[composite] = (expires_at, generation, value)

    def invalidate_project(self, project_id: str) -> int:
        """Invalidate all cached entries for a project regardless of generation."""
        prefix = f"{project_id}:"
        to_del = [k for k in self._cache if k.startswith(prefix)]
        for k in to_del:
            del self._cache[k]
        return len(to_del)

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate all keys matching prefix."""
        to_del = [k for k in self._cache if k.startswith(prefix)]
        for k in to_del:
            del self._cache[k]
        return len(to_del)

    def clear(self) -> None:
        """Clear entire cache."""
        self._cache.clear()


# Backwards-compatible alias
CacheManager = GenerationCache

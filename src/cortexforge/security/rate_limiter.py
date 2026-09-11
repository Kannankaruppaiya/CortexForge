"""Sliding-window in-memory rate limiter for authentication endpoints."""

import threading
import time
from collections import defaultdict


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: float) -> bool:
        """Check if request under key is allowed within window_seconds."""
        now = time.time()
        with self._lock:
            timestamps = self._history[key]
            # Prune timestamps outside window
            cutoff = now - window_seconds
            self._history[key] = [t for t in timestamps if t > cutoff]
            return len(self._history[key]) < max_requests

    def record_hit(self, key: str) -> None:
        """Record an attempt under key."""
        now = time.time()
        with self._lock:
            self._history[key].append(now)

    def check_and_record(
        self, key: str, max_requests: int, window_seconds: float
    ) -> bool:
        """Atomically check and record hit if allowed. Returns True if allowed, False if limit exceeded."""
        now = time.time()
        with self._lock:
            timestamps = self._history[key]
            cutoff = now - window_seconds
            valid_timestamps = [t for t in timestamps if t > cutoff]
            if len(valid_timestamps) >= max_requests:
                self._history[key] = valid_timestamps
                return False
            valid_timestamps.append(now)
            self._history[key] = valid_timestamps
            return True

    def check(
        self,
        category: str,
        key: str,
        max_requests: int = 10,
        window_seconds: float = 60.0,
    ) -> bool:
        """Scoped check-and-record helper."""
        full_key = f"{category}:{key}"
        return self.check_and_record(full_key, max_requests, window_seconds)

    def record_failure(self, key: str) -> None:
        """Record an attempt or failure."""
        self.record_hit(key)

    def reset(self, key: str) -> None:
        """Clear rate limit history for key (e.g. after successful login)."""
        with self._lock:
            self._history.pop(key, None)

    def clear_all(self) -> None:
        """Clear all rate limiting history (for test isolation)."""
        with self._lock:
            self._history.clear()


# Global singleton instance for authentication rate limiting
auth_rate_limiter = SlidingWindowRateLimiter()

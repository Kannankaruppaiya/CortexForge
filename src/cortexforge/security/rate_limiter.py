"""Sliding-window rate limiter with Redis multi-worker coordination and in-memory fallback."""

import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_req = tonumber(ARGV[3])
local member = ARGV[4]

local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count < max_req then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, math.ceil(window) + 10)
    return 1
else
    return 0
end
"""


class SlidingWindowRateLimiter:
    """Rate limiter supporting both distributed Redis-backed sliding window and thread-safe in-memory fallback."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, list[float]] = defaultdict(list)
        self._redis_url = (
            redis_url
            or os.environ.get("CORTEX_REDIS_URL")
            or os.environ.get("REDIS_URL")
        )
        self._redis_client: Any = None
        if self._redis_url:
            try:
                import redis

                client = redis.Redis.from_url(
                    self._redis_url, decode_responses=True, socket_timeout=1.5
                )
                client.ping()
                self._redis_client = client
                logger.info("Connected to Redis for multi-worker distributed rate limiting.")
            except Exception as exc:
                logger.warning(
                    "Could not connect to Redis (%s), falling back to in-memory rate limiting.",
                    exc,
                )
                self._redis_client = None

    def _redis_check_and_record(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
        is_security_sensitive: bool = False,
    ) -> bool:
        """Atomic sliding window check and record in Redis using an atomic Lua script."""
        try:
            rkey = f"cortex:ratelimit:{key}"
            now = time.time()
            member = f"{now}:{uuid.uuid4().hex[:6]}"
            result = self._redis_client.eval(
                SLIDING_WINDOW_LUA,
                1,
                rkey,
                str(now),
                str(window_seconds),
                str(max_requests),
                member,
            )
            return bool(result == 1)
        except Exception as exc:
            logger.warning("Redis rate limiter error (%s)", exc)
            from cortexforge.core.db import is_managed_environment

            if is_managed_environment() and is_security_sensitive:
                logger.error(
                    "Failing closed for rate limit on '%s' due to Redis error in managed environment.",
                    key,
                )
                return False
            return self._memory_check_and_record(key, max_requests, window_seconds)

    def _memory_check_and_record(
        self, key: str, max_requests: int, window_seconds: float
    ) -> bool:
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

    def check_and_record(
        self,
        key: str,
        max_requests: int,
        window_seconds: float,
        is_security_sensitive: bool = False,
    ) -> bool:
        """Atomically check and record hit if allowed. Returns True if allowed, False if limit exceeded."""
        if self._redis_client is not None:
            return self._redis_check_and_record(
                key, max_requests, window_seconds, is_security_sensitive
            )
        from cortexforge.core.db import is_managed_environment

        if (
            is_managed_environment()
            and is_security_sensitive
            and not os.environ.get("PYTEST_CURRENT_TEST")
        ):
            logger.error(
                "Failing closed for rate limit on '%s' due to missing Redis in managed environment.",
                key,
            )
            return False
        return self._memory_check_and_record(key, max_requests, window_seconds)

    def check(
        self,
        category: str,
        key: str,
        max_requests: int = 10,
        window_seconds: float = 60.0,
    ) -> bool:
        """Scoped check-and-record helper."""
        full_key = f"{category}:{key}"
        is_sensitive = category.lower() in {
            "auth",
            "login",
            "password",
            "otp",
            "pwd_reset",
        }
        return self.check_and_record(
            full_key, max_requests, window_seconds, is_security_sensitive=is_sensitive
        )

    def record_hit(self, key: str) -> None:
        """Record an attempt under key."""
        if self._redis_client is not None:
            try:
                rkey = f"cortex:ratelimit:{key}"
                now = time.time()
                member = f"{now}:{uuid.uuid4().hex[:6]}"
                self._redis_client.zadd(rkey, {member: now})
                self._redis_client.expire(rkey, 300)
                return
            except Exception as exc:
                logger.debug("Redis record_hit failed, falling back to memory: %s", exc)
        now = time.time()
        with self._lock:
            self._history[key].append(now)

    def record_failure(self, key: str) -> None:
        """Record an attempt or failure."""
        self.record_hit(key)

    def reset(self, key: str) -> None:
        """Clear rate limit history for key (e.g. after successful login)."""
        if self._redis_client is not None:
            try:
                self._redis_client.delete(f"cortex:ratelimit:{key}")
            except Exception as exc:
                logger.debug("Redis reset failed: %s", exc)
        with self._lock:
            self._history.pop(key, None)

    def clear_all(self) -> None:
        """Clear all rate limiting history (for test isolation)."""
        if self._redis_client is not None:
            try:
                keys = self._redis_client.keys("cortex:ratelimit:*")
                if keys:
                    self._redis_client.delete(*keys)
            except Exception as exc:
                logger.debug("Redis clear_all failed: %s", exc)
        with self._lock:
            self._history.clear()


# Global singleton instance for authentication rate limiting
auth_rate_limiter = SlidingWindowRateLimiter()

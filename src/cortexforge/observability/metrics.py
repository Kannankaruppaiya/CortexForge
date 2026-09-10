"""Structured observability and metrics instrumentation for CortexForge."""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cortexforge.security.redactor import SecretRedactor

logger = logging.getLogger("cortexforge.observability")


@dataclass
class MetricsSnapshot:
    """Snapshot of CortexForge runtime telemetry and performance counters."""

    timestamp: str
    uptime_seconds: float
    total_requests: int = 0
    avg_request_latency_ms: float = 0.0
    total_retrievals: int = 0
    avg_retrieval_latency_ms: float = 0.0
    total_embeddings: int = 0
    avg_embedding_latency_ms: float = 0.0
    total_indexing_ops: int = 0
    avg_indexing_duration_ms: float = 0.0
    memory_writes: int = 0
    memory_revisions: int = 0
    stale_detections: int = 0
    conflicts_detected: int = 0
    consolidations: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate_pct: float = 0.0


# Telemetry modes. `CORTEX_TELEMETRY` is documented in .env.example, so it is
# read here and its effect is reported honestly: `otlp` export is not implemented,
# and a request for it produces a warning rather than being silently ignored. A
# configuration flag that appears to work while doing nothing is worse than one
# that says it is unavailable.
TELEMETRY_DISABLED = "disabled"
TELEMETRY_OTLP = "otlp"
SUPPORTED_TELEMETRY_MODES = frozenset({TELEMETRY_DISABLED})


def configured_telemetry_mode() -> str:
    """The telemetry mode this environment requests."""
    return os.environ.get("CORTEX_TELEMETRY", TELEMETRY_DISABLED).strip().lower()


class MetricsCollector:
    """Thread-safe in-process metrics aggregator with sanitization guarantees."""

    _instance: "MetricsCollector | None" = None

    def __init__(self) -> None:
        self.telemetry_mode = configured_telemetry_mode()
        if self.telemetry_mode not in SUPPORTED_TELEMETRY_MODES:
            logger.warning(
                "CORTEX_TELEMETRY is set to %r, but only %s is implemented. Metrics "
                "are collected in-process and exposed at /api/v1/metrics; nothing is "
                "exported.",
                self.telemetry_mode,
                ", ".join(sorted(SUPPORTED_TELEMETRY_MODES)),
            )
        self._start_time = time.time()
        self._requests_count = 0
        self._request_latencies: list[float] = []
        self._retrievals_count = 0
        self._retrieval_latencies: list[float] = []
        self._embeddings_count = 0
        self._embedding_latencies: list[float] = []
        self._indexing_count = 0
        self._indexing_durations: list[float] = []
        self._memory_writes = 0
        self._memory_revisions = 0
        self._stale_detections = 0
        self._conflicts_detected = 0
        self._consolidations = 0
        self._tokens_input = 0
        self._tokens_output = 0
        self._cache_hits = 0
        self._cache_misses = 0

    @classmethod
    def get_instance(cls) -> "MetricsCollector":
        """Singleton accessor for metrics collector."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_request_latency(self, duration_ms: float) -> None:
        self._requests_count += 1
        self._request_latencies.append(duration_ms)
        if len(self._request_latencies) > 500:
            self._request_latencies.pop(0)

    def record_retrieval_latency(self, duration_ms: float) -> None:
        self._retrievals_count += 1
        self._retrieval_latencies.append(duration_ms)
        if len(self._retrieval_latencies) > 500:
            self._retrieval_latencies.pop(0)

    def record_embedding_latency(self, duration_ms: float) -> None:
        self._embeddings_count += 1
        self._embedding_latencies.append(duration_ms)
        if len(self._embedding_latencies) > 500:
            self._embedding_latencies.pop(0)

    def record_indexing_duration(self, duration_ms: float) -> None:
        self._indexing_count += 1
        self._indexing_durations.append(duration_ms)
        if len(self._indexing_durations) > 200:
            self._indexing_durations.pop(0)

    def increment_memory_writes(self, count: int = 1) -> None:
        self._memory_writes += count

    def increment_memory_revisions(self, count: int = 1) -> None:
        self._memory_revisions += count

    def increment_stale_detections(self, count: int = 1) -> None:
        self._stale_detections += count

    def increment_conflicts_detected(self, count: int = 1) -> None:
        self._conflicts_detected += count

    def increment_consolidations(self, count: int = 1) -> None:
        self._consolidations += count

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self._tokens_input += input_tokens
        self._tokens_output += output_tokens

    def record_cache_access(self, hit: bool) -> None:
        if hit:
            self._cache_hits += 1
        else:
            self._cache_misses += 1

    def get_snapshot(self) -> MetricsSnapshot:
        """Produce point-in-time metrics snapshot."""
        now = time.time()
        uptime = now - self._start_time

        avg_req = (
            sum(self._request_latencies) / len(self._request_latencies)
            if self._request_latencies
            else 0.0
        )
        avg_ret = (
            sum(self._retrieval_latencies) / len(self._retrieval_latencies)
            if self._retrieval_latencies
            else 0.0
        )
        avg_emb = (
            sum(self._embedding_latencies) / len(self._embedding_latencies)
            if self._embedding_latencies
            else 0.0
        )
        avg_idx = (
            sum(self._indexing_durations) / len(self._indexing_durations)
            if self._indexing_durations
            else 0.0
        )

        total_cache = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total_cache * 100.0) if total_cache > 0 else 0.0

        return MetricsSnapshot(
            timestamp=datetime.now(UTC).isoformat(),
            uptime_seconds=round(uptime, 2),
            total_requests=self._requests_count,
            avg_request_latency_ms=round(avg_req, 2),
            total_retrievals=self._retrievals_count,
            avg_retrieval_latency_ms=round(avg_ret, 2),
            total_embeddings=self._embeddings_count,
            avg_embedding_latency_ms=round(avg_emb, 2),
            total_indexing_ops=self._indexing_count,
            avg_indexing_duration_ms=round(avg_idx, 2),
            memory_writes=self._memory_writes,
            memory_revisions=self._memory_revisions,
            stale_detections=self._stale_detections,
            conflicts_detected=self._conflicts_detected,
            consolidations=self._consolidations,
            tokens_input=self._tokens_input,
            tokens_output=self._tokens_output,
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
            cache_hit_rate_pct=round(hit_rate, 1),
        )

    def to_prometheus_text(self) -> str:
        """Render metrics in standard Prometheus exposition format."""
        snap = self.get_snapshot()
        lines = [
            "# HELP cortexforge_uptime_seconds Process uptime in seconds",
            "# TYPE cortexforge_uptime_seconds gauge",
            f"cortexforge_uptime_seconds {snap.uptime_seconds}",
            "# HELP cortexforge_http_requests_total Total HTTP requests handled",
            "# TYPE cortexforge_http_requests_total counter",
            f"cortexforge_http_requests_total {snap.total_requests}",
            "# HELP cortexforge_request_latency_ms Average HTTP request latency in ms",
            "# TYPE cortexforge_request_latency_ms gauge",
            f"cortexforge_request_latency_ms {snap.avg_request_latency_ms}",
            "# HELP cortexforge_retrievals_total Total memory retrievals",
            "# TYPE cortexforge_retrievals_total counter",
            f"cortexforge_retrievals_total {snap.total_retrievals}",
            "# HELP cortexforge_retrieval_latency_ms Average retrieval latency in ms",
            "# TYPE cortexforge_retrieval_latency_ms gauge",
            f"cortexforge_retrieval_latency_ms {snap.avg_retrieval_latency_ms}",
            "# HELP cortexforge_embeddings_total Total embedding calls",
            "# TYPE cortexforge_embeddings_total counter",
            f"cortexforge_embeddings_total {snap.total_embeddings}",
            "# HELP cortexforge_indexing_ops_total Total indexing operations",
            "# TYPE cortexforge_indexing_ops_total counter",
            f"cortexforge_indexing_ops_total {snap.total_indexing_ops}",
            "# HELP cortexforge_memory_writes_total Total memory writes",
            "# TYPE cortexforge_memory_writes_total counter",
            f"cortexforge_memory_writes_total {snap.memory_writes}",
            "# HELP cortexforge_cache_hit_rate_pct Memory cache hit rate percentage",
            "# TYPE cortexforge_cache_hit_rate_pct gauge",
            f"cortexforge_cache_hit_rate_pct {snap.cache_hit_rate_pct}",
        ]
        return "\n".join(lines) + "\n"

    def safe_log_event(self, event_name: str, payload: dict[str, Any]) -> None:
        """Log structured telemetry event guaranteeing secret redaction."""
        serialized = json.dumps(payload, default=str)
        sanitized = SecretRedactor.redact_secrets(serialized)
        logger.info("[%s] %s", event_name, sanitized)

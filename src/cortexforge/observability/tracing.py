"""OpenTelemetry-compatible distributed tracing and correlation for CortexForge.

Provides span creation, ambient context propagation, correlation tracking
(trace_id, task_id, project_id, workspace_id, job_id), and secret redaction
before any span attributes or events are recorded.
"""

import contextvars
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from cortexforge.security.redactor import SecretRedactor

logger = logging.getLogger("cortexforge.observability.tracing")

# Context variables for ambient correlation
_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cortexforge_current_trace_id", default=None
)
_CURRENT_SPAN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cortexforge_current_span_id", default=None
)
_CORRELATION_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "cortexforge_correlation_context", default=None
)

# OpenTelemetry bridge detection
try:
    from opentelemetry import trace as otel_trace

    _OTEL_AVAILABLE = True
except Exception:
    _OTEL_AVAILABLE = False


def is_telemetry_enabled() -> bool:
    """Check if telemetry tracing is active in the current environment."""
    mode = os.environ.get("CORTEX_TELEMETRY", "local").strip().lower()
    return mode not in {"disabled", "off", "0", "false"}


def generate_trace_id() -> str:
    """Generate a standard 32-hex-character W3C trace ID."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate a standard 16-hex-character W3C span ID."""
    return secrets.token_hex(8)


def get_current_trace_id() -> str:
    """Retrieve the ambient trace ID or generate a new one if not present."""
    tid = _CURRENT_TRACE_ID.get()
    if not tid:
        tid = generate_trace_id()
        _CURRENT_TRACE_ID.set(tid)
    return tid


def set_correlation_context(
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
    job_id: str | None = None,
    **kwargs: Any,
) -> None:
    """Set ambient correlation keys in the current async task context."""
    current = dict(_CORRELATION_CONTEXT.get() or {})
    if project_id is not None:
        current["project_id"] = str(project_id)
    if task_id is not None:
        current["task_id"] = str(task_id)
    if workspace_id is not None:
        current["workspace_id"] = str(workspace_id)
    if job_id is not None:
        current["job_id"] = str(job_id)
    for k, v in kwargs.items():
        if v is not None:
            current[k] = str(v)
    _CORRELATION_CONTEXT.set(current)


def get_correlation_context() -> dict[str, str]:
    """Get the current ambient correlation attributes."""
    return dict(_CORRELATION_CONTEXT.get() or {})


def _sanitize_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Sanitize and redact secrets from all span attributes."""
    sanitized: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, (int, float, bool)):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = SecretRedactor.redact_secrets(value)
        elif isinstance(value, (dict, list)):
            try:
                serialized = json.dumps(value)
                redacted = SecretRedactor.redact_secrets(serialized)
                sanitized[key] = json.loads(redacted)
            except Exception:
                sanitized[key] = str(value)
        else:
            sanitized[key] = SecretRedactor.redact_secrets(str(value))
    return sanitized


@dataclass
class SpanData:
    """In-memory representation of an executed trace span."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    duration_ms: float = 0.0
    status: str = "OK"  # "OK", "ERROR", "UNSET"
    attributes: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def finish(self, status: str = "OK", error: Exception | None = None) -> None:
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        self.status = status
        if error:
            self.status = "ERROR"
            self.error_message = SecretRedactor.redact_secrets(str(error))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceManager:
    """Thread-safe collector for in-process spans and OpenTelemetry bridge."""

    _instance: "TraceManager | None" = None

    def __init__(self, max_spans: int = 2000) -> None:
        self._max_spans = max_spans
        self._spans: list[SpanData] = []
        self._otel_tracer = None
        if _OTEL_AVAILABLE:
            try:
                self._otel_tracer = otel_trace.get_tracer("cortexforge")
            except Exception as exc:
                logger.debug("OpenTelemetry tracer initialization skipped: %s", exc)

    @classmethod
    def get_instance(cls) -> "TraceManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_span(self, span: SpanData) -> None:
        """Store span in circular in-memory buffer."""
        self._spans.append(span)
        if len(self._spans) > self._max_spans:
            self._spans.pop(0)

    def get_spans(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[SpanData]:
        """Retrieve collected spans filtered by trace_id or project_id."""
        matching = self._spans
        if trace_id:
            matching = [s for s in matching if s.trace_id == trace_id]
        if project_id:
            matching = [
                s
                for s in matching
                if s.attributes.get("project_id") == project_id
            ]
        return matching[-limit:]

    def clear(self) -> None:
        """Clear recorded in-memory spans."""
        self._spans.clear()


@contextmanager
def start_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    *,
    parent_span_id: str | None = None,
) -> Generator[SpanData, None, None]:
    """Synchronous context manager for tracing an operation."""
    trace_id = _CURRENT_TRACE_ID.get() or generate_trace_id()
    span_id = generate_span_id()
    parent_id = parent_span_id or _CURRENT_SPAN_ID.get()

    token_trace = _CURRENT_TRACE_ID.set(trace_id)
    token_span = _CURRENT_SPAN_ID.set(span_id)

    # Merge ambient correlation context with passed attributes
    combined_attrs = dict(get_correlation_context())
    if attributes:
        combined_attrs.update(attributes)
    sanitized_attrs = _sanitize_attributes(combined_attrs)

    span = SpanData(
        name=name,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_id,
        attributes=sanitized_attrs,
    )

    try:
        yield span
        span.finish(status="OK")
    except Exception as exc:
        span.finish(status="ERROR", error=exc)
        raise
    finally:
        TraceManager.get_instance().record_span(span)
        _CURRENT_TRACE_ID.reset(token_trace)
        _CURRENT_SPAN_ID.reset(token_span)


@asynccontextmanager
async def start_async_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    *,
    parent_span_id: str | None = None,
) -> AsyncGenerator[SpanData, None]:
    """Asynchronous context manager for tracing an async operation."""
    trace_id = _CURRENT_TRACE_ID.get() or generate_trace_id()
    span_id = generate_span_id()
    parent_id = parent_span_id or _CURRENT_SPAN_ID.get()

    token_trace = _CURRENT_TRACE_ID.set(trace_id)
    token_span = _CURRENT_SPAN_ID.set(span_id)

    combined_attrs = dict(get_correlation_context())
    if attributes:
        combined_attrs.update(attributes)
    sanitized_attrs = _sanitize_attributes(combined_attrs)

    span = SpanData(
        name=name,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_id,
        attributes=sanitized_attrs,
    )

    try:
        yield span
        span.finish(status="OK")
    except Exception as exc:
        span.finish(status="ERROR", error=exc)
        raise
    finally:
        TraceManager.get_instance().record_span(span)
        _CURRENT_TRACE_ID.reset(token_trace)
        _CURRENT_SPAN_ID.reset(token_span)

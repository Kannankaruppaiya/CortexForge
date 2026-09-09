"""Tests for OpenTelemetry-compatible tracing and secret redaction in CortexForge."""

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.observability.tracing import (
    TraceManager,
    set_correlation_context,
    start_async_span,
    start_span,
)


@pytest.fixture(autouse=True)
def clear_traces():
    """Ensure in-memory trace manager is fresh for each test."""
    TraceManager.get_instance().clear()
    yield
    TraceManager.get_instance().clear()


def test_start_sync_span_lifecycle():
    """Verify synchronous span creation, duration measurement, and status."""
    with start_span("test.sync_operation", {"custom_attr": "value123"}) as span:
        assert span.name == "test.sync_operation"
        assert span.trace_id is not None
        assert len(span.trace_id) == 32
        assert span.span_id is not None
        assert len(span.span_id) == 16
        assert span.status == "OK"

    assert span.end_time is not None
    assert span.duration_ms >= 0.0

    recorded = TraceManager.get_instance().get_spans(trace_id=span.trace_id)
    assert len(recorded) == 1
    assert recorded[0].name == "test.sync_operation"
    assert recorded[0].attributes["custom_attr"] == "value123"


@pytest.mark.asyncio
async def test_start_async_span_hierarchy():
    """Verify parent-child span hierarchy across async contexts."""
    async with start_async_span("parent.span", {"project_id": "proj_abc"}) as parent:
        parent_trace_id = parent.trace_id
        parent_span_id = parent.span_id

        async with start_async_span("child.span") as child:
            assert child.trace_id == parent_trace_id
            assert child.parent_span_id == parent_span_id

    recorded = TraceManager.get_instance().get_spans(trace_id=parent_trace_id)
    assert len(recorded) == 2
    names = [s.name for s in recorded]
    assert "child.span" in names
    assert "parent.span" in names


def test_secret_redaction_in_span_attributes():
    """Verify that credentials and tokens are redacted before span recording."""
    sensitive_token = "sk-proj-abc1234567890abcdef1234567890abcdef"
    db_pass_uri = "postgresql://user:supersecretpass@db.example.com:5432/cortex"

    with start_span(
        "redaction.test",
        {
            "api_key": sensitive_token,
            "conn_string": db_pass_uri,
            "nested": {"token": "ghp_123456789012345678901234567890123456"},
        },
    ) as span:
        pass

    assert sensitive_token not in str(span.attributes)
    assert "supersecretpass" not in str(span.attributes)
    assert "[REDACTED" in str(span.attributes)


def test_correlation_context_propagation():
    """Verify ambient correlation variables attach automatically to spans."""
    set_correlation_context(
        project_id="proj_xyz",
        task_id="task_123",
        workspace_id="ws_456",
        job_id="job_789",
    )

    with start_span("correlated.span") as span:
        assert span.attributes["project_id"] == "proj_xyz"
        assert span.attributes["task_id"] == "task_123"
        assert span.attributes["workspace_id"] == "ws_456"
        assert span.attributes["job_id"] == "job_789"


@pytest.mark.asyncio
async def test_api_tracing_middleware_and_traces_endpoint():
    """Verify that HTTP requests receive X-Trace-ID and produce queryable trace spans."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Call health endpoint
        resp = await client.get("/health")
        assert resp.status_code == 200
        trace_id = resp.headers.get("X-Trace-ID")
        assert trace_id is not None
        assert len(trace_id) == 32

        # Query /api/v1/traces
        traces_resp = await client.get(f"/api/v1/traces?trace_id={trace_id}")
        assert traces_resp.status_code == 200
        spans = traces_resp.json()
        assert len(spans) >= 1
        assert spans[0]["name"] == "http_request"
        assert spans[0]["trace_id"] == trace_id
        assert spans[0]["attributes"]["http.method"] == "GET"
        assert spans[0]["attributes"]["http.status_code"] == 200

"""FastAPI application entrypoint for CortexForge Gateway."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cortexforge.apps.api.routes import (
    agents,
    auth,
    cognition,
    cognitive,
    github,
    graph,
    jobs,
    memories,
    projects,
    retrieval,
)
from cortexforge.core.db import init_db
from cortexforge.core.schemas import HealthResponse
from cortexforge.security.auth import Principal, get_current_principal


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan handler for database initialization, recovery, and cleanup."""
    await init_db()
    from cortexforge.core.db import session_scope
    from cortexforge.jobs.durable import DurableJobStore

    try:
        async with session_scope() as session:
            await DurableJobStore().recover_abandoned(session)
    except Exception as exc:
        import logging

        logging.getLogger("cortexforge.api").warning(
            "Could not recover abandoned jobs at startup: %s", exc
        )
    yield


app = FastAPI(
    title="CortexForge API",
    description="A continuously evolving, verified project memory layer for AI coding agents.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS Configuration (§19): explicit origins, no wildcard credentials
allowed_origins_env = os.environ.get("CORTEX_ALLOWED_ORIGINS", "").strip()
if allowed_origins_env:
    allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
    allow_credentials = "*" not in allowed_origins
else:
    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ]
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    """Ambient distributed tracing middleware with validated trace context (§20)."""
    import re

    from cortexforge.observability.tracing import (
        _CURRENT_TRACE_ID,
        generate_trace_id,
        set_correlation_context,
        start_async_span,
    )

    raw_traceparent = request.headers.get("traceparent")
    raw_trace_id = request.headers.get("x-trace-id")
    trace_id: str | None = None

    # Validate standard W3C traceparent (version-traceid-parentid-traceflags)
    if raw_traceparent and re.match(
        r"^00-[0-9a-fA-F]{32}-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}$", raw_traceparent
    ):
        parts = raw_traceparent.split("-")
        if parts[1] != "0" * 32:
            trace_id = parts[1].lower()

    # If no valid traceparent, validate x-trace-id for hexadecimal characters (16-64 chars)
    if not trace_id and raw_trace_id:
        cleaned = raw_trace_id.strip()
        if re.match(r"^[0-9a-fA-F]{16,64}$", cleaned):
            trace_id = cleaned.lower()

    # Generate cryptographically secure trace ID if missing or untrusted
    if not trace_id:
        trace_id = generate_trace_id()

    # Extract client IP respecting reverse proxies if valid
    client_ip = "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host

    _CURRENT_TRACE_ID.set(trace_id)
    set_correlation_context(trace_id=trace_id)

    async with start_async_span(
        "http_request",
        {
            "http.method": request.method,
            "http.url": str(request.url.path),
            "http.client_ip": client_ip,
        },
    ) as span:
        response = await call_next(request)
        span.attributes["http.status_code"] = response.status_code
        if response.status_code >= 400:
            span.status = "ERROR"
        response.headers["X-Trace-ID"] = trace_id
        return response


# Mount API routers under /api/v1
app.include_router(auth.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(memories.router, prefix="/api/v1")
app.include_router(cognitive.router, prefix="/api/v1")
# The cognition router already carries its own /api/v1 prefix: its paths mix
# project-scoped and resource-scoped roots, so nesting it under another prefix
# would place /memories/{id}/claims under /projects.
app.include_router(cognition.router)
app.include_router(retrieval.router, prefix="/api/v1")
app.include_router(github.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")


@app.get("/health/live", tags=["health"])
@app.get("/api/v1/health/live", tags=["health"])
async def liveness_check() -> dict[str, Any]:
    """Process liveness probe (§15)."""
    return {"status": "alive", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/health/ready", tags=["health"])
@app.get("/api/v1/health/ready", tags=["health"])
async def readiness_check() -> dict[str, Any]:
    """Service readiness probe checking database connection (§15). Returns 503 if not ready."""
    import asyncio

    from sqlalchemy import text

    from cortexforge.core.db import session_scope

    try:
        async with asyncio.timeout(2.0):
            async with session_scope() as session:
                res = await session.execute(text("SELECT 1"))
                if res.scalar() != 1:
                    raise RuntimeError("Unexpected database response")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service not ready: database connection check failed ({exc})",
        )

    return {
        "status": "ready",
        "database": "connected",
        "version": "0.1.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/health", response_model=HealthResponse, tags=["health"])
@app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """System health check endpoint with active database verification (§15)."""
    import asyncio

    from sqlalchemy import text

    from cortexforge.core.db import session_scope

    database_connected = False
    try:
        async with asyncio.timeout(2.0):
            async with session_scope() as session:
                res = await session.execute(text("SELECT 1"))
                database_connected = bool(res.scalar() == 1)
    except Exception:
        database_connected = False

    status_str = "healthy" if database_connected else "degraded"
    return HealthResponse(
        status=status_str,
        database_connected=database_connected,
        version="0.1.0",
        timestamp=datetime.now(UTC),
    )


@app.get("/metrics", tags=["observability"])
@app.get("/api/v1/metrics", tags=["observability"])
async def get_metrics(request: Request, format: str | None = None):
    """Retrieve runtime performance telemetry and counters (§21)."""
    from fastapi.responses import PlainTextResponse

    from cortexforge.observability.metrics import MetricsCollector

    collector = MetricsCollector.get_instance()
    accept = request.headers.get("accept", "")
    if (
        format == "prometheus"
        or "text/plain" in accept
        or request.url.path == "/metrics"
    ):
        return PlainTextResponse(
            collector.to_prometheus_text(),
            media_type="text/plain; version=0.0.4",
        )

    return collector.get_snapshot()


@app.get("/api/v1/traces", tags=["observability"])
async def get_traces(
    trace_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    """Retrieve collected distributed trace spans with correlation and redacted attributes."""
    from cortexforge.observability.tracing import TraceManager

    if project_id:
        if not principal.can_access_project(project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to traces for project '{project_id}'.",
            )
    else:
        if not principal.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Global traces access requires administrator privileges. Provide a project_id.",
            )

    spans = TraceManager.get_instance().get_spans(
        trace_id=trace_id, project_id=project_id, limit=limit
    )
    return [s.to_dict() for s in spans]


@app.get("/api/v1/capabilities", tags=["capabilities"])
@app.get("/api/v1/cognitive/capabilities", tags=["capabilities"])
async def get_capabilities(
    status: str | None = None,
    category: str | None = None,
):
    """Retrieve the machine-readable capability registry (Specification §42)."""
    from cortexforge.core.capabilities import CapabilityRegistry, CapabilityStatus

    reg = CapabilityRegistry.get_instance()
    status_enum = None
    if status:
        try:
            status_enum = CapabilityStatus(status.upper())
        except ValueError:
            pass

    caps = reg.list_capabilities(status=status_enum, category=category)
    return {
        "summary": reg.summary(),
        "capabilities": [c.model_dump() for c in caps],
    }


# Serve Developer Web Dashboard if built in apps/web/dist
_dist_dir = Path(__file__).resolve().parents[4] / "apps" / "web" / "dist"
if _dist_dir.exists() and (_dist_dir / "index.html").exists():
    _assets_dir = _dist_dir / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str) -> FileResponse:
    """Serve built React single-page application and static assets."""
    if _dist_dir.exists() and (_dist_dir / "index.html").exists():
        if full_path:
            try:
                from cortexforge.security.path_safety import (
                    PathSecurity,
                    PathSecurityError,
                )

                canonical_target = PathSecurity.safe_resolve(_dist_dir, full_path)
                target_path = Path(canonical_target)
                if target_path.is_file():
                    return FileResponse(target_path)
            except (PathSecurityError, ValueError):
                pass
        return FileResponse(_dist_dir / "index.html")

    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="Web dashboard assets not built.")

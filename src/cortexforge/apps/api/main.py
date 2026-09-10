"""FastAPI application entrypoint for CortexForge Gateway."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cortexforge.apps.api.routes import (
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    """Ambient distributed tracing middleware with X-Trace-ID injection."""
    trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent")
    if trace_id and trace_id.count("-") == 3:
        parts = trace_id.split("-")
        trace_id = parts[1]

    from cortexforge.observability.tracing import (
        _CURRENT_TRACE_ID,
        generate_trace_id,
        set_correlation_context,
        start_async_span,
    )

    if not trace_id:
        trace_id = generate_trace_id()

    _CURRENT_TRACE_ID.set(trace_id)
    set_correlation_context(trace_id=trace_id)

    async with start_async_span(
        "http_request",
        {
            "http.method": request.method,
            "http.url": str(request.url.path),
            "http.client_ip": request.client.host if request.client else "unknown",
        },
    ) as span:
        response = await call_next(request)
        span.attributes["http.status_code"] = response.status_code
        if response.status_code >= 400:
            span.status = "ERROR"
        response.headers["X-Trace-ID"] = trace_id
        return response


# Mount API routers under /api/v1
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


@app.get("/health", response_model=HealthResponse, tags=["health"])
@app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """System health check endpoint."""
    return HealthResponse(
        status="healthy",
        database_connected=True,
        version="0.1.0",
        timestamp=datetime.now(UTC),
    )


@app.get("/api/v1/metrics", tags=["observability"])
async def get_metrics():
    """Retrieve runtime performance telemetry and counters."""
    from cortexforge.observability.metrics import MetricsCollector

    return MetricsCollector.get_instance().get_snapshot()


@app.get("/api/v1/traces", tags=["observability"])
async def get_traces(
    trace_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
):
    """Retrieve collected distributed trace spans with correlation and redacted attributes."""
    from cortexforge.observability.tracing import TraceManager

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

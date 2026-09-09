"""FastAPI application entrypoint for CortexForge Gateway."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
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



# Serve Developer Web Dashboard if built in apps/web/dist
_dist_dir = Path(__file__).resolve().parents[4] / "apps" / "web" / "dist"
if _dist_dir.exists() and (_dist_dir / "index.html").exists():
    _assets_dir = _dist_dir / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve built React single-page application and static assets."""
        requested_file = _dist_dir / full_path
        if full_path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(_dist_dir / "index.html")


"""FastAPI application entrypoint for CortexForge Gateway."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cortexforge.apps.api.routes import graph, projects
from cortexforge.core.db import init_db
from cortexforge.core.schemas import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan handler for database initialization and cleanup."""
    await init_db()
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

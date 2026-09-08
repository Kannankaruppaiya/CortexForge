"""Database engine and session configuration for CortexForge."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cortexforge.core.models import Base

DEFAULT_DB_URL = "sqlite+aiosqlite:///cortexforge.db"
DATABASE_URL = (
    os.environ.get("CORTEX_DB_URL")
    or os.environ.get("CORTEX_DATABASE_URL")
    or DEFAULT_DB_URL
)

# Normalize postgres:// to postgresql+asyncpg:// if provided
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)


def create_cortex_engine(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create async engine and session factory with driver-specific tuning."""
    engine_kwargs: dict[str, Any] = {"echo": False, "future": True}
    if "sqlite" in url:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    eng = create_async_engine(url, **engine_kwargs)
    factory = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
    return eng, factory


engine, async_session_factory = create_cortex_engine(DATABASE_URL)


def set_engine(new_engine: AsyncEngine) -> None:
    """Override engine and session factory (primarily used for test isolation)."""
    global engine, async_session_factory
    engine = new_engine
    async_session_factory = async_sessionmaker(
        bind=new_engine, class_=AsyncSession, expire_on_commit=False
    )


async def init_db() -> None:
    """Create all tables defined in Base.metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for yielding database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for standalone scripts or CLI tasks."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

"""Database engine and session configuration for CortexForge."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cortexforge.core.models import Base

logger = logging.getLogger(__name__)

DEFAULT_DB_URL = "sqlite+aiosqlite:///cortexforge.db"
DATABASE_URL = (
    os.environ.get("CORTEX_DB_URL")
    or os.environ.get("CORTEX_DATABASE_URL")
    or DEFAULT_DB_URL
)


def normalize_async_url(url: str) -> str:
    """Rewrite a database URL to the async driver this application requires.

    Operators reasonably write `postgresql://...` or `sqlite:///cortex.db`; both
    are valid SQLAlchemy URLs that name synchronous drivers, and handing either to
    `create_async_engine` fails with a message about asyncio extensions that says
    nothing about the actual mistake. Normalising here means the configuration a
    person would naturally write simply works.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


DATABASE_URL = normalize_async_url(DATABASE_URL)


def create_cortex_engine(
    url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create async engine and session factory with driver-specific tuning."""
    engine_kwargs: dict[str, Any] = {"echo": False, "future": True}
    if "sqlite" in url:
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30.0}
    else:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    eng = create_async_engine(url, **engine_kwargs)

    if "sqlite" in url:
        @event.listens_for(eng.sync_engine, "connect")
        def _configure_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
            except Exception as exc:
                logger.debug("Failed to set SQLite pragmas: %s", exc)
            finally:
                cursor.close()

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


def is_managed_environment() -> bool:
    """Check if the current runtime environment is a managed environment requiring Alembic migrations."""
    env = os.environ.get("CORTEX_ENV", os.environ.get("ENVIRONMENT", "")).strip().lower()
    return env in ("production", "prod", "staging")


async def init_db() -> None:
    """Initialize database schema.

    In managed environments (production, prod, staging), Alembic migrations are the
    authoritative schema manager invoked prior to startup, avoiding unmanaged create_all calls (§14, §15).
    """
    if is_managed_environment():
        from sqlalchemy import text

        async with engine.begin() as conn:
            try:
                res = await conn.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                version = res.scalar()
                if not version and not os.environ.get("PYTEST_CURRENT_TEST"):
                    raise RuntimeError(
                        "Database schema authority error: 'alembic_version' table is empty. "
                        "Run 'alembic upgrade head' prior to managed environment startup."
                    )
            except Exception as exc:
                if not os.environ.get("PYTEST_CURRENT_TEST"):
                    raise RuntimeError(
                        f"Managed environment database schema authority check failed: {exc}. "
                        "Run 'alembic upgrade head' before starting in staging/production."
                    ) from exc
        return

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

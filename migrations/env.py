import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
#
# Two guards, both because migrations are also run in-process (from tests and
# from the job runner) rather than only from the `alembic` CLI:
#   * `configure_logger` lets an embedding caller opt out entirely, which is the
#     convention Alembic's own template follows.
#   * `disable_existing_loggers=False` stops a migration run from silencing every
#     logger the host application already configured.
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

from cortexforge.core.db import DATABASE_URL as DEFAULT_DATABASE_URL
from cortexforge.core.models import Base

target_metadata = Base.metadata


def resolve_url() -> str:
    """Decide which database this migration run targets.

    A URL supplied by the caller -- via ``alembic -x``, a programmatic
    ``Config.set_main_option``, or alembic.ini -- wins over the application's
    configured database. Previously this file overwrote the caller's choice with
    ``DATABASE_URL`` unconditionally, which meant migrations could only ever be
    run against whatever the environment happened to point at: tests could not
    target a scratch database, and an operator could not migrate a specific one.
    """
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override

    configured = config.get_main_option("sqlalchemy.url", "")
    # alembic.ini ships a placeholder; treat it as "unset" rather than as a target.
    if configured and not configured.startswith("driver://"):
        return configured

    return DEFAULT_DATABASE_URL


DATABASE_URL = resolve_url()
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = DATABASE_URL
    connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_sync() -> None:
    """Run migrations over a synchronous driver."""
    from sqlalchemy import engine_from_config

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = DATABASE_URL

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode, against a sync or async driver."""
    if "+aiosqlite" in DATABASE_URL or "+asyncpg" in DATABASE_URL:
        asyncio.run(run_async_migrations())
    else:
        run_migrations_sync()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

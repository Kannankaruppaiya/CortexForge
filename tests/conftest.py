"""Shared pytest fixtures for CortexForge tests."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import cortexforge.core.db as core_db
from cortexforge.core.db import set_engine
from cortexforge.core.models import Base


@pytest.fixture(scope="session", autouse=True)
def isolated_database():
    """Point the process-wide engine at a throwaway database for the whole run.

    Integration tests reach the API through `init_db()` and `session_scope()`,
    which use the module-global engine. Without this fixture that engine resolves
    to `cortexforge.db` in the repository root: tests would write to a real file,
    leak state between runs, and -- because `create_all` never alters an existing
    table -- fail confusingly the first time a model gains a column. Redirecting
    the engine once per session keeps runs hermetic and repeatable.
    """
    tmp_dir = tempfile.mkdtemp(prefix="cortexforge_test_db_")
    db_path = os.path.join(tmp_dir, "test.db")
    original_engine = core_db.engine

    test_engine, _ = core_db.create_cortex_engine(f"sqlite+aiosqlite:///{db_path}")
    set_engine(test_engine)

    yield db_path

    set_engine(original_engine)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def sample_repo():
    """Create a temporary multi-language repository."""
    tmp_dir = tempfile.mkdtemp(prefix="cortexforge_test_repo_")

    # Python module
    os.makedirs(os.path.join(tmp_dir, "services"), exist_ok=True)
    with open(os.path.join(tmp_dir, "services", "auth.py"), "w", encoding="utf-8") as f:
        f.write("""
class AuthService:
    def authenticate(self, user: str) -> bool:
        return user == "admin"
""")

    with open(
        os.path.join(tmp_dir, "services", "payment.py"), "w", encoding="utf-8"
    ) as f:
        f.write("""
import services.auth

class PaymentService:
    def __init__(self):
        self.auth = services.auth.AuthService()

    def process(self, amount: float) -> bool:
        return amount > 0
""")

    # TypeScript module
    os.makedirs(os.path.join(tmp_dir, "client"), exist_ok=True)
    with open(os.path.join(tmp_dir, "client", "api.ts"), "w", encoding="utf-8") as f:
        f.write("""
export interface ApiConfig {
    baseUrl: string;
}

export class ApiClient {
    fetchData(): string {
        return "data";
    }
}
""")

    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def test_session():
    """Create an in-memory SQLite async test database and isolate global engine."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    orig_engine = core_db.engine
    set_engine(test_engine)

    session_maker = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        yield session

    set_engine(orig_engine)
    await test_engine.dispose()

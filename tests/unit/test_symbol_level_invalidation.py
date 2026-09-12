"""Unit test validating Section 11 Invariant:

A memory about function_a() should NOT become stale merely because
an unrelated function_b() in the same file changed.
"""

import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import Base, Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.service import MemoryService


@pytest.fixture
async def symbol_test_env():
    """Create temporary project repository and isolated SQLite DB."""
    temp_dir = tempfile.mkdtemp(prefix="cortex_sym_inv_")
    services_dir = os.path.join(temp_dir, "services")
    os.makedirs(services_dir, exist_ok=True)

    # Initial file with two distinct functions
    calc_file = os.path.join(services_dir, "calc.py")
    with open(calc_file, "w", encoding="utf-8") as f:
        f.write("""def add_numbers(a: int, b: int) -> int:
    return a + b

def multiply_numbers(x: int, y: int) -> int:
    return x * y
""")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield temp_dir, session, calc_file

    await engine.dispose()


@pytest.mark.asyncio
async def test_fine_grained_symbol_invalidation_invariant(symbol_test_env):
    temp_dir, session, calc_file = symbol_test_env

    # 1. Register & Scan project
    project = Project(name="SymbolPrecisionTest", local_path=temp_dir, status="READY")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    scanner = RepositoryScanner()
    await scanner.scan_project(session, project, incremental=False)

    # Fetch scanned entities
    mem_service = MemoryService()

    # Create Memory 1 grounded in add_numbers (lines 1-2)
    mem_add = await mem_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            layer="L2",
            memory_type="CONVENTION",
            title="Addition Convention",
            content="add_numbers handles basic addition.",
            summary="Addition summary",
            importance=0.8,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/calc.py", line_start=1, line_end=2
                )
            ],
        ),
    )

    # Create Memory 2 grounded in multiply_numbers (lines 4-5)
    mem_mult = await mem_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            layer="L3",
            memory_type="DECISION",
            title="Multiplication Algorithm",
            content="multiply_numbers computes product.",
            summary="Multiplication summary",
            importance=0.9,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/calc.py", line_start=4, line_end=5
                )
            ],
        ),
    )

    assert mem_add.status == "ACTIVE"
    assert mem_mult.status == "ACTIVE"

    # 2. Modify ONLY multiply_numbers in services/calc.py (add_numbers is completely untouched!)
    with open(calc_file, "w", encoding="utf-8") as f:
        f.write("""def add_numbers(a: int, b: int) -> int:
    return a + b

def multiply_numbers(x: int, y: int) -> int:
    # Optimized multiplication with algorithm change
    res = x * y
    return res
""")

    # 3. Propagate changes
    propagator = SemanticChangePropagator()
    impact = await propagator.propagate_changes(
        session, project.id, modified_files=["services/calc.py"], mark_stale=True
    )

    # Re-fetch memories
    updated_add = await mem_service.get_memory(session, mem_add.id)
    updated_mult = await mem_service.get_memory(session, mem_mult.id)

    # 4. Verify Invariant:
    # multiply_numbers WAS modified -> mem_mult MUST be STALE
    assert updated_mult is not None
    assert updated_mult.status == "STALE"
    assert any("Multiplication Algorithm" in t for t in impact.memories_flagged_stale)

    # add_numbers was NOT modified -> mem_add MUST REMAIN ACTIVE!
    assert updated_add is not None
    assert updated_add.status == "ACTIVE"
    assert any("Addition Convention" in t for t in impact.memories_retained_active)

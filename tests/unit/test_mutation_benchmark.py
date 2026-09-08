"""Unit tests running the mutation benchmark suite."""

import tempfile
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.core.models import Base
from cortexforge.evaluation.mutations import CANONICAL_MUTATIONS, MutationBenchmarkHarness


@pytest.mark.asyncio
async def test_mutation_benchmark_suite():
    """Run all canonical repository mutations and verify cognitive update accuracy."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    harness = MutationBenchmarkHarness()

    for case in CANONICAL_MUTATIONS:
        temp_dir = tempfile.mkdtemp(prefix=f"cortex_mut_{case.id}_")
        async with factory() as session:
            result = await harness.run_mutation_test(session, temp_dir, case)
            assert result.passed, f"Mutation test {case.id} ({case.name}) failed! Details: {result.details}"

    await engine.dispose()

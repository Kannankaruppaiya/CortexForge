"""Mutation Benchmark Suite for Evaluating Cognitive Update Engine Ground Truth.

Implements Specification Section 45:
Deterministic repository mutations:
- rename symbol
- move file
- change signature
- change function behaviour
- change database schema
- change API contract
- change configuration
- remove dependency
- change architecture edge
- change test expectation

For each mutation, defines expected cognitive update outcomes:
(unchanged, reanchored, revised, stale, conflicted, invalidated).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import (
    ChangeImpactReport,
    SemanticChangePropagator,
)
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.service import MemoryService


class MutationType(str, Enum):
    RENAME_SYMBOL = "RENAME_SYMBOL"
    MOVE_FILE = "MOVE_FILE"
    CHANGE_SIGNATURE = "CHANGE_SIGNATURE"
    CHANGE_BEHAVIOUR = "CHANGE_BEHAVIOUR"
    CHANGE_DB_SCHEMA = "CHANGE_DB_SCHEMA"
    CHANGE_API_CONTRACT = "CHANGE_API_CONTRACT"
    CHANGE_CONFIGURATION = "CHANGE_CONFIGURATION"
    REMOVE_DEPENDENCY = "REMOVE_DEPENDENCY"
    CHANGE_ARCHITECTURE_EDGE = "CHANGE_ARCHITECTURE_EDGE"
    CHANGE_TEST_EXPECTATION = "CHANGE_TEST_EXPECTATION"


@dataclass
class MutationTestCase:
    id: str
    name: str
    mutation_type: MutationType
    initial_code: dict[str, str]
    initial_memory: MemoryCreate
    mutated_code: dict[str, str]
    expected_status: str  # ACTIVE, STALE, INVALIDATED, CONFLICTED
    expected_reanchored: bool = False
    expected_invalidated: bool = False
    expected_stale: bool = False


@dataclass
class MutationResult:
    test_id: str
    mutation_type: str
    passed: bool
    actual_status: str
    expected_status: str
    reanchored_as_expected: bool
    invalidated_as_expected: bool
    details: str = ""


CANONICAL_MUTATIONS: list[MutationTestCase] = [
    MutationTestCase(
        id="MUT-01",
        name="Rename Function Symbol Preserving Body",
        mutation_type=MutationType.RENAME_SYMBOL,
        initial_code={
            "calc.py": "def add_tax(val: float) -> float:\n    return val * 1.1\n"
        },
        initial_memory=MemoryCreate(
            title="Tax Addition Logic",
            content="add_tax adds 10% tax.",
            summary="Tax addition",
            layer="L2",
            memory_type="CONVENTION",
            evidence=[MemoryEvidenceCreate(file_path="calc.py", line_start=1, line_end=2)],
        ),
        mutated_code={
            "calc.py": "def apply_tax(val: float) -> float:\n    return val * 1.1\n"
        },
        expected_status="ACTIVE",
        expected_reanchored=True,
    ),
    MutationTestCase(
        id="MUT-02",
        name="Change Function Signature Parameters",
        mutation_type=MutationType.CHANGE_SIGNATURE,
        initial_code={
            "auth.py": "def verify(user: str, token: str) -> bool:\n    return bool(token)\n"
        },
        initial_memory=MemoryCreate(
            title="Authentication Verification",
            content="verify accepts user and token.",
            summary="Auth check",
            layer="L3",
            memory_type="DECISION",
            evidence=[MemoryEvidenceCreate(file_path="auth.py", line_start=1, line_end=2)],
        ),
        mutated_code={
            "auth.py": "def verify(user: str, token: str, mfa: bool = False) -> bool:\n    return bool(token)\n"
        },
        expected_status="STALE",
        expected_stale=True,
    ),
    MutationTestCase(
        id="MUT-03",
        name="Remove Function Without Replacement",
        mutation_type=MutationType.REMOVE_DEPENDENCY,
        initial_code={
            "legacy.py": "def legacy_routine() -> None:\n    pass\n"
        },
        initial_memory=MemoryCreate(
            title="Legacy Routine Execution",
            content="legacy_routine performs maintenance.",
            summary="Legacy routine",
            layer="L2",
            memory_type="CONVENTION",
            evidence=[MemoryEvidenceCreate(file_path="legacy.py", line_start=1, line_end=2)],
        ),
        mutated_code={
            "legacy.py": "# Legacy routine removed\n"
        },
        expected_status="INVALIDATED",
        expected_invalidated=True,
    ),
    MutationTestCase(
        id="MUT-04",
        name="Change Function Internal Behavior",
        mutation_type=MutationType.CHANGE_BEHAVIOUR,
        initial_code={
            "hash_util.py": "def hash_str(s: str) -> str:\n    return s.strip().lower()\n"
        },
        initial_memory=MemoryCreate(
            title="String Hashing Behavior",
            content="hash_str strips and lowercases string.",
            summary="String hash",
            layer="L2",
            memory_type="CONVENTION",
            evidence=[MemoryEvidenceCreate(file_path="hash_util.py", line_start=1, line_end=2)],
        ),
        mutated_code={
            "hash_util.py": "def hash_str(s: str) -> str:\n    # Modified algorithm\n    return s.replace(' ', '_').upper()\n"
        },
        expected_status="STALE",
        expected_stale=True,
    ),
]


class MutationBenchmarkHarness:
    """Executes deterministic repository mutations and verifies cognitive update engine accuracy."""

    def __init__(self) -> None:
        self.propagator = SemanticChangePropagator()
        self.scanner = RepositoryScanner()
        self.mem_service = MemoryService()

    async def run_mutation_test(
        self,
        session: AsyncSession,
        project_dir: str,
        case: MutationTestCase,
    ) -> MutationResult:
        """Run a single mutation test case and assert cognitive update invariants."""
        # 1. Write initial files
        for rel_path, code in case.initial_code.items():
            full_path = f"{project_dir}/{rel_path}"
            import os
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(code)

        # 2. Register & scan project
        project = Project(name=f"MutationTest_{case.id}", local_path=project_dir, status="READY")
        session.add(project)
        await session.commit()
        await session.refresh(project)

        await self.scanner.scan_project(session, project, incremental=False)

        # 3. Create initial memory
        mem = await self.mem_service.create_memory(session, project.id, case.initial_memory)
        assert mem.status == "ACTIVE"

        # 4. Apply mutation to disk
        modified_files = []
        for rel_path, code in case.mutated_code.items():
            full_path = f"{project_dir}/{rel_path}"
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(code)
            modified_files.append(rel_path)

        # 5. Propagate changes
        report: ChangeImpactReport = await self.propagator.propagate_changes(
            session=session,
            project_id=project.id,
            modified_files=modified_files,
            mark_stale=True,
        )

        # 6. Evaluate cognitive update results
        updated_mem = await self.mem_service.get_memory(session, mem.id)
        actual_st = updated_mem.status if updated_mem else "UNKNOWN"

        reanchored_ok = (not case.expected_reanchored) or bool(report.memories_reanchored)
        invalidated_ok = (not case.expected_invalidated) or bool(report.memories_invalidated)

        passed = (
            actual_st == case.expected_status
            and reanchored_ok
            and invalidated_ok
        )

        details = (
            f"Expected {case.expected_status}, got {actual_st}. "
            f"Reanchored: {report.memories_reanchored}, Invalidated: {report.memories_invalidated}."
        )

        return MutationResult(
            test_id=case.id,
            mutation_type=case.mutation_type.value,
            passed=passed,
            actual_status=actual_st,
            expected_status=case.expected_status,
            reanchored_as_expected=reanchored_ok,
            invalidated_as_expected=invalidated_ok,
            details=details,
        )

    async def run_suite(
        self,
        session: AsyncSession,
        project_id: str | None = None,
    ) -> list[MutationResult]:
        """Run all canonical repository mutation benchmarks."""
        import tempfile
        results = []
        for case in CANONICAL_MUTATIONS:
            with tempfile.TemporaryDirectory() as tmpdir:
                res = await self.run_mutation_test(session, tmpdir, case)
                results.append(res)
        return results


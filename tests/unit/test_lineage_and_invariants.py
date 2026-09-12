"""Unit tests validating symbol lineage re-anchoring, architecture invariants, and provenance."""

import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import (
    ArchitectureRule,
    Base,
    CodeEntity,
    Project,
    Relationship,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.provenance import ProvenanceEngine
from cortexforge.memory.service import MemoryService


@pytest.fixture
async def lineage_test_env():
    """Create isolated SQLite environment with a sample repository."""
    temp_dir = tempfile.mkdtemp(prefix="cortex_lineage_test_")
    src_dir = os.path.join(temp_dir, "core")
    os.makedirs(src_dir, exist_ok=True)

    math_file = os.path.join(src_dir, "math_utils.py")
    with open(math_file, "w", encoding="utf-8") as f:
        f.write("""def compute_discount(price: float, rate: float) -> float:
    return price * (1.0 - rate)

def obsolete_tax_calc(amount: float) -> float:
    return amount * 0.05
""")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield temp_dir, session, math_file

    await engine.dispose()


@pytest.mark.asyncio
async def test_symbol_reanchoring_and_invalidation(lineage_test_env):
    temp_dir, session, math_file = lineage_test_env

    # 1. Register & Initial Scan
    project = Project(name="LineageTestProj", local_path=temp_dir, status="READY")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    scanner = RepositoryScanner()
    await scanner.scan_project(session, project, incremental=False)

    mem_service = MemoryService()
    # Memory 1: attached to compute_discount
    mem_discount = await mem_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            layer="L2",
            memory_type="CONVENTION",
            title="Discount Computation Logic",
            content="compute_discount applies a rate reduction to price.",
            summary="Discount logic",
            importance=0.8,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="core/math_utils.py", line_start=1, line_end=2
                )
            ],
        ),
    )

    # Memory 2: attached to obsolete_tax_calc
    mem_tax = await mem_service.create_memory(
        session,
        project.id,
        MemoryCreate(
            layer="L2",
            memory_type="CONVENTION",
            title="Tax Calculation Formula",
            content="obsolete_tax_calc calculates 5% tax.",
            summary="Tax calculation",
            importance=0.7,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="core/math_utils.py", line_start=4, line_end=5
                )
            ],
        ),
    )

    assert mem_discount.status == "ACTIVE"
    assert mem_tax.status == "ACTIVE"

    # 2. Rename compute_discount -> calculate_discount (same body & type)
    # AND completely remove obsolete_tax_calc
    with open(math_file, "w", encoding="utf-8") as f:
        f.write("""def calculate_discount(price: float, rate: float) -> float:
    return price * (1.0 - rate)
""")

    # 3. Propagate changes
    propagator = SemanticChangePropagator()
    impact = await propagator.propagate_changes(
        session, project.id, modified_files=["core/math_utils.py"], mark_stale=True
    )

    # 4. Verify outcomes:
    # Memory 1 was re-anchored to calculate_discount and REMAINED ACTIVE!
    updated_discount = await mem_service.get_memory(session, mem_discount.id)
    assert updated_discount is not None
    assert updated_discount.status == "ACTIVE"
    assert any("Discount Computation Logic" in t for t in impact.memories_reanchored)

    # Memory 2 had its symbol deleted without replacement -> INVALIDATED
    updated_tax = await mem_service.get_memory(session, mem_tax.id)
    assert updated_tax is not None
    assert updated_tax.status == "INVALIDATED"
    assert any("Tax Calculation Formula" in t for t in impact.memories_invalidated)

    # 5. Provenance must explain the re-anchoring.
    #
    # Re-anchoring deliberately does not bump the memory's version: nothing the
    # memory asserts changed, only where its evidence points. The audit trail for
    # it therefore lives in the decision log, which records the decision code, the
    # reason code and the symbols involved.
    provenance = await ProvenanceEngine.get_provenance(session, updated_discount.id)
    assert provenance is not None
    assert provenance.status == "ACTIVE"
    reanchor_decisions = [
        d for d in provenance.decisions if d["decision"] == "REANCHOR"
    ]
    assert reanchor_decisions, (
        f"expected a REANCHOR decision, got {provenance.decisions}"
    )
    assert reanchor_decisions[0]["reason_code"] == "SYMBOL_RENAMED"
    assert "compute_discount" in reanchor_decisions[0]["reason"]
    # The claim inside the memory was re-verified against the symbol's new name.
    assert any(claim["status"] == "VERIFIED" for claim in provenance.claims)

    # The invalidated memory's decision log must say why it was invalidated.
    tax_provenance = await ProvenanceEngine.get_provenance(session, updated_tax.id)
    invalidations = [
        d for d in tax_provenance.decisions if d["decision"] == "INVALIDATE"
    ]
    assert invalidations
    assert invalidations[0]["reason_code"] == "SYMBOL_REMOVED"


@pytest.mark.asyncio
async def test_architecture_invariant_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        project = Project(name="ArchTestProj", local_path="/tmp/fake", status="READY")
        session.add(project)
        await session.commit()
        await session.refresh(project)

        # Create entities: UI component and Database repository
        ui_ent = CodeEntity(
            project_id=project.id,
            name="UserProfileView",
            qualified_name="apps.web.UserProfileView",
            entity_type="class",
            file_path="apps/web/user_profile.tsx",
            language="typescript",
            start_line=1,
            end_line=20,
            content_hash="hash1",
        )
        db_ent = CodeEntity(
            project_id=project.id,
            name="UserRepository",
            qualified_name="core.database.UserRepository",
            entity_type="class",
            file_path="core/database/repo.py",
            language="python",
            start_line=1,
            end_line=30,
            content_hash="hash2",
        )
        session.add_all([ui_ent, db_ent])
        await session.commit()
        await session.refresh(ui_ent)
        await session.refresh(db_ent)

        # Add an illegal relationship: UI calls DB directly!
        rel = Relationship(
            project_id=project.id,
            source_entity_id=ui_ent.id,
            target_entity_id=db_ent.id,
            relationship_type="calls",
        )
        session.add(rel)

        # Add an ArchitectureRule: "UI must not access repository directly"
        rule = ArchitectureRule(
            project_id=project.id,
            rule_name="No direct UI to DB access",
            description="UI components must route through API or service layer, not DB repos.",
            forbidden_source_pattern="*apps/web*",
            forbidden_target_pattern="*core/database*",
            severity="CRITICAL",
            enforcement_status="ACTIVE",
        )
        session.add(rule)
        await session.commit()

        # Evaluate rules
        arch_engine = ArchitectureInvariantEngine()
        result = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=True
        )

        assert result.total_rules_evaluated == 1
        assert len(result.violations_detected) == 1
        assert result.has_critical_violations is True
        viol = result.violations_detected[0]
        assert "UserProfileView" in viol["source"]
        assert "UserRepository" in viol["target"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_architecture_modalities():
    """Verify all architecture rule modalities (MUST, MUST_NOT, SHOULD, ONLY_IF, REQUIRES)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        project = Project(
            name="ModalityTestProj", local_path="/tmp/fake", status="READY"
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        # Entities
        controller = CodeEntity(
            project_id=project.id,
            name="UserController",
            qualified_name="apps.api.UserController",
            entity_type="class",
            file_path="apps/api/user_controller.py",
            language="python",
            start_line=1,
            end_line=20,
            content_hash="h1",
        )
        service = CodeEntity(
            project_id=project.id,
            name="UserService",
            qualified_name="services.UserService",
            entity_type="class",
            file_path="services/user_service.py",
            language="python",
            start_line=1,
            end_line=25,
            content_hash="h2",
        )
        db = CodeEntity(
            project_id=project.id,
            name="UserRepository",
            qualified_name="core.database.UserRepository",
            entity_type="class",
            file_path="core/database/repo.py",
            language="python",
            start_line=1,
            end_line=30,
            content_hash="h3",
        )
        legacy = CodeEntity(
            project_id=project.id,
            name="LegacyUtil",
            qualified_name="legacy.LegacyUtil",
            entity_type="class",
            file_path="legacy/old_module.py",
            language="python",
            start_line=1,
            end_line=15,
            content_hash="h4",
        )
        session.add_all([controller, service, db, legacy])
        await session.commit()
        await session.refresh(controller)
        await session.refresh(service)
        await session.refresh(db)
        await session.refresh(legacy)

        arch_engine = ArchitectureInvariantEngine()

        # 1. Test MUST_NOT: controller cannot access database
        rule_must_not = ArchitectureRule(
            project_id=project.id,
            rule_name="Controller cannot call DB",
            description="Controllers MUST_NOT call database directly",
            modality="MUST_NOT",
            forbidden_source_pattern="*apps/api*",
            forbidden_target_pattern="*core/database*",
            severity="CRITICAL",
            enforcement_status="ACTIVE",
        )
        session.add(rule_must_not)
        # Add invalid edge
        rel_bad = Relationship(
            project_id=project.id,
            source_entity_id=controller.id,
            target_entity_id=db.id,
            relationship_type="calls",
        )
        session.add(rel_bad)
        await session.commit()

        res1 = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=False
        )
        assert res1.total_rules_evaluated == 1
        assert len(res1.violations_detected) == 1
        assert res1.has_critical_violations is True
        assert "[MUST_NOT]" in res1.violations_detected[0]["details"]

        # 2. Test ONLY_IF: database can ONLY_IF be accessed by services
        rule_only_if = ArchitectureRule(
            project_id=project.id,
            rule_name="DB accessed only by services",
            description="Database repository may ONLY_IF be accessed by services",
            modality="ONLY_IF",
            forbidden_source_pattern="*services*",
            forbidden_target_pattern="*core/database*",
            severity="ERROR",
            enforcement_status="ACTIVE",
        )
        session.add(rule_only_if)
        await session.commit()

        # Controller -> DB violates ONLY_IF because controller does not match *services*
        res2 = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=False
        )
        assert any("[ONLY_IF]" in v["details"] for v in res2.violations_detected)

        # 3. Test MUST / REQUIRES: controller MUST depend on a service
        rule_must = ArchitectureRule(
            project_id=project.id,
            rule_name="Controller must depend on service",
            description="Every controller MUST route through a service layer",
            modality="MUST",
            forbidden_source_pattern="*apps/api*",
            forbidden_target_pattern="*services*",
            severity="ERROR",
            enforcement_status="ACTIVE",
        )
        session.add(rule_must)
        await session.commit()

        res3 = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=False
        )
        # Controller currently has no edge to service, so MUST triggers a violation
        assert any("[MUST]" in v["details"] for v in res3.violations_detected)

        # Now satisfy MUST by adding controller -> service relationship
        rel_ok = Relationship(
            project_id=project.id,
            source_entity_id=controller.id,
            target_entity_id=service.id,
            relationship_type="calls",
        )
        session.add(rel_ok)
        await session.commit()

        # Remove rule_must_not and rule_only_if to isolate MUST check
        await session.delete(rule_must_not)
        await session.delete(rule_only_if)
        await session.delete(rel_bad)
        await session.commit()

        res4 = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=False
        )
        assert len(res4.violations_detected) == 0  # MUST is now satisfied!

        # 4. Test SHOULD: advisory warning that does not trigger has_critical_violations
        rule_should = ArchitectureRule(
            project_id=project.id,
            rule_name="Discourage legacy utility use",
            description="Services SHOULD avoid calling legacy modules",
            modality="SHOULD",
            forbidden_source_pattern="*services*",
            forbidden_target_pattern="*legacy*",
            severity="WARNING",
            enforcement_status="ACTIVE",
        )
        session.add(rule_should)
        rel_legacy = Relationship(
            project_id=project.id,
            source_entity_id=service.id,
            target_entity_id=legacy.id,
            relationship_type="calls",
        )
        session.add(rel_legacy)
        await session.commit()

        res5 = await arch_engine.evaluate_rules(
            session, project.id, persist_violations=False
        )
        assert len(res5.violations_detected) == 1
        assert res5.violations_detected[0]["severity"] == "WARNING"
        assert res5.has_critical_violations is False
        assert "[SHOULD]" in res5.violations_detected[0]["details"]

    await engine.dispose()

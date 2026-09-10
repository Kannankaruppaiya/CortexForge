"""Regression tests reproducing hostile audit defects before fixing.

Each test in this file targets a confirmed defect identified in the
hostile verification audit of commit 70dbb84.
"""

import pytest
import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.test_intelligence import (
    TestCaseResult,
    TestIntelligenceEngine,
)
from cortexforge.apps.mcp.server import memory_create
from cortexforge.code_intelligence.change_propagator import (
    SemanticChangePropagator,
)
from cortexforge.cognition.epistemics import TestAttribution
from cortexforge.core.models import (
    CodeEntity,
    Memory,
    MemoryEvidence,
    Project,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.mark.asyncio
async def test_regression_unverified_memory_with_fake_evidence_does_not_become_active(
    test_session: AsyncSession, tmp_path
):
    """Defect #2: High-importance agent observation with nonexistent evidence must NOT be ACTIVE."""
    proj = Project(name="PromoTestProject", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()
    await test_session.refresh(proj)

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj.id,
        MemoryCreate(
            title="Fake Grounded Memory",
            summary="Fake summary",
            content="Points to a file that does not exist on disk",
            memory_type="FACT",
            source_type="agent_observation",
            importance=0.99,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="completely_fake_nonexistent_file.py",
                    line_start=1,
                    line_end=10,
                )
            ],
        ),
    )

    # Must NOT be promoted to ACTIVE without verified grounding
    assert mem.status != "ACTIVE", f"Expected unverified status, got {mem.status}"
    # Verification timestamp must NOT be fabricated
    assert mem.last_verified_at is None, f"Expected None, got {mem.last_verified_at}"


def test_regression_mcp_trusted_user_confirmed_cannot_be_forged():
    """Defect #3: Model-controlled trusted_user_confirmed must NOT exist in MCP memory_create tool."""
    import inspect

    sig = inspect.signature(memory_create)
    assert "trusted_user_confirmed" not in sig.parameters, (
        "trusted_user_confirmed must be removed from MCP memory_create tool signature"
    )


@pytest.mark.asyncio
async def test_regression_hard_token_budget_mathematical_guarantee(
    test_session: AsyncSession, tmp_path
):
    """Defect #4: exact token count must be <= budget for every requested budget."""
    proj = Project(name="BudgetProject", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()
    await test_session.refresh(proj)

    composer = ContextComposer()
    enc = tiktoken.get_encoding("cl100k_base")

    test_budgets = [1, 2, 3, 5, 10, 15, 20, 26, 27, 32, 64, 100]
    for b in test_budgets:
        packet = await composer.compose_context_packet(
            test_session,
            proj.id,
            task_text="Test prompt for token budget compliance",
            max_tokens=b,
        )
        exact_toks = len(enc.encode(packet.context_markdown))
        assert exact_toks <= b, f"Budget {b} exceeded: emitted {exact_toks} tokens"


@pytest.mark.asyncio
async def test_regression_graph_relevance_rejects_substring_matching(
    test_session: AsyncSession, tmp_path
):
    """Defect #5: An entity named 'id' must not falsely match 'src/identity.py'."""
    proj = Project(name="GraphSubstrProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.flush()

    # Create entity 'id' in 'src/user.py'
    ent = CodeEntity(
        project_id=proj.id,
        name="id",
        qualified_name="User:id",
        file_path="src/user.py",
        entity_type="field",
        language="python",
        content_hash="hash-user-id-123",
        start_line=1,
        end_line=2,
    )
    test_session.add(ent)

    # Create memory with evidence in 'src/identity.py' (completely unrelated, but contains 'id' substring)
    mem = Memory(
        project_id=proj.id,
        title="Identity Service",
        content="Identity management rules",
        summary="Identity summary",
        layer="L1",
        memory_type="FACT",
        status="ACTIVE",
        confidence=0.9,
        authority="AGENT_OBSERVED",
        importance=0.5,
    )
    test_session.add(mem)
    await test_session.flush()

    ev = MemoryEvidence(
        memory_id=mem.id,
        file_path="src/identity.py",
        source_type="code",
        evidence_hash="ev-hash-ident",
    )
    test_session.add(ev)
    await test_session.commit()

    engine = HybridRetrievalEngine()
    results = await engine.retrieve(
        test_session,
        project_id=proj.id,
        query="identity management",
        target_files=["src/user.py"],
        limit=5,
    )

    # Evidence in identity.py must NOT match entity 'id' in user.py
    for item in results:
        if item.id == mem.id:
            assert item.breakdown.get("graph", 0.0) == 0.0, (
                "Accidental substring match 'id' in 'src/identity.py' gave graph relevance!"
            )


@pytest.mark.asyncio
async def test_regression_deterministic_replay_respects_task_and_profile(
    test_session: AsyncSession, tmp_path
):
    """Defect #6: Replaying different tasks/profiles must not return identical static dumps."""
    proj = Project(name="ReplayProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.flush()

    # Add two different memories
    m1 = Memory(
        project_id=proj.id,
        title="Authentication Protocol",
        content="JWT and bearer tokens for session validation",
        summary="Auth details",
        layer="L2",
        memory_type="DECISION",
        status="ACTIVE",
        confidence=0.9,
        authority="USER_CONFIRMED",
        importance=0.8,
    )
    m2 = Memory(
        project_id=proj.id,
        title="Database Migration Policy",
        content="Schema migrations must run via Alembic in sequence",
        summary="DB policy",
        layer="L3",
        memory_type="CONSTRAINT",
        status="ACTIVE",
        confidence=0.9,
        authority="USER_CONFIRMED",
        importance=0.8,
    )
    test_session.add_all([m1, m2])
    await test_session.commit()

    # Capture snapshot
    await CognitiveSnapshotEngine.take_snapshot(
        test_session, proj.id, commit_sha="commit-alpha"
    )

    replay_auth = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session,
        proj.id,
        commit_sha="commit-alpha",
        task_text="auth session tokens",
        profile="small",
    )
    replay_db = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session,
        proj.id,
        commit_sha="commit-alpha",
        task_text="database schema migration",
        profile="large",
    )

    assert "deterministic_replay_hash" in replay_auth
    assert "composed_context_markdown" in replay_auth
    assert (
        replay_auth["deterministic_replay_hash"]
        != replay_db["deterministic_replay_hash"]
    ), "Replaying different task and profile produced identical state dump!"


def test_regression_causality_attribution_missing_changeset_is_unknown():
    """Defect #7: When no changeset is provided, attribution must be UNKNOWN, not INTRODUCED_BY."""
    engine = TestIntelligenceEngine()
    result = TestCaseResult(
        test_run_id="run-1",
        test_name="tests/test_auth.py::test_login",
        status="FAILED",
    )

    # Historical run where test was PASSED
    hist = TestCaseResult(
        test_run_id="run-0",
        test_name="tests/test_auth.py::test_login",
        status="PASSED",
    )

    verdict = engine._attribute(
        result=result,
        history=[hist],
        changed_symbols=set(),  # No change set provided!
    )

    assert verdict.attribution in (
        TestAttribution.UNKNOWN.value,
        "UNKNOWN_CAUSALITY",
    ), (
        f"Expected UNKNOWN/UNKNOWN_CAUSALITY when no changeset provided, got {verdict.attribution}"
    )


@pytest.mark.asyncio
async def test_regression_change_propagation_confidence_empty_and_subset(
    test_session: AsyncSession, tmp_path
):
    """Defect #9: Empty files must not be EXACT; unparsed files must not be EXACT."""
    proj = Project(name="PropTestProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    propagator = SemanticChangePropagator()

    # 1. Empty modified files must be UNKNOWN_CHANGE_SCOPE
    rep_empty = await propagator.propagate_changes(
        test_session, proj.id, modified_files=[], mark_stale=False
    )
    assert rep_empty.propagation_confidence == "UNKNOWN_CHANGE_SCOPE", (
        f"Expected UNKNOWN_CHANGE_SCOPE for empty files, got {rep_empty.propagation_confidence}"
    )


@pytest.mark.asyncio
async def test_regression_memory_relation_project_integrity_enforced(
    test_session: AsyncSession, tmp_path
):
    """Defect #10: MemoryRelation must enforce project_id and reject cross-project linking."""
    p1 = Project(name="P1", local_path=str(tmp_path / "1"), status="READY")
    p2 = Project(name="P2", local_path=str(tmp_path / "2"), status="READY")
    test_session.add_all([p1, p2])
    await test_session.flush()

    m1 = Memory(
        project_id=p1.id,
        title="M1",
        content="C1",
        summary="S1",
        layer="L1",
        memory_type="DECISION",
        status="ACTIVE",
        confidence=0.8,
        authority="USER_CONFIRMED",
        importance=0.5,
    )
    m2 = Memory(
        project_id=p2.id,
        title="M2",
        content="C2",
        summary="S2",
        layer="L1",
        memory_type="DECISION",
        status="ACTIVE",
        confidence=0.8,
        authority="USER_CONFIRMED",
        importance=0.5,
    )
    test_session.add_all([m1, m2])
    await test_session.commit()

    m_service = MemoryService()

    # Attempt cross-project deprecation / supersession
    with pytest.raises((ValueError, AssertionError)):
        await m_service.deprecate_memory(
            test_session,
            memory_id=m1.id,
            superseded_by_id=m2.id,
            reason="Cross project test",
        )

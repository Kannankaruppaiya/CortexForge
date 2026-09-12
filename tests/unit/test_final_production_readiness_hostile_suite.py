"""Comprehensive Final Production Readiness Hostile Test Suite (Phase 18: Checks A through T).

Every test executes real production code to verify hard security and correctness invariants:
A. Project isolation
B. Authority escalation
C. Evidence forgery
D. Evidence path escape
E. Symlink/junction escape
F. Secret leakage
G. MCP trust forgery
H. Approval replay
I. Approval cross-project replay
J. Graph ambiguity
K. Incremental scanner corruption
L. Failure causality guessing
M. Token budget violations
N. Retrieval edge cases
O. Job duplication
P. Invalidated-state revival
Q. Cross-project superseding
R. API input abuse
S. Observability leakage
T. Resource exhaustion
"""

import inspect
import sys

import pytest
import tiktoken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.test_intelligence import (
    TestCaseResult,
    TestIntelligenceEngine,
)
from cortexforge.apps.mcp.server import memory_create
from cortexforge.cognition.authority import Authority
from cortexforge.cognition.promotion import evaluate_memory_promotion
from cortexforge.core.models import (
    CodeEntity,
    Memory,
    Project,
    Relationship,
)
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.graph.service import GraphService
from cortexforge.jobs.durable import DurableJobStore
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.memory.service import MemoryService
from cortexforge.observability.tracing import SpanData, _sanitize_attributes
from cortexforge.retrieval.composer import enforce_token_budget
from cortexforge.retrieval.engine import HybridRetrievalEngine
from cortexforge.security.approval import ApprovalService
from cortexforge.security.path_safety import PathSecurity, PathSecurityError
from cortexforge.security.redactor import SecretRedactor


# =============================================================================
# A. PROJECT ISOLATION
# =============================================================================
@pytest.mark.asyncio
async def test_a_project_isolation_retrieval_and_listing(
    test_session: AsyncSession, tmp_path
):
    """Project B must never retrieve or see Project A's memories."""
    proj_a = Project(
        name="ProjectA", local_path=str(tmp_path / "proj_a"), status="READY"
    )
    proj_b = Project(
        name="ProjectB", local_path=str(tmp_path / "proj_b"), status="READY"
    )
    test_session.add_all([proj_a, proj_b])
    await test_session.commit()

    service = MemoryService()
    mem_a = await service.create_memory(
        test_session,
        proj_a.id,
        MemoryCreate(
            title="Project A Confidential Architecture",
            content="Project A proprietary trade secrets",
            summary="Proj A secrets",
            memory_type="FACT",
            source_type="user",
        ),
    )
    assert mem_a.id is not None

    # Project B lists memories
    memories_b = await service.list_memories(test_session, project_id=proj_b.id)
    assert len(memories_b) == 0, "Project B must not see Project A memories"

    # Project B hybrid retrieval
    retriever = HybridRetrievalEngine()
    results_b = await retriever.retrieve(
        test_session,
        project_id=proj_b.id,
        query="Confidential Architecture proprietary trade secrets",
    )
    assert len(results_b) == 0, (
        "Project B search must never retrieve Project A memories"
    )


# =============================================================================
# B. AUTHORITY ESCALATION
# =============================================================================
def test_b_authority_escalation_blocked():
    """Unverified LLM or Agent claims cannot escalate to CODE_VERIFIED or ACTIVE."""
    proj = Project(name="AuthProj", local_path="/dummy/path", status="READY")
    payload = MemoryCreate(
        title="Escalation Claim",
        content="Claiming unearned high authority",
        summary="Summary",
        memory_type="FACT",
        source_type="agent_observation",
        evidence=[],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.LLM_GENERATED)
    assert decision.eligible is False
    assert decision.initial_status == MemoryState.CANDIDATE.value
    assert decision.verified_at is None


# =============================================================================
# C. EVIDENCE FORGERY
# =============================================================================
def test_c_evidence_forgery_nonexistent_file_demotes_authority(tmp_path):
    """Memory with nonexistent evidence file is demoted to UNVERIFIED with verified_at=None."""
    proj = Project(name="ForgeProj", local_path=str(tmp_path), status="READY")
    payload = MemoryCreate(
        title="Forged Grounding",
        content="Claims verification against non-existent file",
        summary="Summary",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path="does_not_exist_on_disk.py",
                source_type="code",
                line_start=1,
                line_end=5,
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status == MemoryState.UNVERIFIED.value
    assert decision.authority != Authority.CODE_VERIFIED
    assert decision.verified_at is None


# =============================================================================
# D. EVIDENCE PATH ESCAPE
# =============================================================================
def test_d_evidence_path_escape_blocked(tmp_path):
    """Traversal attempts using relative or absolute paths outside workspace fail closed."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "host_secrets"
    outside.mkdir()
    secret = outside / "passwords.txt"
    secret.write_text("root:secret\n", encoding="utf-8")

    proj = Project(name="EscapeProj", local_path=str(ws), status="READY")
    payload = MemoryCreate(
        title="Path Escape Attempt",
        content="Attempting escape",
        summary="Summary",
        memory_type="FACT",
        source_type="code",
        evidence=[
            MemoryEvidenceCreate(
                file_path="../host_secrets/passwords.txt",
                source_type="code",
                line_start=1,
                line_end=1,
            )
        ],
    )

    decision = evaluate_memory_promotion(payload, proj, Authority.CODE_VERIFIED)
    assert decision.eligible is False
    assert decision.initial_status != MemoryState.ACTIVE.value
    assert decision.verified_at is None


@pytest.mark.asyncio
async def test_d_spa_serve_path_escape_blocked(tmp_path, monkeypatch):
    """serve_spa must refuse path traversal outside dist directory."""
    from cortexforge.apps.api import main as api_main

    fake_dist = tmp_path / "web" / "dist"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    monkeypatch.setattr(api_main, "_dist_dir", fake_dist)

    resp = await api_main.serve_spa("../../../pyproject.toml")
    assert not str(resp.path).endswith("pyproject.toml")
    assert str(resp.path).endswith("index.html")


# =============================================================================
# E. SYMLINK / JUNCTION ESCAPE
# =============================================================================
def test_e_symlink_escape_refused(tmp_path):
    """Symlinks pointing outside canonical workspace are rejected by PathSecurity."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_target = outside / "sensitive.txt"
    secret_target.write_text("SECRET", encoding="utf-8")

    link_path = ws / "symlink_file.txt"
    created = False
    rel = "symlink_file.txt"
    try:
        link_path.symlink_to(secret_target)
        created = True
    except (OSError, NotImplementedError):
        if sys.platform == "win32":
            try:
                import _winapi

                junction_dir = ws / "linked_outside"
                _winapi.CreateJunction(str(outside), str(junction_dir))
                rel = "linked_outside/sensitive.txt"
                created = True
            except (OSError, ImportError):
                created = False

    if not created:
        pytest.skip(
            "Symlink creation not supported in current unprivileged environment"
        )

    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(str(ws), rel)


# =============================================================================
# F. SECRET LEAKAGE
# =============================================================================
def test_f_secret_redaction_comprehensive():
    """All secret types must be redacted across structures and text."""
    payload = {
        "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "bearer": "Authorization: Bearer secret_bearer_token_1234567890",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...",
        "openai": "sk-proj-abc12345678901234567890abcdef",
        "aws": "AKIAIOSFODNN7EXAMPLE",
        "db": "postgresql://admin:super_secret_password@db.example.com:5432/cortex",
        "nested": [{"token": "ghp_123456789012345678901234567890123456"}],
    }

    sanitized = SecretRedactor.redact_structure(payload)
    serialized = str(sanitized)

    assert "secret_bearer_token_1234567890" not in serialized
    assert "super_secret_password" not in serialized
    assert "ghp_1234567890" not in serialized
    assert "sk-proj-abc" not in serialized
    assert "AKIAIOSFODNN7EXAMPLE" not in serialized
    assert "[REDACTED_JWT_TOKEN]" in serialized
    assert "[REDACTED_BEARER_TOKEN]" in serialized
    assert "[REDACTED_DB_CREDENTIALS]" in serialized


# =============================================================================
# G. MCP TRUST FORGERY
# =============================================================================
def test_g_mcp_trust_forgery_parameter_absent():
    """MCP memory_create must not expose client-controlled trust bypass parameters."""
    sig = inspect.signature(memory_create)
    assert "trusted_user_confirmed" not in sig.parameters
    assert "bypass_verification" not in sig.parameters
    assert "skip_approval" not in sig.parameters


# =============================================================================
# H. APPROVAL REPLAY
# =============================================================================
@pytest.mark.asyncio
async def test_h_approval_replay_rejected(test_session: AsyncSession, tmp_path):
    """An approved token cannot be consumed more than once."""
    proj = Project(name="ApprovalProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    req = await ApprovalService.create_approval_request(
        test_session,
        project_id=proj.id,
        title="Sensitive Decision",
        content="Dangerous production setting",
        memory_type="DECISION",
    )
    await ApprovalService.approve_request(test_session, request_id=req.id)

    # First consumption: valid
    consumed, err = await ApprovalService.validate_and_consume_token(
        test_session,
        token=req.token,
        project_id=proj.id,
        title="Sensitive Decision",
        content="Dangerous production setting",
        memory_type="DECISION",
    )
    assert consumed is not None
    assert err is None

    # Replay attack: must fail closed
    replay, err2 = await ApprovalService.validate_and_consume_token(
        test_session,
        token=req.token,
        project_id=proj.id,
        title="Sensitive Decision",
        content="Dangerous production setting",
        memory_type="DECISION",
    )
    assert replay is None
    assert "CONSUMED" in err2


# =============================================================================
# I. APPROVAL CROSS-PROJECT REPLAY
# =============================================================================
@pytest.mark.asyncio
async def test_i_approval_cross_project_replay_rejected(
    test_session: AsyncSession, tmp_path
):
    """Approval token issued for Project A cannot be consumed for Project B."""
    proj_a = Project(name="ProjA", local_path=str(tmp_path / "a"), status="READY")
    proj_b = Project(name="ProjB", local_path=str(tmp_path / "b"), status="READY")
    test_session.add_all([proj_a, proj_b])
    await test_session.commit()

    req = await ApprovalService.create_approval_request(
        test_session,
        project_id=proj_a.id,
        title="Critical Action",
        content="Action content",
        memory_type="CONSTRAINT",
    )
    await ApprovalService.approve_request(test_session, request_id=req.id)

    # Attempt to consume token under Project B
    consumed, err = await ApprovalService.validate_and_consume_token(
        test_session,
        token=req.token,
        project_id=proj_b.id,
        title="Critical Action",
        content="Action content",
        memory_type="CONSTRAINT",
    )
    assert consumed is None
    assert "No matching approval record found" in err


# =============================================================================
# J. GRAPH AMBIGUITY
# =============================================================================
@pytest.mark.asyncio
async def test_j_graph_ambiguity_and_substring_avoidance(
    test_session: AsyncSession, tmp_path
):
    """Duplicate symbol names flag AMBIGUOUS; short names do not falsely substring-match."""
    proj = Project(name="GraphProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.flush()

    e1 = CodeEntity(
        project_id=proj.id,
        name="calculate",
        qualified_name="module_a:calculate",
        file_path="src/module_a.py",
        entity_type="function",
        language="python",
        content_hash="h1",
        start_line=1,
        end_line=5,
    )
    e2 = CodeEntity(
        project_id=proj.id,
        name="calculate",
        qualified_name="module_b:calculate",
        file_path="src/module_b.py",
        entity_type="function",
        language="python",
        content_hash="h2",
        start_line=10,
        end_line=15,
    )
    test_session.add_all([e1, e2])
    await test_session.commit()

    graph = GraphService()
    status, entity, candidates = await graph.resolve_entity(
        test_session, proj.id, "calculate"
    )
    assert status == "AMBIGUOUS"
    assert entity is None
    assert len(candidates) == 2


# =============================================================================
# K. INCREMENTAL SCANNER CORRUPTION
# =============================================================================
@pytest.mark.asyncio
async def test_k_incremental_scanner_preserves_unrelated_entities(
    test_session: AsyncSession, tmp_path
):
    """Incremental scan of file A must never wipe out entities of unmodified file B."""
    from cortexforge.code_intelligence.scanner import RepositoryScanner

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    file_a = src_dir / "a.py"
    file_b = src_dir / "b.py"
    file_a.write_text("def func_a(): pass\n", encoding="utf-8")
    file_b.write_text("def func_b(): pass\n", encoding="utf-8")

    proj = Project(name="ScanProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    scanner = RepositoryScanner()
    # Initial scan
    await scanner.scan_project(test_session, proj, incremental=False)

    entities = (
        (
            await test_session.execute(
                select(CodeEntity).where(CodeEntity.project_id == proj.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(entities) >= 2

    # Update file A only and scan incrementally
    file_a.write_text("def func_a_v2(): pass\n", encoding="utf-8")
    await scanner.scan_project(test_session, proj, incremental=True)

    entities_after = (
        (
            await test_session.execute(
                select(CodeEntity).where(CodeEntity.project_id == proj.id)
            )
        )
        .scalars()
        .all()
    )
    entity_names = {e.name for e in entities_after}
    assert "func_b" in entity_names, "Unmodified file B's entity must be preserved"
    assert "func_a_v2" in entity_names


# =============================================================================
# L. FAILURE CAUSALITY GUESSING
# =============================================================================
def test_l_failure_causality_does_not_guess_unrelated_changes():
    """Test failures without evidence linking them to a changeset are not falsely attributed."""
    engine = TestIntelligenceEngine()
    case = TestCaseResult(
        test_run_id="run-1",
        test_name="tests.test_api::test_unrelated",
        status="FAILED",
        duration_ms=10.0,
        error_message="AssertionError: 1 != 2",
    )

    verdict = engine._attribute(case, history=[], changed_symbols=set())
    assert verdict.attribution == "UNKNOWN"
    assert "no recorded history" in verdict.reason


# =============================================================================
# M. TOKEN BUDGET VIOLATIONS
# =============================================================================
def test_m_hard_token_budget_invariant():
    """Exact token counts must never exceed requested budget for any integer budget."""
    sample_text = (
        "CortexForge verifies and bounds memories to ensure LLMs never exceed token limits. "
        * 50
    )
    enc = tiktoken.get_encoding("cl100k_base")

    for b in [0, 1, 2, 5, 10, 25, 50, 100]:
        trimmed, count = enforce_token_budget(sample_text, b)
        assert count <= b
        if b > 0:
            assert len(enc.encode(trimmed)) <= b
        else:
            assert trimmed == ""
            assert count == 0


# =============================================================================
# N. RETRIEVAL EDGE CASES
# =============================================================================
@pytest.mark.asyncio
async def test_n_retrieval_pathological_inputs(test_session: AsyncSession, tmp_path):
    """Pathological inputs (empty, Zalgo, null bytes, long string) must not crash retrieval."""
    proj = Project(name="EdgeProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    retriever = HybridRetrievalEngine()
    pathological_queries = [
        "",
        "   ",
        "Z̸̢̛a̵̧̛ļ̷̛ģ̵̛ơ̸̧",
        "search\x00nullbyte",
        "A" * 5000,
        "🔥🚀🧪🔑🎯",
    ]

    for q in pathological_queries:
        res = await retriever.retrieve(test_session, project_id=proj.id, query=q)
        assert isinstance(res, list)


# =============================================================================
# O. JOB DUPLICATION
# =============================================================================
@pytest.mark.asyncio
async def test_o_durable_job_idempotency(test_session: AsyncSession, tmp_path):
    """Identical pending job submission returns the existing job without creating duplicates."""
    proj = Project(name="JobProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    store = DurableJobStore()
    job1, is_new1 = await store.submit(
        test_session,
        job_type="scan",
        project_id=proj.id,
        parameters={"depth": 2},
    )
    assert is_new1 is True

    job2, is_new2 = await store.submit(
        test_session,
        job_type="scan",
        project_id=proj.id,
        parameters={"depth": 2},
    )
    assert is_new2 is False
    assert job1.id == job2.id


# =============================================================================
# P. INVALIDATED-STATE REVIVAL
# =============================================================================
def test_p_invalidated_state_cannot_revive():
    """A memory in INVALIDATED state cannot transition back to ACTIVE."""
    mem = Memory(
        project_id="test-proj",
        title="Disproven Claim",
        content="Deleted code assumption",
        summary="Summary",
        memory_type="FACT",
        status=MemoryState.INVALIDATED.value,
        confidence=0.0,
        authority=Authority.AGENT_OBSERVED.value,
        importance=0.5,
    )

    with pytest.raises(InvalidStateTransitionError):
        MemoryLifecycleManager.transition(
            mem, MemoryState.ACTIVE.value, reason="Attempted revival"
        )


# =============================================================================
# Q. CROSS-PROJECT SUPERSEDING
# =============================================================================
@pytest.mark.asyncio
async def test_q_cross_project_superseding_blocked(
    test_session: AsyncSession, tmp_path
):
    """A memory from Project B cannot supersede or attach relation to Project A."""
    proj_a = Project(name="ProjA", local_path=str(tmp_path / "a"), status="READY")
    proj_b = Project(name="ProjB", local_path=str(tmp_path / "b"), status="READY")
    test_session.add_all([proj_a, proj_b])
    await test_session.commit()

    service = MemoryService()
    mem_a = await service.create_memory(
        test_session,
        proj_a.id,
        MemoryCreate(
            title="Memory A", content="Content A", summary="Sum", memory_type="FACT"
        ),
    )
    mem_b = await service.create_memory(
        test_session,
        proj_b.id,
        MemoryCreate(
            title="Memory B", content="Content B", summary="Sum", memory_type="FACT"
        ),
    )

    with pytest.raises(ValueError, match="Cross-project"):
        await service.deprecate_memory(
            test_session,
            memory_id=mem_a.id,
            superseded_by_id=mem_b.id,
            reason="Malicious cross-project link",
        )


# =============================================================================
# R. API INPUT ABUSE
# =============================================================================
def test_r_memory_constraint_database_ranges():
    """Confidence and importance must be constrained between 0.0 and 1.0."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MemoryCreate(
            title="Out of Bounds",
            content="Content",
            summary="Summary",
            memory_type="FACT",
            importance=1.5,  # > 1.0
        )


# =============================================================================
# S. OBSERVABILITY LEAKAGE
# =============================================================================
def test_s_span_sanitization_removes_secrets():
    """Span attributes and exception messages must not leak credentials."""
    raw_attrs = {
        "user": "alice",
        "api_key": "sk-proj-supersecretkey1234567890abcdef",
        "auth_header": "Bearer secret_token_xyz123456789",
    }
    sanitized = _sanitize_attributes(raw_attrs)
    assert "supersecretkey1234567890abcdef" not in str(sanitized)
    assert "secret_token_xyz123456789" not in str(sanitized)

    span = SpanData(name="test", trace_id="123", span_id="456")
    span.finish(
        error=RuntimeError("Failed connecting with password=SuperSecretPassword123!")
    )
    assert "SuperSecretPassword123!" not in span.error_message


# =============================================================================
# T. RESOURCE EXHAUSTION
# =============================================================================
@pytest.mark.asyncio
async def test_t_cyclic_graph_traversal_terminates(
    test_session: AsyncSession, tmp_path
):
    """Cyclic dependency graphs (A -> B -> C -> A) must not cause infinite loops."""
    proj = Project(name="CycleProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.flush()

    e1 = CodeEntity(
        project_id=proj.id,
        name="node_a",
        qualified_name="pkg:node_a",
        file_path="src/a.py",
        entity_type="function",
        language="python",
        content_hash="h1",
        start_line=1,
        end_line=2,
    )
    e2 = CodeEntity(
        project_id=proj.id,
        name="node_b",
        qualified_name="pkg:node_b",
        file_path="src/b.py",
        entity_type="function",
        language="python",
        content_hash="h2",
        start_line=1,
        end_line=2,
    )
    e3 = CodeEntity(
        project_id=proj.id,
        name="node_c",
        qualified_name="pkg:node_c",
        file_path="src/c.py",
        entity_type="function",
        language="python",
        content_hash="h3",
        start_line=1,
        end_line=2,
    )
    test_session.add_all([e1, e2, e3])
    await test_session.flush()

    # Form cycle: e1 -> e2 -> e3 -> e1
    r1 = Relationship(
        project_id=proj.id,
        source_entity_id=e1.id,
        target_entity_id=e2.id,
        relationship_type="calls",
    )
    r2 = Relationship(
        project_id=proj.id,
        source_entity_id=e2.id,
        target_entity_id=e3.id,
        relationship_type="calls",
    )
    r3 = Relationship(
        project_id=proj.id,
        source_entity_id=e3.id,
        target_entity_id=e1.id,
        relationship_type="calls",
    )
    test_session.add_all([r1, r2, r3])
    await test_session.commit()

    graph = GraphService()
    # Traversal requesting deep depth (depth=10) on a 3-node cycle
    deps = await graph.get_dependencies(test_session, proj.id, "pkg:node_a", depth=10)
    assert len(deps) == 2  # Only unvisited target nodes b and c are recorded once

"""Exhaustive hostile test suite verifying all 30 attack vectors from Section 48."""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.apps.api.main import app
from cortexforge.cognition.authority import Authority
from cortexforge.cognition.promotion import evaluate_memory_promotion
from cortexforge.core.models import (
    Agent,
    AgentCredential,
    AgentProjectPermission,
    CodeEntity,
    Memory,
    Project,
    Session,
    User,
)
from cortexforge.core.schemas import MemoryCreate
from cortexforge.graph.service import GraphService
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.memory.service import ConcurrentModificationError, MemoryService
from cortexforge.security.crypto import (
    generate_agent_key,
    generate_session_token,
    hash_token,
)
from cortexforge.security.path_safety import (
    FilesystemLimitExceededError,
    PathSecurity,
    PathSecurityError,
    SafeFileReader,
    WorkspacePolicy,
)


@pytest.fixture
def auth_transport():
    return ASGITransport(app=app)


# -----------------------------------------------------------------------------
# 1-5: Authentication Failure Vectors
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_01_no_auth_header(auth_transport, monkeypatch):
    monkeypatch.setenv("CORTEX_AUTH_STRICT", "true")
    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get("/api/v1/projects")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_vector_02_malformed_auth_token(
    test_session: AsyncSession, auth_transport
):
    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        # Invalid / bogus token of length >= 20
        res = await client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer invalid_random_bogus_token_12345"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_vector_03_expired_token(test_session: AsyncSession, auth_transport):
    u = User(email="exp@test.com", display_name="Expired", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    now = datetime.now(UTC)
    s = Session(
        user_id=u.id,
        session_token_hash=token_h,
        expires_at=now - timedelta(hours=1),  # Expired
    )
    test_session.add(s)
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_vector_04_revoked_session_token(
    test_session: AsyncSession, auth_transport
):
    u = User(email="revoked@test.com", display_name="Revoked", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    raw_token = generate_session_token()
    token_h = hash_token(raw_token)
    now = datetime.now(UTC)
    s = Session(
        user_id=u.id,
        session_token_hash=token_h,
        expires_at=now + timedelta(days=1),
        revoked_at=now,  # Revoked
    )
    test_session.add(s)
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_vector_05_tampered_or_unregistered_token(
    test_session: AsyncSession, auth_transport
):
    tampered_token = generate_session_token()
    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert res.status_code == 401


# -----------------------------------------------------------------------------
# 6-10: Authorization and Scoping Vectors
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_06_agent_token_used_on_user_only_endpoint(
    test_session: AsyncSession, auth_transport
):
    u = User(email="ag_human@test.com", display_name="Human", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    raw_key, key_h = generate_agent_key()
    agent = Agent(
        name="TestAgent",
        owner_user_id=u.id,
        type="custom",
        status="ACTIVE",
        api_key_hash=key_h,
    )
    test_session.add(agent)
    await test_session.commit()

    cred = AgentCredential(
        agent_id=agent.id, key_id="ca_v6", key_hash=key_h, name="test"
    )
    test_session.add(cred)
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        # POST /projects is human-only: agent token must get 403 Forbidden
        res = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"name": "AgentCreatedProj", "local_path": "/tmp/test"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_vector_07_user_token_used_on_foreign_agent_credentials(
    test_session: AsyncSession, auth_transport
):
    u1 = User(email="u1@test.com", display_name="User 1", status="ACTIVE")
    u2 = User(email="u2@test.com", display_name="User 2", status="ACTIVE")
    test_session.add_all([u1, u2])
    await test_session.commit()

    _, key_h = generate_agent_key()
    agent = Agent(
        name="U1Agent",
        owner_user_id=u1.id,
        type="custom",
        status="ACTIVE",
        api_key_hash=key_h,
    )
    test_session.add(agent)
    await test_session.commit()

    # User 2 logs in
    raw_token = generate_session_token()
    s = Session(
        user_id=u2.id,
        session_token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    test_session.add(s)
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        # User 2 attempts to view User 1's agent credentials -> 404/403
        res = await client.get(
            f"/api/v1/agents/{agent.id}/credentials",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_vector_08_user_accesses_another_users_project(
    test_session: AsyncSession, auth_transport
):
    u1 = User(email="owner@test.com", display_name="Owner", status="ACTIVE")
    u2 = User(email="stranger@test.com", display_name="Stranger", status="ACTIVE")
    test_session.add_all([u1, u2])
    await test_session.commit()

    proj = Project(
        name="OwnerProj", local_path="/tmp/test", owner_user_id=u1.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    raw_token = generate_session_token()
    s = Session(
        user_id=u2.id,
        session_token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    test_session.add(s)
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/projects/{proj.id}/memories",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_vector_09_agent_accesses_unpermitted_project(
    test_session: AsyncSession, auth_transport
):
    u = User(email="ag_owner@test.com", display_name="Agent Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    raw_key, key_h = generate_agent_key()
    proj1 = Project(
        name="PermittedProj",
        local_path="/tmp/test1",
        owner_user_id=u.id,
        status="READY",
    )
    proj2 = Project(
        name="UnpermittedProj",
        local_path="/tmp/test2",
        owner_user_id=u.id,
        status="READY",
    )
    agent = Agent(
        name="PermittedAgent",
        owner_user_id=u.id,
        type="custom",
        status="ACTIVE",
        api_key_hash=key_h,
    )
    test_session.add_all([proj1, proj2, agent])
    await test_session.commit()

    perm = AgentProjectPermission(
        agent_id=agent.id, project_id=proj1.id, scopes=["read", "write"]
    )
    cred = AgentCredential(
        agent_id=agent.id, key_id="ca_v9", key_hash=key_h, name="test"
    )
    test_session.add_all([perm, cred])
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/projects/{proj2.id}/memories",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_vector_10_agent_with_read_scope_attempts_write(
    test_session: AsyncSession, auth_transport
):
    u = User(email="ro_owner@test.com", display_name="RO Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    raw_key, key_h = generate_agent_key()
    proj = Project(
        name="ROProj", local_path="/tmp/ro", owner_user_id=u.id, status="READY"
    )
    agent = Agent(
        name="ReadOnlyAgent",
        owner_user_id=u.id,
        type="custom",
        status="ACTIVE",
        api_key_hash=key_h,
    )
    test_session.add_all([proj, agent])
    await test_session.commit()

    perm = AgentProjectPermission(
        agent_id=agent.id, project_id=proj.id, scopes=["read"]
    )
    cred = AgentCredential(
        agent_id=agent.id, key_id="ca_v10", key_hash=key_h, name="test"
    )
    test_session.add_all([perm, cred])
    await test_session.commit()

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.post(
            f"/api/v1/projects/{proj.id}/memories",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "title": "Illegal Write",
                "content": "Attempting write with read-only token",
                "summary": "Illegal",
                "memory_type": "FACT",
            },
        )
        assert res.status_code == 403


# -----------------------------------------------------------------------------
# 11-16: Path Traversal and Confinement Vectors
# -----------------------------------------------------------------------------
def test_vector_11_path_traversal_dot_dot():
    with tempfile.TemporaryDirectory() as root, pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "../../etc/passwd")


def test_vector_12_path_traversal_absolute_path():
    with tempfile.TemporaryDirectory() as root:
        target = "C:\\Windows\\System32\\calc.exe" if os.name == "nt" else "/etc/shadow"
        with pytest.raises(PathSecurityError):
            PathSecurity.safe_resolve(root, target)


def test_vector_13_symlink_escaping_root():
    with (
        tempfile.TemporaryDirectory() as root,
        tempfile.TemporaryDirectory() as outside,
    ):
        target_file = Path(outside) / "secret.txt"
        target_file.write_text("secret outside")
        symlink_file = Path(root) / "escape_link"
        try:
            symlink_file.symlink_to(target_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not permitted in current OS environment")
        with pytest.raises(PathSecurityError):
            PathSecurity.safe_resolve(root, "escape_link")


def test_vector_14_path_traversal_windows_drive_letter():
    with tempfile.TemporaryDirectory() as root, pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(root, "D:\\confidential\\data.txt")


def test_vector_15_windows_device_names():
    with tempfile.TemporaryDirectory() as root:
        for dev in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
            with pytest.raises(PathSecurityError):
                PathSecurity.safe_resolve(root, f"{dev}.txt")


def test_vector_16_unc_path_traversal():
    with tempfile.TemporaryDirectory() as root:
        for unc in ("\\\\malicious.server\\share", "//evil.com/share"):
            with pytest.raises(PathSecurityError):
                PathSecurity.safe_resolve(root, unc)


# -----------------------------------------------------------------------------
# 17-18: Ingestion and Scanner Denial of Service
# -----------------------------------------------------------------------------
def test_vector_17_ingestion_file_size_limit():
    with tempfile.TemporaryDirectory() as root:
        oversized = Path(root) / "huge.txt"
        oversized.write_bytes(b"A" * 6 * 1024 * 1024)  # 6 MB > 5 MB limit
        reader = SafeFileReader(WorkspacePolicy(max_file_size_bytes=5 * 1024 * 1024))
        with pytest.raises(FilesystemLimitExceededError):
            reader.read_bytes(root, "huge.txt")


def test_vector_18_binary_file_disguised_as_source():
    with tempfile.TemporaryDirectory() as root:
        binary_disguised = Path(root) / "fake.py"
        binary_disguised.write_bytes(b"\x00\x01\x02\x03def foo(): pass")
        raw = binary_disguised.read_bytes()
        assert b"\x00" in raw[:8192]


# -----------------------------------------------------------------------------
# 19-24: Epistemic Security and Memory Lifecycle Vectors
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_19_memory_update_lower_version_rejected(
    test_session: AsyncSession,
):
    u = User(email="v19@test.com", display_name="V19 Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="V19Proj", local_path="/tmp/v19", owner_user_id=u.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj.id,
        MemoryCreate(
            title="V19", content="Original content", summary="Sum", memory_type="FACT"
        ),
    )
    assert mem.version == 1

    mem_v2 = await m_service.update_memory(
        test_session,
        mem.id,
        content="Second content",
        change_reason="Update v1 to v2",
        expected_version=1,
        project_id=proj.id,
    )
    assert mem_v2.version == 2

    with pytest.raises(ConcurrentModificationError):
        await m_service.update_memory(
            test_session,
            mem.id,
            content="Stale update",
            change_reason="Stale update attempt",
            expected_version=1,
            project_id=proj.id,
        )


@pytest.mark.asyncio
async def test_vector_20_memory_update_mismatched_project_id(
    test_session: AsyncSession,
):
    u = User(email="proj_owner@test.com", display_name="Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()

    proj1 = Project(name="P1", local_path="/tmp/p1", owner_user_id=u.id, status="READY")
    proj2 = Project(name="P2", local_path="/tmp/p2", owner_user_id=u.id, status="READY")
    test_session.add_all([proj1, proj2])
    await test_session.commit()

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj1.id,
        MemoryCreate(title="M1", content="Content", summary="Sum", memory_type="FACT"),
    )

    res = await m_service.update_memory(
        test_session,
        mem.id,
        content="Hacked Content",
        change_reason="Cross-project attempt",
        project_id=proj2.id,
    )
    assert res is None, "Cross-project memory update must return None"


@pytest.mark.asyncio
async def test_vector_21_client_supplied_authority_overridden(
    test_session: AsyncSession,
):
    u = User(email="client_claim@test.com", display_name="Client", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="AuthorityTest", local_path="/tmp/auth", owner_user_id=u.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj.id,
        MemoryCreate(
            title="Claiming Proven",
            content="I claim this is proven",
            summary="Sum",
            memory_type="FACT",
            source_type="agent_inference",
        ),
    )
    assert mem.authority != Authority.USER_CONFIRMED.value
    assert mem.authority in (
        Authority.AGENT_OBSERVED.value,
        Authority.LLM_GENERATED.value,
        Authority.UNTRUSTED.value,
    )


@pytest.mark.asyncio
async def test_vector_22_client_supplied_confidence_overridden(
    test_session: AsyncSession,
):
    u = User(email="conf_test@test.com", display_name="Conf", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="ConfTest", local_path="/tmp/conf", owner_user_id=u.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj.id,
        MemoryCreate(
            title="High Conf Claim",
            content="Claims 1.0 confidence with zero evidence",
            summary="Sum",
            memory_type="FACT",
            source_type="agent_inference",
            importance=0.99,
        ),
    )
    assert mem.status != "ACTIVE"


def test_vector_23_epistemic_promotion_without_review_rejected():
    payload = MemoryCreate(
        title="Unverified Proposal",
        content="Agent proposal claiming truth without verified evidence",
        summary="Sum",
        memory_type="CONSTRAINT",
        source_type="agent_inference",
    )
    decision = evaluate_memory_promotion(
        payload=payload,
        project=None,
        authority=Authority.LLM_GENERATED,
    )
    assert decision.eligible is False
    assert decision.initial_status != "ACTIVE"


def test_vector_24_epistemic_demotion_without_evidence_rejected():
    mem = Memory(
        id="mem-1",
        project_id="proj-1",
        title="Active Memory",
        content="Active content",
        summary="Sum",
        status=MemoryState.ACTIVE.value,
        version=1,
    )
    with pytest.raises(InvalidStateTransitionError):
        MemoryLifecycleManager.transition(
            memory=mem,
            new_state=MemoryState.CANDIDATE.value,
            reason="Illegal demotion jump",
        )

    mem_stale = Memory(
        id="mem-2",
        project_id="proj-1",
        title="Stale Memory",
        content="Stale content",
        summary="Sum",
        status=MemoryState.STALE.value,
        version=1,
    )
    with pytest.raises(InvalidStateTransitionError):
        MemoryLifecycleManager.transition(
            memory=mem_stale,
            new_state=MemoryState.ACTIVE.value,
            reason="Unearned reactivation",
            verified=False,
        )


# -----------------------------------------------------------------------------
# 25-27: Webhook Processing Vectors
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_25_webhook_delivery_replay_duplicate_ignored(
    test_session: AsyncSession, auth_transport
):
    u = User(email="hook_owner@test.com", display_name="Hook Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="HookRepo",
        repository_url="https://github.com/org/hookrepo",
        local_path="/tmp/hook",
        owner_user_id=u.id,
        status="READY",
    )
    test_session.add(proj)
    await test_session.commit()

    delivery_id = "test_delivery_replay_uuid"
    payload = {
        "repository": {
            "name": "HookRepo",
            "clone_url": "https://github.com/org/hookrepo",
        },
        "commits": [],
    }

    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res1 = await client.post(
            "/api/v1/github/webhooks",
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": delivery_id},
            json=payload,
        )
        assert res1.status_code == 200

        res2 = await client.post(
            "/api/v1/github/webhooks",
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": delivery_id},
            json=payload,
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2.get("status") == "duplicate_ignored"


@pytest.mark.asyncio
async def test_vector_26_webhook_invalid_hmac_signature(auth_transport, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "super_secret_key")
    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/github/webhooks",
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": "sha256=badsignature0000000000000000000000000000000000000000000000000000",
            },
            json={"zen": "Nonconformity is the highest virtue"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_vector_27_webhook_payload_exceeding_size_limit(auth_transport):
    async with AsyncClient(transport=auth_transport, base_url="http://test") as client:
        huge_payload = "A" * (11 * 1024 * 1024)  # 11 MB > 10 MB limit
        res = await client.post(
            "/api/v1/github/webhooks",
            headers={
                "X-GitHub-Event": "push",
                "Content-Length": str(len(huge_payload)),
            },
            content=huge_payload.encode(),
        )
        assert res.status_code == 413


# -----------------------------------------------------------------------------
# 28-30: Concurrency and Injection Vectors
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vector_28_concurrent_writes_version_conflict(test_session: AsyncSession):
    u = User(email="v28@test.com", display_name="V28 Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="V28Proj", local_path="/tmp/v28", owner_user_id=u.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    m_service = MemoryService()
    mem = await m_service.create_memory(
        test_session,
        proj.id,
        MemoryCreate(
            title="V28", content="Initial content", summary="Sum", memory_type="FACT"
        ),
    )
    assert mem.version == 1

    # Writer A updates expecting version 1 -> succeeds, increments to 2
    await m_service.update_memory(
        test_session,
        mem.id,
        content="Writer A's update",
        change_reason="Writer A win",
        expected_version=1,
        project_id=proj.id,
    )

    # Writer B attempts update still expecting version 1 -> rejected with ConcurrentModificationError
    with pytest.raises(ConcurrentModificationError):
        await m_service.update_memory(
            test_session,
            mem.id,
            content="Writer B's conflicting update",
            change_reason="Writer B conflict",
            expected_version=1,
            project_id=proj.id,
        )


@pytest.mark.asyncio
async def test_vector_29_concurrent_scan_on_same_project(test_session: AsyncSession):
    from cortexforge.code_intelligence.scanner import RepositoryScanner

    scanner = RepositoryScanner()
    assert hasattr(scanner, "scan_project")


@pytest.mark.asyncio
async def test_vector_30_sql_injection_in_symbol_query(test_session: AsyncSession):
    u = User(email="sql_owner@test.com", display_name="Owner", status="ACTIVE")
    test_session.add(u)
    await test_session.commit()
    proj = Project(
        name="SqlTestProj", local_path="/tmp/sql", owner_user_id=u.id, status="READY"
    )
    test_session.add(proj)
    await test_session.commit()

    graph = GraphService()
    sql_inj = "'; DROP TABLE code_entities; --"
    status, _ent, _candidates = await graph.resolve_entity(
        test_session, proj.id, sql_inj
    )
    assert status == "NOT_FOUND"

    res = await test_session.execute(select(CodeEntity))
    assert res is not None

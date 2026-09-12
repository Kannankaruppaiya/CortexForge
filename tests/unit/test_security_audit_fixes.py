"""Comprehensive Zero-Trust verification tests for P0, P1, and P2 security audit fixes."""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.code_intelligence.git_service import validate_git_url
from cortexforge.core.db import init_db, is_managed_environment
from cortexforge.core.models import Project, ProjectMembership, User


@pytest.fixture(autouse=True)
async def ensure_db():
    await init_db()
from mcp.shared.auth import OAuthClientInformationFull

from cortexforge.llm.provider import GeminiProvider
from cortexforge.security.auth import (
    Principal,
    assert_startup_auth_safety,
    get_current_principal,
    validate_runtime_environment,
)
from cortexforge.security.crypto import decrypt_token, encrypt_token
from cortexforge.security.mcp_auth import CortexForgeOAuthProvider
from cortexforge.security.policy import Permission, ProjectRole
from cortexforge.security.rate_limiter import SlidingWindowRateLimiter


# ==============================================================================
# 1. P0: Project Membership Privilege Escalation & Ownership Transfer
# ==============================================================================
def test_project_membership_permissions_policy():
    """Verify PROJECT_MEMBERS_MANAGE is granted only to OWNER and ADMIN, not MEMBER or VIEWER."""
    from cortexforge.security.policy import ROLE_PERMISSIONS

    assert Permission.PROJECT_MEMBERS_MANAGE in ROLE_PERMISSIONS[ProjectRole.OWNER]
    assert Permission.PROJECT_MEMBERS_MANAGE in ROLE_PERMISSIONS[ProjectRole.ADMIN]
    assert Permission.PROJECT_MEMBERS_MANAGE not in ROLE_PERMISSIONS[ProjectRole.MEMBER]
    assert Permission.PROJECT_MEMBERS_MANAGE not in ROLE_PERMISSIONS[ProjectRole.VIEWER]


@pytest.mark.asyncio
async def test_member_cannot_manage_memberships(test_session):
    """Verify normal MEMBER cannot add or update memberships (HTTP 403)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create project
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        project = Project(
            id=pid, name="RBAC Test", owner_user_id="user_owner", local_path="/tmp/test_rbac"
        )
        test_session.add(project)
        await test_session.commit()

        # Caller is a normal MEMBER
        member_principal = Principal(
            principal_id="user_member",
            user_id="user_member",
            actor_type="USER",
            allowed_project_ids={pid},
            project_roles={pid: "MEMBER"},
            is_admin=False,
        )

        app.dependency_overrides[get_current_principal] = lambda: member_principal
        try:
            # 1. Member tries to add a new member
            res = await client.post(
                f"/api/v1/projects/{pid}/members",
                json={"user_id": "user_target", "role": "OWNER"},
            )
            assert res.status_code == 403
            assert "project.members.manage" in res.json()["detail"]

            # 2. Member tries to update a member
            res = await client.put(
                f"/api/v1/projects/{pid}/members/user_target",
                json={"role": "ADMIN"},
            )
            assert res.status_code == 403

            # 3. Member tries to remove a member
            res = await client.delete(f"/api/v1/projects/{pid}/members/user_target")
            assert res.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_admin_cannot_escalate_to_owner_or_admin(test_session):
    """Verify ADMIN cannot grant OWNER or ADMIN roles to users."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        project = Project(
            id=pid, name="Escalation Test", owner_user_id="user_owner", local_path="/tmp/test_esc"
        )
        test_session.add(project)
        await test_session.commit()

        admin_principal = Principal(
            principal_id="user_admin",
            user_id="user_admin",
            actor_type="USER",
            allowed_project_ids={pid},
            project_roles={pid: "ADMIN"},
            is_admin=False,
        )

        app.dependency_overrides[get_current_principal] = lambda: admin_principal
        try:
            # 1. Admin attempts to grant OWNER
            res = await client.post(
                f"/api/v1/projects/{pid}/members",
                json={"user_id": "user_target1", "role": "OWNER"},
            )
            assert res.status_code in (400, 403)
            assert "Cannot assign OWNER role directly" in res.json()["detail"]

            # 2. Admin attempts to grant ADMIN
            res = await client.post(
                f"/api/v1/projects/{pid}/members",
                json={"user_id": "user_target2", "role": "ADMIN"},
            )
            assert res.status_code == 403
            assert "cannot grant ADMIN" in res.json()["detail"]

            # 3. Admin successfully adds MEMBER
            target_user = User(id="user_target3", email="t3@test.com", display_name="Target 3")
            test_session.add(target_user)
            await test_session.commit()

            res = await client.post(
                f"/api/v1/projects/{pid}/members",
                json={"user_id": "user_target3", "role": "MEMBER"},
            )
            assert res.status_code == 201
            assert res.json()["role"] == "MEMBER"
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_explicit_ownership_transfer(test_session):
    """Verify atomic ownership transfer endpoint with role demotion and owner re-assignment."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        owner_id = "user_original_owner"
        target_id = "user_new_owner"

        user1 = User(id=owner_id, email="owner@test.com", display_name="Owner")
        user2 = User(id=target_id, email="target@test.com", display_name="Target")
        project = Project(
            id=pid, name="Transfer Project", owner_user_id=owner_id, local_path="/tmp/test_trans"
        )
        membership2 = ProjectMembership(project_id=pid, user_id=target_id, role="MEMBER")

        test_session.add_all([user1, user2, project, membership2])
        await test_session.commit()

        # Non-owner cannot transfer
        non_owner = Principal(
            principal_id=target_id,
            user_id=target_id,
            allowed_project_ids={pid},
            project_roles={pid: "MEMBER"},
        )
        app.dependency_overrides[get_current_principal] = lambda: non_owner
        try:
            res = await client.post(
                f"/api/v1/projects/{pid}/transfer-ownership",
                json={"new_owner_user_id": target_id, "previous_owner_role": "ADMIN"},
            )
            assert res.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_principal, None)

        # Owner successfully transfers
        owner_p = Principal(
            principal_id=owner_id,
            user_id=owner_id,
            allowed_project_ids={pid},
            project_roles={pid: "OWNER"},
        )
        app.dependency_overrides[get_current_principal] = lambda: owner_p
        try:
            res = await client.post(
                f"/api/v1/projects/{pid}/transfer-ownership",
                json={"new_owner_user_id": target_id, "previous_owner_role": "ADMIN"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["new_owner_user_id"] == target_id
            assert data["previous_owner_user_id"] == owner_id

            # Check database state
            await test_session.refresh(project)
            assert project.owner_user_id == target_id
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


# ==============================================================================
# 2. P0: GitHub Webhook Binding Immutable-ID Only
# ==============================================================================
@pytest.mark.asyncio
async def test_github_webhook_no_url_fallback_when_id_present(test_session):
    """Verify webhook with a different repository ID will NOT fallback to URL matching for bound projects."""
    from unittest.mock import MagicMock

    from cortexforge.apps.api.routes.github import handle_github_webhook
    from cortexforge.core.models import Project

    # Project bound to repo ID 111 with URL https://github.com/org/repo
    proj = Project(
        id="proj_bound",
        name="Bound Project",
        github_repository_id="111",
        repository_url="https://github.com/org/repo",
        local_path="/tmp/test_bound",
    )
    test_session.add(proj)
    await test_session.commit()

    # Webhook arrives from a different repo (ID 222) that reused the same URL
    body = {
        "repository": {
            "id": 222,
            "clone_url": "https://github.com/org/repo.git",
            "html_url": "https://github.com/org/repo",
        },
        "ref": "refs/heads/main",
        "head_commit": {"id": "abcdef1234567890"},
    }
    import json

    raw_bytes = json.dumps(body).encode()

    mock_request = MagicMock()
    mock_request.headers = {
        "x-github-event": "push",
        "x-github-delivery": "del_123",
        "x-hub-signature-256": None,
        "content-length": str(len(raw_bytes)),
    }
    mock_request.body = AsyncMock(return_value=raw_bytes)
    mock_request.json = AsyncMock(return_value=body)

    with patch("cortexforge.apps.api.routes.github.verify_github_signature", return_value=True):
        res = await handle_github_webhook(
            request=mock_request,
            x_github_event="push",
            x_hub_signature_256=None,
            x_github_delivery="del_123",
            session=test_session,
        )
        # Should be ignored because repository ID 222 does not match bound ID 111
        assert res.get("status") == "ignored"
        assert "No registered CortexForge project matches" in res.get("message", "")


# ==============================================================================
# 3. P0: GitHub OAuth Token Envelope Encryption & Disconnect
# ==============================================================================
def test_envelope_encryption_roundtrip():
    """Verify AES-256-GCM envelope encryption and decryption."""
    raw_token = "ghp_SuperSecretGitHubOAuthToken1234567890"
    encrypted = encrypt_token(raw_token)

    assert encrypted != raw_token
    assert encrypted.startswith("v1:enc:")
    assert raw_token not in encrypted

    decrypted = decrypt_token(encrypted)
    assert decrypted == raw_token


# ==============================================================================
# 4. P0: Production Authentication Fail-Closed & Startup Safety
# ==============================================================================
def test_validate_runtime_environment_strictness():
    """Verify invalid or missing runtime environment fails closed."""
    with (
        patch.dict(os.environ, {"CORTEX_ENV": "invalid_environment", "PYTEST_CURRENT_TEST": ""}),
        pytest.raises(ValueError, match="Invalid CORTEX_ENV"),
    ):
        validate_runtime_environment()


def test_assert_startup_auth_safety_public_interface():
    """Verify server refuses to start on public interface with auth disabled."""
    with (
        patch("cortexforge.security.auth.is_auth_disabled", return_value=True),
        pytest.raises(RuntimeError, match="FATAL SECURITY MISCONFIGURATION"),
    ):
        assert_startup_auth_safety("0.0.0.0")


# ==============================================================================
# 5. P1: REST Background Jobs Granular Permissions
# ==============================================================================
@pytest.mark.asyncio
async def test_background_jobs_granular_permission_enforcement(test_session):
    """Verify rebuild/scan require PROJECT_SCAN and consolidate requires MEMORY_UPDATE."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        project = Project(
            id=pid, name="Job RBAC", owner_user_id="user_owner", local_path="/tmp/test_job"
        )
        test_session.add(project)
        await test_session.commit()

        # Viewer principal has only PROJECT_READ, not PROJECT_SCAN or MEMORY_UPDATE
        viewer_p = Principal(
            principal_id="viewer_1",
            user_id="viewer_1",
            allowed_project_ids={pid},
            project_roles={pid: "VIEWER"},
        )

        app.dependency_overrides[get_current_principal] = lambda: viewer_p
        try:
            # 1. Rebuild rejected without PROJECT_SCAN
            res = await client.post(f"/api/v1/jobs/projects/{pid}/rebuild")
            assert res.status_code == 403
            assert "project.scan" in res.json()["detail"]

            # 2. Scan rejected without PROJECT_SCAN
            res = await client.post(f"/api/v1/jobs/projects/{pid}/scan")
            assert res.status_code == 403
            assert "project.scan" in res.json()["detail"]

            # 3. Consolidate rejected without MEMORY_UPDATE
            res = await client.post(f"/api/v1/jobs/projects/{pid}/consolidate")
            assert res.status_code == 403
            assert "memory.update" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


# ==============================================================================
# 6. P1: Cognitive Snapshot Granular Permissions
# ==============================================================================
@pytest.mark.asyncio
async def test_cognitive_snapshots_permission_enforcement(test_session):
    """Verify take_snapshot requires SNAPSHOT_CREATE and benchmark requires JOB_CREATE."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        project = Project(
            id=pid, name="Cognitive RBAC", owner_user_id="user_owner", local_path="/tmp/test_cog"
        )
        test_session.add(project)
        await test_session.commit()

        # Principal without snapshot create or job create
        viewer_p = Principal(
            principal_id="viewer_1",
            user_id="viewer_1",
            allowed_project_ids={pid},
            project_roles={pid: "VIEWER"},
        )

        app.dependency_overrides[get_current_principal] = lambda: viewer_p
        try:
            # 1. Take snapshot requires SNAPSHOT_CREATE
            res = await client.post(f"/api/v1/projects/{pid}/snapshots?commit_sha=1234567")
            assert res.status_code == 403
            assert "snapshot.create" in res.json()["detail"]

            # 2. Replay requires SNAPSHOT_READ
            # (Viewer has SNAPSHOT_READ, let's test a principal without it)
            restricted_p = Principal(
                principal_id="r1",
                user_id="r1",
                allowed_project_ids={pid},
                project_roles={pid: "VIEWER"},
                token_scopes={"code:read"},  # Token scope does not map to SNAPSHOT_READ
            )
            app.dependency_overrides[get_current_principal] = lambda: restricted_p
            res = await client.post(f"/api/v1/projects/{pid}/snapshots/1234567/replay")
            assert res.status_code == 403

            # 3. Mutation benchmark requires JOB_CREATE
            res = await client.post(f"/api/v1/projects/{pid}/mutations/benchmark")
            assert res.status_code == 403
            assert "job.create" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_current_principal, None)


# ==============================================================================
# 7. P1: Git Clone SSRF Mitigation
# ==============================================================================
def test_git_url_ssrf_mitigation():
    """Verify validate_git_url blocks private IPs, loopbacks, and cloud metadata."""
    # SSRF Attack vectors must be blocked
    assert validate_git_url("https://127.0.0.1/repo.git") is False
    assert validate_git_url("https://localhost/repo.git") is False
    assert validate_git_url("https://169.254.169.254/latest/meta-data") is False
    assert validate_git_url("https://10.0.0.1/internal/repo.git") is False
    assert validate_git_url("https://192.168.1.100/repo.git") is False
    assert validate_git_url("https://172.16.0.5/repo.git") is False
    assert validate_git_url("https://server.internal/repo.git") is False
    assert validate_git_url("file:///etc/passwd") is False

    # Legitimate providers must be allowed
    assert validate_git_url("https://github.com/org/repo.git") is True
    assert validate_git_url("https://gitlab.com/group/repo.git") is True
    assert validate_git_url("https://bitbucket.org/team/repo.git") is True
    assert validate_git_url("git@github.com:org/repo.git") is True


# ==============================================================================
# 8. P1: Remote MCP OAuth Client Registration
# ==============================================================================
@pytest.mark.asyncio
async def test_mcp_oauth_rejects_duplicate_client_registration():
    """Verify dynamic client registration rejects overwriting an existing client_id."""
    provider = CortexForgeOAuthProvider()
    client_info = OAuthClientInformationFull(
        client_id="unique_claude_client_1",
        client_name="Claude Client",
        redirect_uris=["https://claude.ai/callback"],
    )

    await provider.register_client(client_info)

    # Attempting to re-register the same client_id must raise ValueError
    with pytest.raises(ValueError, match="already registered"):
        await provider.register_client(client_info)


# ==============================================================================
# 9. P1: MCP OAuth Token Scope Enforcement
# ==============================================================================
def test_mcp_token_scopes_restrict_principal_permissions():
    """Verify Principal.has_permission enforces token_scopes restriction."""
    # Principal is OWNER of project, but token has only 'memory:read' scope
    principal = Principal(
        principal_id="user_owner",
        user_id="user_owner",
        allowed_project_ids={"p1"},
        project_roles={"p1": "OWNER"},
        token_scopes={"memory:read"},
    )

    # memory.read is allowed by token scope
    assert principal.has_permission("p1", Permission.MEMORY_READ) is True

    # memory.create / memory.update is NOT allowed by token scope, even though user is OWNER
    assert principal.has_permission("p1", Permission.MEMORY_CREATE) is False
    assert principal.has_permission("p1", Permission.MEMORY_UPDATE) is False
    assert principal.has_permission("p1", Permission.PROJECT_DELETE) is False


# ==============================================================================
# 10. P1: Managed Environment Database Authority
# ==============================================================================
def test_is_managed_environment_helper():
    """Verify is_managed_environment correctly identifies production and staging."""
    with patch.dict(os.environ, {"CORTEX_ENV": "production"}):
        assert is_managed_environment() is True
    with patch.dict(os.environ, {"CORTEX_ENV": "prod"}):
        assert is_managed_environment() is True
    with patch.dict(os.environ, {"CORTEX_ENV": "staging"}):
        assert is_managed_environment() is True
    with patch.dict(os.environ, {"CORTEX_ENV": "development"}):
        assert is_managed_environment() is False
    with patch.dict(os.environ, {"CORTEX_ENV": "test"}):
        assert is_managed_environment() is False


# ==============================================================================
# 11. P1: Sliding Window Rate Limiter
# ==============================================================================
def test_sliding_window_rate_limiter_behavior():
    """Verify sliding window rate limiter limits and resets properly."""
    limiter = SlidingWindowRateLimiter()
    limiter.clear_all()

    key = "test_user_attempt"
    # Allow 3 requests per 10 seconds
    assert limiter.check("auth", key, max_requests=3, window_seconds=10.0) is True
    assert limiter.check("auth", key, max_requests=3, window_seconds=10.0) is True
    assert limiter.check("auth", key, max_requests=3, window_seconds=10.0) is True
    # 4th request exceeds limit
    assert limiter.check("auth", key, max_requests=3, window_seconds=10.0) is False

    # Reset clears history
    limiter.reset(f"auth:{key}")
    assert limiter.check("auth", key, max_requests=3, window_seconds=10.0) is True


# ==============================================================================
# 12. P2: Gemini API Key Header Auth
# ==============================================================================
@pytest.mark.asyncio
async def test_gemini_api_key_in_header_not_url():
    """Verify GeminiProvider uses x-goog-api-key header and does not put key in URL."""
    provider = GeminiProvider(api_key="secret_gemini_key_xyz123")

    captured_request = {}

    async def mock_post(url, json=None, headers=None):
        captured_request["url"] = str(url)
        captured_request["headers"] = headers or {}
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "candidates": [{"content": {"parts": [{"text": "Model output"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
        }
        mock_resp.raise_for_status = lambda: None
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        res = await provider.generate(prompt="Hello")
        assert res.content == "Model output"
        # URL must NOT have ?key=
        assert "?key=" not in captured_request["url"]
        assert "secret_gemini_key_xyz123" not in captured_request["url"]
        # Header must contain the API key
        assert captured_request["headers"].get("x-goog-api-key") == "secret_gemini_key_xyz123"


# ==============================================================================
# 13. P2: Metrics Endpoint Protected by Principal
# ==============================================================================
@pytest.mark.asyncio
async def test_metrics_endpoint_requires_auth():
    """Verify /metrics requires an authenticated principal in production mode."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # In test mode with strict auth or when unauthenticated without dev opt-in
        with patch("cortexforge.security.auth.is_production_environment", return_value=True):
            res = await client.get("/metrics")
            assert res.status_code == 401

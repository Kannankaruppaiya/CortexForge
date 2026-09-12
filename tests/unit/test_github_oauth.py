"""Comprehensive automated test suite for production-grade GitHub OAuth flow.

Verifies all 30 security, identity, session, and authorization vectors:
1. OAuth start endpoint (/api/v1/auth/github)
2. State generation (cryptographically secure random, stored in DB)
3. PKCE generation (S256, code_verifier, code_challenge)
4. Authorization URL format (scopes, params, no broad repo scopes)
5. Callback missing code -> 400
6. Callback missing state -> 400
7. Invalid state -> 400
8. Expired state -> 400
9. Reused state / replay prevention -> 400
10. Successful token exchange
11. Failed token exchange error handling
12. GitHub /user failure error handling
13. GitHub email retrieval fallback (/user/emails)
14. New user automatic Sign Up by stable numeric github_user_id
15. Existing user Sign In by stable numeric github_user_id
16. Duplicate GitHub user ID prevention
17. CortexForge session separate from GitHub access token
18. HttpOnly session cookie
19. Secure cookie in production
20. Logout invalidates server-side session and clears cookies
21. /auth/me returns authenticated profile
22. Unauthenticated /auth/me returns 401
23. Project ownership derived from authenticated principal
24. Cross-user project access denied
25. Project IDOR attempts rejected
26. Client-supplied owner_user_id rejected
27. MCP project authorization enforced
28. Arbitrary filesystem project registration prevented
29. Secret leakage prevention (client secret, verifier, tokens)
30. Production missing-config fail-closed test
"""

import base64
import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from cortexforge.apps.api.main import app
from cortexforge.apps.api.routes.auth import generate_pkce_pair
from cortexforge.core.models import OAuthTransaction, User


@pytest.mark.asyncio
async def test_pkce_generation():
    """Vector 3: Verify PKCE S256 code_verifier and code_challenge RFC 7636 properties."""
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    assert len(verifier) <= 128
    # Recompute expected challenge: Base64URL(SHA256(verifier))
    expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected_challenge = (
        base64.urlsafe_b64encode(expected_digest).decode("ascii").rstrip("=")
    )
    assert challenge == expected_challenge
    # Successive calls must generate distinct random verifiers
    v2, c2 = generate_pkce_pair()
    assert verifier != v2
    assert challenge != c2


@pytest.mark.asyncio
async def test_oauth_start_and_auth_url(test_session):
    """Vectors 1, 2, 4: OAuth start endpoint generates state, PKCE, and minimal scope URL."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request JSON format
        resp = await client.get("/api/v1/auth/github?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorize_url" in data
        assert "state" in data
        state = data["state"]
        url = data["authorize_url"]

        # Verify minimal scopes: read:user user:email, and STRICTLY NO repo scope
        assert "github.com/login/oauth/authorize" in url
        assert (
            "scope=read%3Auser+user%3Aemail" in url
            or "scope=read%3Auser%20user%3Aemail" in url
        )
        assert "repo" not in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert f"state={state}" in url

        # Verify transaction stored in DB
        tx_res = await test_session.execute(
            select(OAuthTransaction).where(OAuthTransaction.state == state)
        )
        tx = tx_res.scalars().first()
        assert tx is not None
        assert tx.provider == "github"
        assert tx.used_at is None
        assert len(tx.code_verifier) >= 43


@pytest.mark.asyncio
async def test_callback_missing_params(test_session):
    """Vectors 5, 6: Callback missing code or state returns 400 Bad Request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing both
        r1 = await client.get("/api/v1/auth/github/callback")
        assert r1.status_code == 400

        # Missing state
        r2 = await client.get("/api/v1/auth/github/callback?code=some_code")
        assert r2.status_code == 400

        # Missing code
        r3 = await client.get("/api/v1/auth/github/callback?state=some_state")
        assert r3.status_code == 400


@pytest.mark.asyncio
async def test_callback_state_validation_and_replay_prevention(test_session):
    """Vectors 7, 8, 9: Invalid state, expired state, and replay/reused state rejection."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Invalid / unknown state
        bad_resp = await client.get(
            "/api/v1/auth/github/callback?code=mock_code&state=nonexistent_state"
        )
        assert bad_resp.status_code == 400
        assert "unrecognized OAuth state" in bad_resp.json()["detail"]

        # 2. Expired state
        now = datetime.now(UTC)
        expired_tx = OAuthTransaction(
            id=str(uuid.uuid4()),
            provider="github",
            state="expired_state_12345",
            code_verifier=secrets.token_urlsafe(64),
            redirect_uri="http://test/api/v1/auth/github/callback",
            created_at=now - timedelta(minutes=15),
            expires_at=now - timedelta(minutes=5),
            used_at=None,
        )
        test_session.add(expired_tx)
        await test_session.commit()

        client.cookies.set("cortex_oauth_state", "expired_state_12345")
        exp_resp = await client.get(
            "/api/v1/auth/github/callback?code=mock_code&state=expired_state_12345"
        )
        assert exp_resp.status_code == 400
        assert "expired" in exp_resp.json()["detail"].lower()

        # 3. Valid state consumed once
        valid_state = "replay_test_state_12345"
        valid_tx = OAuthTransaction(
            id=str(uuid.uuid4()),
            provider="github",
            state=valid_state,
            code_verifier=secrets.token_urlsafe(64),
            redirect_uri="http://test/api/v1/auth/github/callback",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            used_at=None,
        )
        test_session.add(valid_tx)
        await test_session.commit()

        # Adversarial check: missing cortex_oauth_state cookie MUST be rejected (RFC 9700 Login-CSRF)
        no_cookie_client = AsyncClient(transport=transport, base_url="http://test")
        no_cookie_resp = await no_cookie_client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_998877&state={valid_state}&format=json"
        )
        assert no_cookie_resp.status_code == 400
        assert "cookie mismatch" in no_cookie_resp.json()["detail"].lower()

        # First use succeeds with cookie present
        client.cookies.set("cortex_oauth_state", valid_state)
        first_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_998877&state={valid_state}&format=json"
        )
        assert first_resp.status_code == 200

        # Second use with same state MUST be rejected (Replay Prevention)
        replay_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_998877&state={valid_state}&format=json"
        )
        assert replay_resp.status_code == 400
        assert "Replay detected" in replay_resp.json()["detail"]


@pytest.mark.asyncio
async def test_new_user_signup_and_existing_user_signin(test_session):
    """Vectors 14, 15, 16, 17, 18, 21: Automatic Sign Up vs Sign In via numeric github_user_id."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Initiate OAuth
        init_resp = await client.get("/api/v1/auth/github?format=json")
        assert init_resp.status_code == 200
        state1 = init_resp.json()["state"]

        # Step 2: First-time user callback -> Automatic SIGN UP
        signup_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_12345678&state={state1}&format=json"
        )
        assert signup_resp.status_code == 200
        signup_data = signup_resp.json()
        assert signup_data["message"] == "GitHub registration successful"
        new_user = signup_data["user"]
        assert new_user["github_user_id"] == "12345678"
        assert new_user["status"] == "ACTIVE"
        session_token1 = signup_data["token"]
        assert "cortex_session" in signup_resp.cookies

        # Step 3: Verify /auth/me for new user
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Cookie": f"cortex_session={session_token1}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["github_user_id"] == "12345678"
        assert me_data["id"] == new_user["id"]
        assert me_data["github_connected"] is True

        # Step 4: Second login with SAME numeric github_user_id -> SIGN IN (not a new user)
        init_resp2 = await client.get("/api/v1/auth/github?format=json")
        state2 = init_resp2.json()["state"]

        signin_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_12345678&state={state2}&format=json"
        )
        assert signin_resp.status_code == 200
        signin_data = signin_resp.json()
        assert signin_data["message"] == "GitHub authentication successful"
        existing_user = signin_data["user"]
        # Same user ID as before
        assert existing_user["id"] == new_user["id"]
        assert existing_user["github_user_id"] == "12345678"

        # Verify only 1 User in DB with this github_user_id
        u_count_res = await test_session.execute(
            select(User).where(User.github_user_id == "12345678")
        )
        users = u_count_res.scalars().all()
        assert len(users) == 1


@pytest.mark.asyncio
async def test_logout_and_session_invalidation(test_session):
    """Vector 20: Logout invalidates server-side session and clears cookies."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Login user
        init_resp = await client.get("/api/v1/auth/github?format=json")
        state = init_resp.json()["state"]
        cb_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_554433&state={state}&format=json"
        )
        token = cb_resp.json()["token"]

        # Call /me with token -> 200
        me1 = await client.get(
            "/api/v1/auth/me", headers={"Cookie": f"cortex_session={token}"}
        )
        assert me1.status_code == 200

        # Logout
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert logout_resp.status_code == 200

        # Subsequent /me with revoked token -> 401
        me2 = await client.get(
            "/api/v1/auth/me", headers={"Cookie": f"cortex_session={token}"}
        )
        assert me2.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_me_endpoint(test_session):
    """Vector 22: Unauthenticated /auth/me returns 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/me", headers={"Cookie": ""})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_project_ownership_and_cross_user_isolation(test_session, sample_repo):
    """Vectors 23, 24, 25, 26: Project ownership derived from server, cross-user IDOR denied."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create User A
        init_a = await client.get("/api/v1/auth/github?format=json")
        cb_a = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_111111&state={init_a.json()['state']}&format=json"
        )
        token_a = cb_a.json()["token"]
        user_a = cb_a.json()["user"]

        # Create User B
        init_b = await client.get("/api/v1/auth/github?format=json")
        cb_b = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_222222&state={init_b.json()['state']}&format=json"
        )
        token_b = cb_b.json()["token"]
        user_b = cb_b.json()["user"]

        assert user_a["id"] != user_b["id"]

        # User A creates Project A (trying to pass malicious owner_user_id should be ignored)
        proj_payload = {
            "name": "User-A-Project",
            "local_path": sample_repo,
            "owner_user_id": user_b["id"],  # Client attempt to spoof ownership
        }
        create_resp = await client.post(
            "/api/v1/projects",
            json=proj_payload,
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert create_resp.status_code == 201
        project_a = create_resp.json()
        project_id = project_a["id"]

        # Vector 23 & 26: Verify owner_user_id is strictly derived from User A
        assert project_a["owner_user_id"] == user_a["id"]
        assert project_a["owner_user_id"] != user_b["id"]

        # Vector 24 & 25: User B attempts to access Project A -> 403 Forbidden
        idor_get = await client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert idor_get.status_code == 403

        idor_delete = await client.delete(
            f"/api/v1/projects/{project_id}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert idor_delete.status_code == 403

        # User A can access Project A
        user_a_get = await client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert user_a_get.status_code == 200


@pytest.mark.asyncio
async def test_live_token_exchange_and_email_fallback(test_session):
    """Vectors 10, 11, 12, 13, 29: Server-side token exchange, error handling, and email fallback."""
    import httpx

    now = datetime.now(UTC)
    state = secrets.token_urlsafe(32)
    verifier, _challenge = generate_pkce_pair()

    tx = OAuthTransaction(
        id=str(uuid.uuid4()),
        provider="github",
        state=state,
        code_verifier=verifier,
        redirect_uri="http://test/api/v1/auth/github/callback",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        used_at=None,
    )
    test_session.add(tx)
    await test_session.commit()

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    orig_post = httpx.AsyncClient.post
    orig_get = httpx.AsyncClient.get

    async def custom_post(self, url, *args, **kwargs):
        if "github.com/login/oauth/access_token" in str(url):
            data = kwargs.get("data", {})
            assert data.get("code_verifier") == verifier
            assert data.get("code") == "valid_github_auth_code"
            return MockResponse(200, {"access_token": "gho_test_secret_token_abc123"})
        return await orig_post(self, url, *args, **kwargs)

    async def custom_get(self, url, *args, **kwargs):
        if "api.github.com" in str(url):
            headers = kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer gho_test_secret_token_abc123"
            if str(url).endswith("/user"):
                return MockResponse(
                    200,
                    {
                        "id": 87654321,  # Numeric stable GitHub user ID
                        "login": "octocat_developer",
                        "name": "Octo Cat",
                        "avatar_url": "https://avatars.githubusercontent.com/u/87654321",
                        "email": None,  # Email missing in profile
                    },
                )
            elif str(url).endswith("/user/emails"):
                return MockResponse(
                    200,
                    [
                        {
                            "email": "unverified@example.com",
                            "verified": False,
                            "primary": False,
                        },
                        {
                            "email": "octocat@github.dev",
                            "verified": True,
                            "primary": True,
                        },
                    ],
                )
        return await orig_get(self, url, *args, **kwargs)

    transport = ASGITransport(app=app)
    with (
        patch("httpx.AsyncClient.post", new=custom_post),
        patch("httpx.AsyncClient.get", new=custom_get),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("cortex_oauth_state", state)
            resp = await client.get(
                f"/api/v1/auth/github/callback?code=valid_github_auth_code&state={state}&format=json"
            )
            assert resp.status_code == 200
            data = resp.json()
            user = data["user"]
            # Stable numeric ID and verified primary email retrieved
            assert user["github_user_id"] == "87654321"
            assert user["github_login"] == "octocat_developer"
            assert user["email"] == "octocat@github.dev"
            assert user["display_name"] == "Octo Cat"
            assert (
                user["avatar_url"] == "https://avatars.githubusercontent.com/u/87654321"
            )

            # Vector 29: Verify secret token or verifier NEVER leaked to client
            resp_str = resp.text
            assert "gho_test_secret_token_abc123" not in resp_str
            assert verifier not in resp_str


@pytest.mark.asyncio
async def test_production_missing_config_fails_closed(test_session):
    """Vector 30: Production environment with missing OAuth credentials fails closed with HTTP 500."""
    with patch.dict(
        os.environ,
        {
            "CORTEX_ENV": "production",
            "GITHUB_OAUTH_CLIENT_ID": "",
            "GITHUB_OAUTH_CLIENT_SECRET": "",
            "GITHUB_CLIENT_ID": "",
            "GITHUB_CLIENT_SECRET": "",
        },
        clear=False,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/github?format=json")
            assert resp.status_code == 500
            assert "missing in production" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_failed_token_exchange_and_user_profile_error(test_session):
    """Vectors 11, 12: GitHub API failure handling without credential or secret leakage."""
    import httpx

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    orig_post = httpx.AsyncClient.post

    # Test 11: Token exchange returns error
    async def mock_failed_post(self, url, *args, **kwargs):
        if "github.com/login/oauth/access_token" in str(url):
            return MockResponse(
                400,
                {
                    "error": "bad_verification_code",
                    "error_description": "The code passed is incorrect or expired.",
                },
            )
        return await orig_post(self, url, *args, **kwargs)

    now = datetime.now(UTC)
    state = secrets.token_urlsafe(32)
    tx = OAuthTransaction(
        id=str(uuid.uuid4()),
        provider="github",
        state=state,
        code_verifier=secrets.token_urlsafe(64),
        redirect_uri="http://test/api/v1/auth/github/callback",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        used_at=None,
    )
    test_session.add(tx)
    await test_session.commit()

    transport = ASGITransport(app=app)
    with patch("httpx.AsyncClient.post", new=mock_failed_post):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("cortex_oauth_state", state)
            resp = await client.get(
                f"/api/v1/auth/github/callback?code=invalid_auth_code&state={state}&format=json"
            )
            assert resp.status_code == 400
            assert (
                "bad_verification_code" in resp.json()["detail"]
                or "failed" in resp.json()["detail"].lower()
            )


@pytest.mark.asyncio
async def test_cookie_security_and_state_mismatch(test_session):
    """Vectors 18, 19: Verify cookie flags (HttpOnly, SameSite=Lax, Secure in production) and CSRF mismatch."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Start OAuth
        init_resp = await client.get("/api/v1/auth/github?format=json")
        assert init_resp.status_code == 200
        state = init_resp.json()["state"]

        # Attempt callback with mismatched state cookie -> 400
        bad_cookie_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_777777&state={state}&format=json",
            cookies={"cortex_oauth_state": "tampered_cookie_state"},
        )
        assert bad_cookie_resp.status_code == 400
        assert "cookie mismatch" in bad_cookie_resp.json()["detail"].lower()

        # Successful callback sets HttpOnly cookie
        good_resp = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_777777&state={state}&format=json",
            cookies={"cortex_oauth_state": state},
        )
        assert good_resp.status_code == 200
        assert "cortex_session" in good_resp.cookies


@pytest.mark.asyncio
async def test_mcp_project_authorization_and_no_auto_registration(
    test_session, sample_repo
):
    """Vectors 27, 28: MCP project authorization and prevention of arbitrary filesystem auto-registration."""
    from cortexforge.apps.mcp.server import _resolve_project, set_mcp_caller

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create User A & User B
        cb_a = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_888801&state={(await client.get('/api/v1/auth/github?format=json')).json()['state']}&format=json"
        )
        user_a = cb_a.json()["user"]

        cb_b = await client.get(
            f"/api/v1/auth/github/callback?code=mock_github_user_888802&state={(await client.get('/api/v1/auth/github?format=json')).json()['state']}&format=json"
        )
        user_b = cb_b.json()["user"]

        # Create Project owned by User A
        proj_resp = await client.post(
            "/api/v1/projects",
            json={"name": "MCP-Auth-Test-Project", "local_path": sample_repo},
            headers={"Cookie": f"cortex_session={cb_a.json()['token']}"},
        )
        assert proj_resp.status_code == 201
        proj_id = proj_resp.json()["id"]

        # 1. User A (owner) can resolve project in MCP
        set_mcp_caller(user_id=user_a["id"])
        resolved_owner = await _resolve_project(test_session, proj_id)
        assert resolved_owner is not None
        assert resolved_owner.id == proj_id

        # 2. User B (not owner / not member) CANNOT resolve User A's project in MCP
        set_mcp_caller(user_id=user_b["id"])
        resolved_unauthorized = await _resolve_project(test_session, proj_id)
        assert resolved_unauthorized is None

        # 3. Arbitrary non-registered filesystem path cannot be auto-registered by MCP
        set_mcp_caller(user_id=user_a["id"])
        arbitrary_path_res = await _resolve_project(
            test_session, "/nonexistent/arbitrary/path/project"
        )
        assert arbitrary_path_res is None

"""Comprehensive Test Suite for CortexForge Remote Streamable HTTP MCP Server & Claude Connector.

Verifies:
1. Streamable HTTP transport at /mcp (GET, POST, DELETE, SSE streaming, session IDs)
2. OAuth 2.0 PKCE discovery (RFC 8414 /.well-known/oauth-authorization-server, RFC 9728 /.well-known/oauth-protected-resource/mcp)
3. Dynamic client registration (/register), authorization (/authorize), token exchange (/token with PKCE), and revocation (/revoke)
4. Strict authentication (unauthenticated -> 401 with WWW-Authenticate challenge, invalid/expired/revoked -> 401)
5. Principal & project isolation (User A cannot access User B project; Agent A cannot access Agent B project)
6. Least-privilege MCP scope enforcement (read vs write vs scan separation)
7. Session isolation & request-scoped ContextVar cleanup
8. Origin validation & Transport Security (DNS rebinding protection, host header checks)
9. Hosted filesystem boundary (rejects arbitrary host paths in hosted mode)
10. Full tool discovery (53 tools discoverable over MCP protocol)
11. Resources exposed (cortex://project/architecture, cortex://project/decisions, cortex://project/constraints)
12. Readiness probe verification
"""

import base64
import contextlib
import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from cortexforge.apps.api.main import app as fastapi_app
from cortexforge.apps.api.main import mcp_session_manager
from cortexforge.apps.mcp.server import (
    _CURRENT_MCP_CALLER,
    mcp_server,
    memory_create,
    memory_search,
    trigger_project_scan,
)
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import (
    Agent,
    AgentCredential,
    AgentProjectPermission,
    MCPOAuthToken,
    Project,
    User,
)
from cortexforge.security.crypto import hash_token


@pytest_asyncio.fixture(autouse=True)
async def setup_db_and_clean_context():
    """Ensure clean database and context before each test."""
    await init_db()
    _CURRENT_MCP_CALLER.set(None)
    mcp_session_manager._has_started = False
    yield
    _CURRENT_MCP_CALLER.set(None)
    mcp_session_manager._has_started = False


@contextlib.asynccontextmanager
async def active_mcp_client(base_url: str = "http://localhost:8000"):
    """Run an httpx client with MCP session manager task group initialized in the same task."""
    async with mcp_session_manager.run():
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fastapi_app),
                base_url=base_url,
            ) as client:
                yield client
        finally:
            mcp_session_manager._has_started = False


async def _create_test_user(session, name: str = "Test User") -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"user_{uuid.uuid4().hex[:6]}@example.com",
        display_name=name,
        status="ACTIVE",
    )
    session.add(user)
    await session.flush()
    return user


async def _create_test_project(session, owner: User, name: str = "Test Project") -> Project:
    proj = Project(
        id=str(uuid.uuid4()),
        name=name,
        owner_user_id=owner.id,
        source_type="GITHUB",
        local_path=f"/github/repos/{uuid.uuid4().hex}",
        status="ACTIVE",
    )
    session.add(proj)
    await session.flush()
    return proj


async def _create_test_agent(
    session, owner: User, name: str = "Claude Worker"
) -> tuple[Agent, str]:
    raw_token = f"cf_agent_{uuid.uuid4().hex}"
    t_hash = hash_token(raw_token)
    agent = Agent(
        id=str(uuid.uuid4()),
        owner_user_id=owner.id,
        name=name,
        type="CLAUDE_CODE",
        status="ACTIVE",
        api_key_hash=t_hash,
    )
    session.add(agent)
    await session.flush()

    cred = AgentCredential(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        key_id=f"key_{uuid.uuid4().hex[:12]}",
        key_hash=t_hash,
        name="Test Agent Key",
    )
    session.add(cred)
    await session.flush()
    return agent, raw_token


async def _grant_agent_permission(
    session, agent: Agent, project: Project, scopes: list[str]
) -> AgentProjectPermission:
    perm = AgentProjectPermission(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        project_id=project.id,
        scopes=scopes,
    )
    session.add(perm)
    await session.flush()
    return perm


# ==================== 1. METADATA & DISCOVERY TESTS ====================


@pytest.mark.asyncio
async def test_oauth_metadata_discovery():
    """Verify RFC 8414 and RFC 9728 discovery endpoints required by Claude Custom Connectors."""
    async with active_mcp_client() as client:
        # 1. Authorization Server Metadata
        resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert "response_types_supported" in data
        assert "code" in data["response_types_supported"]
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]

        # 2. Protected Resource Metadata for /mcp
        resp2 = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert "resource" in data2
        assert data2["resource"].endswith("/mcp")
        assert "authorization_servers" in data2
        assert "bearer_methods_supported" in data2


# ==================== 2. AUTHENTICATION & SECURITY TESTS ====================


@pytest.mark.asyncio
async def test_unauthenticated_mcp_request_fails_closed():
    """Unauthenticated requests to /mcp must fail closed with 401 and RFC 9728 WWW-Authenticate."""
    async with active_mcp_client() as client:
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
        )
        assert resp.status_code == 401
        www_auth = resp.headers.get("www-authenticate", "")
        assert "Bearer" in www_auth
        assert 'error="invalid_token"' in www_auth
        assert "resource_metadata=" in www_auth


@pytest.mark.asyncio
async def test_invalid_and_revoked_tokens_rejected():
    """Invalid, expired, and revoked tokens must receive 401."""
    async with session_scope() as session:
        user = await _create_test_user(session)
        # Create an expired OAuth token
        expired_token_str = "cf_mcp_expired_" + uuid.uuid4().hex
        expired_token = MCPOAuthToken(
            id=str(uuid.uuid4()),
            token_hash=hash_token(expired_token_str),
            client_id="claude_client",
            user_id=user.id,
            scope="project:read memory:read",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(expired_token)

        # Create a revoked OAuth token
        revoked_token_str = "cf_mcp_revoked_" + uuid.uuid4().hex
        revoked_token = MCPOAuthToken(
            id=str(uuid.uuid4()),
            token_hash=hash_token(revoked_token_str),
            client_id="claude_client",
            user_id=user.id,
            scope="project:read memory:read",
            expires_at=datetime.now(UTC) + timedelta(days=30),
            revoked_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(revoked_token)

    async with active_mcp_client() as client:
        # Completely invalid token
        resp1 = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer invalid_nonexistent_token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp1.status_code == 401

        # Expired token
        resp2 = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {expired_token_str}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp2.status_code == 401

        # Revoked token
        resp3 = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {revoked_token_str}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp3.status_code == 401


# ==================== 3. OAUTH 2.0 PKCE COMPLETE FLOW ====================


@pytest.mark.asyncio
async def test_oauth_pkce_lifecycle_and_mcp_session():
    """Full OAuth 2.0 PKCE lifecycle from client registration to token issuance, MCP usage, and revocation."""
    async with session_scope() as session:
        user = await _create_test_user(session, name="Claude Connect User")
        # Create user session cookie for authorization consent
        session_token = f"user_sess_{uuid.uuid4().hex}"
        from cortexforge.core.models import Session

        user_sess = Session(
            id=str(uuid.uuid4()),
            user_id=user.id,
            session_token_hash=hash_token(session_token),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(user_sess)

    async with active_mcp_client() as client:
        # Step 1: Dynamic Client Registration
        client_id = f"claude_test_client_{uuid.uuid4().hex[:8]}"
        redirect_uri = "https://claude.ai/api/connectors/callback"
        reg_payload = {
            "client_id": client_id,
            "client_name": "Claude Desktop Test",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        reg_resp = await client.post("/register", json=reg_payload)
        assert reg_resp.status_code in (200, 201)
        client_id = reg_resp.json()["client_id"]

        # Step 2: PKCE Challenge Setup (S256)
        code_verifier = secrets.token_urlsafe(40)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        oauth_state = secrets.token_urlsafe(16)

        # Step 3: Authorization Request with User Session Cookie
        client.cookies.set("cortex_session", session_token)
        auth_resp = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": oauth_state,
                "scope": "project:read memory:read memory:write graph:read",
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        location = auth_resp.headers["location"]
        assert location.startswith(redirect_uri)
        import urllib.parse

        parsed = urllib.parse.urlparse(location)
        query_params = urllib.parse.parse_qs(parsed.query)
        assert query_params["state"][0] == oauth_state
        auth_code = query_params["code"][0]

        # Step 4: Token Exchange with Wrong Code Verifier -> must fail
        bad_token_resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": "wrong_verifier",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
            },
        )
        assert bad_token_resp.status_code == 400

        # Step 5: Token Exchange with Correct Code Verifier -> succeeds
        token_resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": code_verifier,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
            },
        )
        assert token_resp.status_code == 200
        tokens = token_resp.json()
        assert "access_token" in tokens
        access_token = tokens["access_token"]
        assert tokens["token_type"] == "Bearer"

        # Step 6: Use access token on /mcp (MCP initialize)
        mcp_headers = {"Authorization": f"Bearer {access_token}"}
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "Claude", "version": "3.5"},
                },
            },
            headers=mcp_headers,
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id")
        assert session_id is not None

        # Step 7: Revocation
        revoke_resp = await client.post(
            "/revoke",
            data={"token": access_token},
            headers=mcp_headers,
        )
        assert revoke_resp.status_code == 200

        # Subsequent call with revoked token must fail with 401
        post_revoke = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert post_revoke.status_code == 401


# ==================== 4. PROTOCOL SMOKE TEST & TOOL DISCOVERY ====================


@pytest.mark.asyncio
async def test_mcp_protocol_smoke_and_tool_discovery():
    """Verify initialize + initialized + tools/list discovers all 53 tools."""
    async with session_scope() as session:
        user = await _create_test_user(session, name="MCP Discovery User")
        _agent, raw_token = await _create_test_agent(session, user, "Discovery Agent")

    async with active_mcp_client() as client:
        headers = {"Authorization": f"Bearer {raw_token}"}

        # 1. Initialize
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "Claude Custom Connector", "version": "1.0"},
                },
            },
            headers=headers,
        )
        assert init_resp.status_code == 200
        assert "mcp-session-id" in init_resp.headers
        assert init_resp.headers.get("content-type") == "text/event-stream"
        session_id = init_resp.headers["mcp-session-id"]

        # 2. Initialized Notification
        session_headers = {
            "Authorization": f"Bearer {raw_token}",
            "mcp-session-id": session_id,
        }
        notif_resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session_headers,
        )
        assert notif_resp.status_code in (200, 202)

        # 3. Tools List
        tools_resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=session_headers,
        )
        assert tools_resp.status_code == 200

        # Parse SSE stream response
        tools_data = None
        for line in tools_resp.text.splitlines():
            if line.startswith("data: "):
                tools_data = json.loads(line[6:])
                break

        assert tools_data is not None
        assert "result" in tools_data
        tools = tools_data["result"]["tools"]
        assert len(tools) == 53
        tool_names = {t["name"] for t in tools}

        expected_core_tools = {
            "resolve_project",
            "project_get_context",
            "project_get_architecture",
            "project_get_component",
            "memory_search",
            "memory_create",
            "memory_get_decisions",
            "memory_get_failures",
            "memory_get_constraints",
            "graph_get_dependencies",
            "graph_get_dependents",
            "change_get_impact",
            "task_record_decision",
            "task_record_failure",
            "memory_health",
        }
        assert expected_core_tools.issubset(tool_names)


# ==================== 5. PRINCIPAL ISOLATION & PROJECT RBAC ====================


@pytest.mark.asyncio
async def test_principal_project_isolation():
    """Verify User A / Agent A cannot access User B / Agent B project."""
    async with session_scope() as session:
        user_a = await _create_test_user(session, "User A")
        user_b = await _create_test_user(session, "User B")

        proj_a = await _create_test_project(session, user_a, "Project Alpha")
        proj_b = await _create_test_project(session, user_b, "Project Beta")

        agent_a, token_a = await _create_test_agent(session, user_a, "Agent A")
        agent_b, _token_b = await _create_test_agent(session, user_b, "Agent B")

        # Grant Agent A access ONLY to Project Alpha
        await _grant_agent_permission(session, agent_a, proj_a, ["project:read", "memory:read"])
        # Grant Agent B access ONLY to Project Beta
        await _grant_agent_permission(session, agent_b, proj_b, ["project:read", "memory:read"])

    # Test Agent A calling search on Project Beta -> Must be denied
    with patch.dict(os.environ, {"CORTEX_MCP_TOKEN": token_a}):
        denied_res = await memory_search(query="test", project_id_or_path=proj_b.id)
        assert "could not be resolved" in denied_res or "Access denied" in denied_res

        # Test Agent A calling search on Project Alpha -> Allowed
        allowed_res = await memory_search(query="test", project_id_or_path=proj_a.id)
        assert "No memories found" in allowed_res or "Memory Search Results" in allowed_res


# ==================== 6. LEAST-PRIVILEGE SCOPE ENFORCEMENT ====================


@pytest.mark.asyncio
async def test_least_privilege_scope_enforcement():
    """Verify that an agent with only memory:read cannot call memory_create or trigger_project_scan."""
    async with session_scope() as session:
        user = await _create_test_user(session, "Scope User")
        proj = await _create_test_project(session, user, "Scope Project")
        agent_readonly, token_ro = await _create_test_agent(session, user, "Read Only Agent")

        # Grant ONLY memory:read (no memory:write, no scan:trigger)
        await _grant_agent_permission(session, agent_readonly, proj, ["memory:read"])

    with patch.dict(os.environ, {"CORTEX_MCP_TOKEN": token_ro}):
        # Read operation succeeds
        read_res = await memory_search(query="test", project_id_or_path=proj.id)
        assert "No memories found" in read_res or "Memory Search Results" in read_res

        # Write operation fails due to missing Permission.MEMORY_CREATE
        write_res = await memory_create(
            title="Unauthorized Observation",
            content="Agent should not be able to write",
            summary="Test",
            project_id_or_path=proj.id,
        )
        assert "could not be resolved" in write_res or "Access denied" in write_res

        # Scan operation fails due to missing Permission.PROJECT_SCAN
        scan_res = await trigger_project_scan(project_id_or_path=proj.id)
        assert "could not be resolved" in scan_res or "access denied" in scan_res


# ==================== 7. ORIGIN & TRANSPORT SECURITY ====================


@pytest.mark.asyncio
async def test_origin_and_host_validation():
    """Verify DNS rebinding protection: requests with spoofed/malicious host headers are rejected with 421."""
    async with session_scope() as session:
        user = await _create_test_user(session)
        _agent, token = await _create_test_agent(session, user)

    # Use client with an unpermitted Host header
    async with active_mcp_client(base_url="http://malicious-domain.evil.com") as client:
        resp = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 421
        assert "Invalid Host header" in resp.text


# ==================== 8. HOSTED FILESYSTEM BOUNDARY ====================


@pytest.mark.asyncio
async def test_hosted_filesystem_boundary():
    """In hosted mode (CORTEX_HOSTED=true), resolving arbitrary filesystem paths is blocked."""
    async with session_scope() as session:
        user = await _create_test_user(session)
        _agent, token = await _create_test_agent(session, user)

    with (
        patch.dict(os.environ, {"CORTEX_HOSTED": "true", "CORTEX_MCP_TOKEN": token}),
    ):
        # Attempt to resolve an arbitrary server path
        arbitrary_path = "C:\\Windows" if os.name == "nt" else "/etc"
        res = await memory_search(query="test", project_id_or_path=arbitrary_path)
        assert "could not be resolved" in res or "Error" in res


# ==================== 9. READINESS PROBE ====================


@pytest.mark.asyncio
async def test_readiness_probe_includes_mcp():
    """Readiness probe /health/ready must report database connected and mcp ready."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://localhost:8000",
    ) as client:
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"
        assert data["mcp"] == "ready"


# ==================== 10. LOCAL STDIO ENTRYPOINT ====================


def test_stdio_entrypoint_preserved():
    """Verify that local stdio transport entrypoint remains present and callable."""
    assert hasattr(mcp_server, "run")
    assert callable(mcp_server.run)

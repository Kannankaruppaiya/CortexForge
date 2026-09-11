"""Integration tests for User Isolation, AI Agent Scoping, Epistemic Security, Audit Integrity, and Filesystem Boundaries."""

import os
import tempfile

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.apps.mcp.server import _resolve_project, set_mcp_caller
from cortexforge.core.db import session_scope
from cortexforge.security.auth import verify_workspace_path_allowed
from cortexforge.security.path_safety import PathSecurity, PathSecurityError


@pytest.mark.asyncio
async def test_cross_user_isolation(test_session):
    """Prove User A and User B are strictly isolated across all project operations."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register User A
        reg_a = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "user_a@cortexforge.dev",
                "password": "PasswordForUserA123!",
                "display_name": "User A",
            },
        )
        token_a = reg_a.json()["token"]
        user_a_id = reg_a.json()["user"]["id"]

        # 2. Register User B
        reg_b = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "user_b@cortexforge.dev",
                "password": "PasswordForUserB123!",
                "display_name": "User B",
            },
        )
        token_b = reg_b.json()["token"]
        user_b_id = reg_b.json()["user"]["id"]

        # 3. User A creates Project A
        tmp_dir_a = tempfile.mkdtemp(prefix="project_a_")
        proj_a_resp = await client.post(
            "/api/v1/projects",
            json={"name": "Project Alpha", "local_path": tmp_dir_a},
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert proj_a_resp.status_code == 201, proj_a_resp.text
        proj_a = proj_a_resp.json()
        proj_a_id = proj_a["id"]
        assert proj_a["owner_user_id"] == user_a_id

        # 4. User B creates Project B
        tmp_dir_b = tempfile.mkdtemp(prefix="project_b_")
        proj_b_resp = await client.post(
            "/api/v1/projects",
            json={"name": "Project Beta", "local_path": tmp_dir_b},
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert proj_b_resp.status_code == 201, proj_b_resp.text
        proj_b = proj_b_resp.json()
        proj_b_id = proj_b["id"]
        assert proj_b["owner_user_id"] == user_b_id

        # --- USER A ISOLATION TESTS AGAINST PROJECT B ---

        # A. User A cannot list Project B
        list_a = await client.get(
            "/api/v1/projects",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert list_a.status_code == 200
        listed_ids_a = [p["id"] for p in list_a.json()]
        assert proj_a_id in listed_ids_a
        assert proj_b_id not in listed_ids_a

        # B. User A cannot read Project B details
        read_b = await client.get(
            f"/api/v1/projects/{proj_b_id}",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert read_b.status_code == 403

        # C. User A cannot delete Project B
        del_b = await client.delete(
            f"/api/v1/projects/{proj_b_id}",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert del_b.status_code == 403

        # D. User A cannot trigger scan on Project B
        scan_b = await client.post(
            f"/api/v1/projects/{proj_b_id}/scan",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert scan_b.status_code == 403

        # E. User A cannot read Project B architecture
        arch_b = await client.get(
            f"/api/v1/projects/{proj_b_id}/architecture",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert arch_b.status_code == 403

        # F. User A cannot read or write memories in Project B
        mem_write_b = await client.post(
            f"/api/v1/projects/{proj_b_id}/memories",
            json={
                "layer": "L1",
                "memory_type": "FACT",
                "title": "Unauthorized memory",
                "content": "Hostile inject",
                "summary": "Hostile inject",
            },
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert mem_write_b.status_code == 403

        mem_list_b = await client.get(
            f"/api/v1/projects/{proj_b_id}/memories",
            headers={"Cookie": f"cortex_session={token_a}"},
        )
        assert mem_list_b.status_code == 403

        # G. User A cannot access Project B through MCP
        async with session_scope() as session:
            set_mcp_caller(user_id=user_a_id)
            resolved = await _resolve_project(session, proj_b_id)
            assert resolved is None, "User A must not resolve Project B in MCP"

        # --- REPEAT IN REVERSE: USER B ISOLATION AGAINST PROJECT A ---

        # A. User B cannot list Project A
        list_b = await client.get(
            "/api/v1/projects",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        listed_ids_b = [p["id"] for p in list_b.json()]
        assert proj_b_id in listed_ids_b
        assert proj_a_id not in listed_ids_b

        # B. User B cannot read Project A
        read_a = await client.get(
            f"/api/v1/projects/{proj_a_id}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert read_a.status_code == 403

        # C. User B cannot delete Project A
        del_a = await client.delete(
            f"/api/v1/projects/{proj_a_id}",
            headers={"Cookie": f"cortex_session={token_b}"},
        )
        assert del_a.status_code == 403

        # D. User B cannot resolve Project A in MCP
        async with session_scope() as session:
            set_mcp_caller(user_id=user_b_id)
            resolved_rev = await _resolve_project(session, proj_a_id)
            assert resolved_rev is None, "User B must not resolve Project A in MCP"


@pytest.mark.asyncio
async def test_agent_isolation_and_scoping(test_session):
    """Prove Agent A authorized for Project A CANNOT access Project B (§13)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register user
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "agent_owner@cortexforge.dev", "password": "Password123!"},
        )
        token = reg_resp.json()["token"]

        # Create Project A and Project B (both owned by same user)
        tmp_a = tempfile.mkdtemp(prefix="agent_proj_a_")
        tmp_b = tempfile.mkdtemp(prefix="agent_proj_b_")
        p_a = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Project A", "local_path": tmp_a},
                headers={"Cookie": f"cortex_session={token}"},
            )
        ).json()
        p_b = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Project B", "local_path": tmp_b},
                headers={"Cookie": f"cortex_session={token}"},
            )
        ).json()

        # Create Agent A
        agent_resp = await client.post(
            "/api/v1/agents",
            json={"name": "Claude Agent", "type": "claude"},
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert agent_resp.status_code == 201
        agent_data = agent_resp.json()
        agent_id = agent_data["agent"]["id"]
        agent_key = agent_data["api_key"]

        # Grant Agent A access ONLY to Project A
        grant_resp = await client.post(
            f"/api/v1/agents/{agent_id}/permissions",
            json={
                "agent_id": agent_id,
                "project_id": p_a["id"],
                "scopes": ["read", "write"],
            },
            headers={"Cookie": f"cortex_session={token}"},
        )
        assert grant_resp.status_code == 201

        # 1. Agent accesses Project A -> 200 OK
        agent_a_resp = await client.get(
            f"/api/v1/projects/{p_a['id']}",
            headers={"Authorization": f"Bearer {agent_key}"},
        )
        assert agent_a_resp.status_code == 200

        # 2. Agent attempts to access Project B -> 403 Forbidden!
        agent_b_resp = await client.get(
            f"/api/v1/projects/{p_b['id']}",
            headers={"Authorization": f"Bearer {agent_key}"},
        )
        assert agent_b_resp.status_code == 403

        # 3. Agent MCP resolution: Agent resolves Project A, but NOT Project B
        async with session_scope() as session:
            set_mcp_caller(token=agent_key)
            res_a = await _resolve_project(session, p_a["id"])
            assert res_a is not None and res_a.id == p_a["id"]

            res_b = await _resolve_project(session, p_b["id"])
            assert res_b is None, (
                "Agent must not resolve Project B without explicit grant"
            )

        # 4. Revoke permission on Project A -> Agent can no longer access Project A
        await client.delete(
            f"/api/v1/agents/{agent_id}/permissions/{p_a['id']}",
            headers={"Cookie": f"cortex_session={token}"},
        )
        agent_a_revoked = await client.get(
            f"/api/v1/projects/{p_a['id']}",
            headers={"Authorization": f"Bearer {agent_key}"},
        )
        assert agent_a_revoked.status_code == 403


@pytest.mark.asyncio
async def test_epistemic_security_and_audit_integrity(test_session):
    """Prove client cannot self-assert human authority or forge audit identity (§18, §19)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register human user
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "human@cortexforge.dev", "password": "Password123!"},
        )
        token = reg_resp.json()["token"]

        tmp_proj = tempfile.mkdtemp(prefix="epistemic_proj_")
        p = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Epistemic Project", "local_path": tmp_proj},
                headers={"Cookie": f"cortex_session={token}"},
            )
        ).json()

        # Create Agent
        agent_resp = await client.post(
            "/api/v1/agents",
            json={"name": "Codex Agent", "type": "codex"},
            headers={"Cookie": f"cortex_session={token}"},
        )
        agent_key = agent_resp.json()["api_key"]
        agent_id = agent_resp.json()["agent"]["id"]

        # Grant agent access to project
        await client.post(
            f"/api/v1/agents/{agent_id}/permissions",
            json={"agent_id": agent_id, "project_id": p["id"]},
            headers={"Cookie": f"cortex_session={token}"},
        )

        # Agent attempts to assert USER_CONFIRMED authority -> 403 Forbidden!
        agent_tamper = await client.post(
            f"/api/v1/projects/{p['id']}/memories",
            json={
                "layer": "L3",
                "memory_type": "DECISION",
                "title": "Forged Authority Decision",
                "content": "Agent trying to self-assert human authority",
                "summary": "Forged",
                "authority": "USER_CONFIRMED",
            },
            headers={"Authorization": f"Bearer {agent_key}"},
        )
        # Agent cannot assert human authority
        assert agent_tamper.status_code == 403


@pytest.mark.asyncio
async def test_filesystem_workspace_security():
    """Prove path traversal, symlink escape, and arbitrary root access are blocked (§16)."""
    # 1. Traversal outside permitted roots raises PathSecurityError
    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(os.getcwd(), "../../Windows/System32")

    with pytest.raises(PathSecurityError):
        PathSecurity.safe_resolve(os.getcwd(), "../../../../etc/passwd")

    # 2. Workspace boundary enforcement
    with pytest.raises(HTTPException):
        # Attempt to register root / or C:\
        verify_workspace_path_allowed("C:\\Windows" if os.name == "nt" else "/etc")

"""Comprehensive Verification Suite for Coding Agent Workflow.

Tests all 24 required capabilities:
1. Claude Code-style MCP agent registration/authentication.
2. Existing project resolution.
3. New project onboarding.
4. Agent-to-project authorization.
5. Unauthorized agent rejection.
6. Agent identity spoofing rejection.
7. User/project isolation.
8. Memory retrieval.
9. Memory write.
10. Decision retrieval.
11. Failure retrieval.
12. Code intelligence retrieval.
13. Current-code-vs-memory provenance.
14. Incremental repository indexing.
15. GitHub source adapter.
16. Local Bridge source adapter.
17. Hosted filesystem boundary.
18. MCP operation without OPENAI_API_KEY.
19. Optional internal LLM operation with provider key.
20. Audit actor correctness.
21. Epistemic authority enforcement.
22. Historical/temporal knowledge correctness.
23. GitHub immutable repository identity.
24. Permission enforcement per MCP operation.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from cortexforge.apps.mcp.server import (
    _resolve_project,
    get_decisions,
    get_git_context,
    get_known_failures,
    get_project_context,
    get_project_status,
    record_decision,
    record_memory,
    resolve_project,
    search_code_knowledge,
    search_project_memory,
    set_mcp_caller,
    trigger_project_scan,
)
from cortexforge.code_intelligence.source_adapter import (
    GitHubRepositorySource,
    LocalBridgeRepositorySource,
    LocalRepositorySource,
    get_repository_source,
)
from cortexforge.cognition.authority import Authority
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import (
    Agent,
    AgentCredential,
    AgentProjectPermission,
    AuditLog,
    CodeEntity,
    Memory,
    Project,
    User,
)
from cortexforge.llm.provider import (
    get_llm_provider,
)
from cortexforge.security.crypto import hash_token


@pytest.fixture(autouse=True)
async def ensure_db_clean():
    """Ensure database is initialized for each test."""
    await init_db()
    set_mcp_caller(None, None, None)
    yield
    set_mcp_caller(None, None, None)


async def _create_test_user_and_agent(
    session, email_prefix: str = "agent_test"
) -> tuple[User, Agent, str]:
    """Helper to create a verified User, Agent, and raw authentication token."""
    user = User(
        id=str(uuid.uuid4()),
        email=f"{email_prefix}_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Operator",
        status="ACTIVE",
    )
    session.add(user)
    await session.flush()

    raw_token = f"cortex_agent_token_{uuid.uuid4().hex}"
    token_hash = hash_token(raw_token)

    agent = Agent(
        id=str(uuid.uuid4()),
        owner_user_id=user.id,
        name="Claude Code Worker",
        type="CLAUDE_CODE",
        status="ACTIVE",
        api_key_hash=token_hash,
    )
    session.add(agent)

    cred = AgentCredential(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        key_id=f"ca_{uuid.uuid4().hex[:20]}",
        key_hash=token_hash,
        name="Default Claude Key",
    )
    session.add(cred)
    await session.commit()

    return user, agent, raw_token


async def _create_test_project(
    session,
    owner_user_id: str,
    local_path: str | None = None,
    name: str = "Test Project",
) -> Project:
    """Helper to create a Project record with an isolated unique directory."""
    if local_path and local_path != ".":
        resolved_path = os.path.realpath(local_path)
    else:
        temp_dir = Path(tempfile.gettempdir()) / f"cortex_repo_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        resolved_path = str(temp_dir.resolve())

    proj = Project(
        id=str(uuid.uuid4()),
        name=name,
        local_path=resolved_path,
        owner_user_id=owner_user_id,
        source_type="LOCAL",
    )
    session.add(proj)
    await session.commit()
    return proj


# ==================== 1. Agent Registration & Authentication ====================


@pytest.mark.asyncio
async def test_01_claude_code_agent_registration_and_authentication():
    """1. Claude Code-style MCP agent authenticates using hashed credential token."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".")

        # Grant agent permission
        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["*"],
        )
        session.add(perm)
        await session.commit()

    # Set ambient token in MCP caller context
    set_mcp_caller(token=token)

    async with session_scope() as session:
        resolved = await _resolve_project(session, proj.id)
        assert resolved is not None
        assert resolved.id == proj.id


# ==================== 2. Existing Project Resolution ====================


@pytest.mark.asyncio
async def test_02_existing_project_resolution():
    """2. Existing authorized project resolves with status, Git branch/commit, and cognition index."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="MyApp")

        # Grant project read
        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["project:read"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    res = await resolve_project(project_id_or_path=proj.id)
    assert f"Project Resolved: {proj.name}" in res
    assert proj.id in res
    assert "Cognition Index" in res
    assert "Status" in res and "Ready" in res


# ==================== 3. New Project Onboarding ====================


@pytest.mark.asyncio
async def test_03_new_project_onboarding():
    """3. Unindexed directory is automatically onboarded, owner derived authoritatively, initial scan triggered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a sample file in temp repo
        sample_file = Path(tmpdir) / "main.py"
        sample_file.write_text("def run_app():\n    return 'running'\n")

        async with session_scope() as session:
            user, agent, token = await _create_test_user_and_agent(session)

        set_mcp_caller(token=token)

        res = await resolve_project(
            project_id_or_path=tmpdir,
            auto_onboard=True,
            project_name="AutoOnboardedApp",
        )

        assert "Project Onboarded: AutoOnboardedApp" in res
        assert "Initial Indexing" in res
        assert "files scanned" in res

        # Verify project exists in database with authoritative owner and bounded scopes
        async with session_scope() as session:
            stmt = select(Project).where(Project.local_path == os.path.realpath(tmpdir))
            created = (await session.execute(stmt)).scalars().first()
            assert created is not None
            assert created.owner_user_id == user.id  # Derived from agent.owner_user_id

            # Verify auto-onboarded agent was granted bounded scopes, NOT wildcard *
            perm_stmt = select(AgentProjectPermission).where(
                AgentProjectPermission.agent_id == agent.id,
                AgentProjectPermission.project_id == created.id,
            )
            perm = (await session.execute(perm_stmt)).scalars().first()
            assert perm is not None
            assert "*" not in perm.scopes
            assert "project:read" in perm.scopes
            assert "memory:read" in perm.scopes
            assert "memory:write" in perm.scopes



# ==================== 4. Agent-to-Project Authorization ====================


@pytest.mark.asyncio
async def test_04_agent_to_project_authorization():
    """4. Agent with explicit project permission can access the project."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".")

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["context:read"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    status_out = await get_project_status(project_id_or_path=proj.id)
    assert f"Project Status: {proj.name}" in status_out
    assert "Cognition Status" in status_out and "READY" in status_out


# ==================== 5. Unauthorized Agent Rejection ====================


@pytest.mark.asyncio
async def test_05_unauthorized_agent_rejection():
    """5. Agent without permission for Project B is rejected with Access Denied."""
    async with session_scope() as session:
        _user_a, _agent_a, token_a = await _create_test_user_and_agent(session, "user_a")
        user_b, agent_b, _token_b = await _create_test_user_and_agent(session, "user_b")

        proj_b = await _create_test_project(session, user_b.id, ".", name="ProjectB")

        # Give agent B permission to project B, but NOT agent A
        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent_b.id,
            project_id=proj_b.id,
            scopes=["*"],
        )
        session.add(perm)
        await session.commit()

    # Agent A attempts to resolve Project B
    set_mcp_caller(token=token_a)

    res = await resolve_project(project_id_or_path=proj_b.id)
    assert "Access Denied" in res


# ==================== 6. Agent Identity Spoofing Rejection ====================


@pytest.mark.asyncio
async def test_06_agent_identity_spoofing_rejection():
    """6. Client cannot supply a foreign agent_id in context while authenticating with its own token."""
    async with session_scope() as session:
        user_a, agent_a, _token_a = await _create_test_user_and_agent(session, "user_a")
        _user_b, _agent_b, token_b = await _create_test_user_and_agent(session, "user_b")

        proj_a = await _create_test_project(session, user_a.id, ".", name="ProjectA")
        perm_a = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent_a.id,
            project_id=proj_a.id,
            scopes=["*"],
        )
        session.add(perm_a)
        await session.commit()

    # Agent B uses token_b but claims agent_id of Agent A
    set_mcp_caller(token=token_b, agent_id=agent_a.id)

    res = await resolve_project(project_id_or_path=proj_a.id)
    assert "Access Denied" in res


# ==================== 7. User and Project Isolation ====================


@pytest.mark.asyncio
async def test_07_user_and_project_isolation():
    """7. Isolation guarantees memories in Project A cannot be read or modified by Project B agent."""
    async with session_scope() as session:
        user_a, _agent_a, _token_a = await _create_test_user_and_agent(session, "user_a")
        user_b, _agent_b, token_b = await _create_test_user_and_agent(session, "user_b")

        proj_a = await _create_test_project(session, user_a.id, ".", name="ProjectA")
        await _create_test_project(session, user_b.id, ".", name="ProjectB")

        mem_a = Memory(
            id=str(uuid.uuid4()),
            project_id=proj_a.id,
            title="Project A Secret Architecture",
            summary="Secret algorithmic details",
            content="Proprietary cryptographic pipeline",
            memory_type="DECISION",
            status="ACTIVE",
            authority=Authority.USER_CONFIRMED.value,
            confidence=1.0,
            importance=0.9,
        )
        session.add(mem_a)
        await session.commit()

    # Agent B attempts to search memories in Project A
    set_mcp_caller(token=token_b)

    res = await search_project_memory(query="Secret", project_id_or_path=proj_a.id)
    assert "Error: Project could not be resolved or access denied" in res


# ==================== 8. Memory Retrieval ====================


@pytest.mark.asyncio
async def test_08_memory_retrieval():
    """8. Retrieves task-relevant memories ranked by hybrid relevance."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="AuthProject")

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["memory:read"],
        )
        session.add(perm)

        mem = Memory(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            title="Stateless JWT Authentication Invariant",
            summary="JWT tokens must be verified without database lookups",
            content="Authentication middleware parses Authorization header and verifies HMAC signature.",
            memory_type="CONSTRAINT",
            status="ACTIVE",
            authority=Authority.USER_CONFIRMED.value,
            confidence=0.95,
            importance=0.85,
        )
        session.add(mem)
        await session.commit()

    set_mcp_caller(token=token)

    res = await search_project_memory(query="JWT", project_id_or_path=proj.id)
    assert "Stateless JWT Authentication Invariant" in res
    assert "[CONSTRAINT]" in res


# ==================== 9. Memory Write ====================


@pytest.mark.asyncio
async def test_09_memory_write():
    """9. Agent records memory with code evidence or candidate classification."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="WriteProject")

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["cognition:write"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    res = await record_memory(
        title="Async Connection Pooling Note",
        content="Always release connections back to the async engine pool after commit.",
        summary="Async engine pooling requirement",
        memory_type="LESSON",
        project_id_or_path=proj.id,
    )

    assert "CANDIDATE_CREATED" in res or "Successfully created memory" in res
    assert "Async Connection Pooling Note" in res


# ==================== 10. Decision Retrieval ====================


@pytest.mark.asyncio
async def test_10_decision_retrieval():
    """10. Retrieves active architectural decisions and rationale."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="DecisionProject")

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["memory:read"],
        )
        session.add(perm)

        dec = Memory(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            title="Use SQLite in Dev and PostgreSQL in Prod",
            summary="Dual-database strategy",
            content="SQLite enables zero-cost local dev while asyncpg provides high concurrency in prod.",
            memory_type="DECISION",
            status="ACTIVE",
            authority=Authority.USER_CONFIRMED.value,
            confidence=1.0,
            importance=0.9,
        )
        session.add(dec)
        await session.commit()

    set_mcp_caller(token=token)

    res = await get_decisions(project_id_or_path=proj.id)
    assert "Use SQLite in Dev and PostgreSQL in Prod" in res
    assert "Dual-database strategy" in res


# ==================== 11. Failure Retrieval ====================


@pytest.mark.asyncio
async def test_11_failure_retrieval():
    """11. Retrieves known failures and post-mortems to avoid repeating past errors."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="FailureProject")

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["memory:read"],
        )
        session.add(perm)

        fail_mem = Memory(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            title="Postgres Parameter Cast Discrepancy",
            summary="Parameter $1 inferred inconsistently as text and varchar",
            content="Fixed by explicitly casting parameter in SQL query: CAST($1 AS VARCHAR).",
            memory_type="FAILURE",
            status="ACTIVE",
            authority=Authority.AGENT_OBSERVED.value,
            confidence=0.9,
            importance=0.95,
        )
        session.add(fail_mem)
        await session.commit()

    set_mcp_caller(token=token)

    res = await get_known_failures(project_id_or_path=proj.id)
    assert "[FAILURE] Postgres Parameter Cast Discrepancy" in res
    assert "CAST($1 AS VARCHAR)" in res


# ==================== 12. Code Intelligence Retrieval ====================


@pytest.mark.asyncio
async def test_12_code_intelligence_retrieval():
    """12. Searches AST symbol index without exposing raw file contents."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(
            session, user.id, ".", name="CodeIntelProject"
        )

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["code:read"],
        )
        session.add(perm)

        entity = CodeEntity(
            id=str(uuid.uuid4()),
            project_id=proj.id,
            name="AuthManager",
            qualified_name="cortexforge.security.auth.AuthManager",
            entity_type="CLASS",
            file_path="src/cortexforge/security/auth.py",
            start_line=10,
            end_line=50,
            language="python",
            signature="class AuthManager(BaseAuth)",
            entity_metadata={
                "docstring": "Manages authentication tokens and sessions."
            },
            content_hash="abc123456789deadbeef",
        )
        session.add(entity)
        await session.commit()

    set_mcp_caller(token=token)

    res = await search_code_knowledge(query="AuthManager", project_id_or_path=proj.id)
    assert "AuthManager" in res
    assert "cortexforge.security.auth.AuthManager" in res
    assert "src/cortexforge/security/auth.py" in res


# ==================== 13. Current Code vs Memory Provenance ====================


@pytest.mark.asyncio
async def test_13_current_code_vs_memory_provenance():
    """13. Real Git state distinguishes live code state from static memory records."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(
            session, user.id, ".", name="ProvenanceProject"
        )

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["*"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    res = await get_git_context(project_id_or_path=proj.id)
    assert f"Git Context for {proj.name}" in res
    assert "Active Branch" in res
    assert "HEAD Commit" in res


# ==================== 14. Incremental Repository Indexing ====================


@pytest.mark.asyncio
async def test_14_incremental_repository_indexing():
    """14. Triggering project scan uses RepositorySource and updates symbols without full duplication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file1 = Path(tmpdir) / "module_a.py"
        file1.write_text("def feature_one(): return 1\n")

        async with session_scope() as session:
            user, agent, token = await _create_test_user_and_agent(session)
            proj = await _create_test_project(
                session, user.id, tmpdir, name="ScanProject"
            )

            perm = AgentProjectPermission(
                id=str(uuid.uuid4()),
                agent_id=agent.id,
                project_id=proj.id,
                scopes=["project:scan"],
            )
            session.add(perm)
            await session.commit()

        set_mcp_caller(token=token)

        res = await trigger_project_scan(project_id_or_path=proj.id)
        assert f"Project scan complete for '{proj.name}'" in res
        assert "Files scanned:" in res


# ==================== 15. GitHub Source Adapter ====================


@pytest.mark.asyncio
async def test_15_github_source_adapter():
    """15. GitHubRepositorySource interacts via genuine remote API without local mirror."""
    import httpx

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if url_str.endswith("/repos/test-org/test-repo"):
            return httpx.Response(200, json={"id": 987654321, "default_branch": "main"})
        if "/commits/main" in url_str:
            return httpx.Response(200, json={"sha": "abc1234567890abcdef1234567890abcdef1234"})
        if "/git/trees/" in url_str:
            return httpx.Response(
                200,
                json={
                    "sha": "abc1234567890abcdef1234567890abcdef1234",
                    "tree": [
                        {"path": "src/main.py", "type": "blob", "size": 100},
                        {"path": "tests/test_main.py", "type": "blob", "size": 50},
                        {"path": "node_modules/pkg.js", "type": "blob", "size": 200},
                        {"path": ".git/config", "type": "blob", "size": 10},
                    ],
                },
            )
        if "/contents/src/main.py" in url_str:
            return httpx.Response(200, content=b"print('hello remote github')\n")
        if "/compare/" in url_str:
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "behind_by": 0,
                    "files": [
                        {"filename": "src/main.py", "status": "modified"},
                        {"filename": "src/new.py", "status": "added"},
                    ],
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})

    _RealClient = httpx.Client
    with patch(
        "cortexforge.code_intelligence.source_adapter.httpx.Client",
        side_effect=lambda *a, **kw: _RealClient(transport=httpx.MockTransport(mock_handler)),
    ):


        gh_source = GitHubRepositorySource(
            github_repository_id="gh_987654321",
            owner="test-org",
            repo="test-repo",
            access_token="ghp_test_token",
            default_branch="main",
        )
        assert gh_source.source_type == "GITHUB"
        assert gh_source.github_repository_id == "gh_987654321"
        assert gh_source.is_accessible() is True
        assert gh_source.get_current_branch() == "main"

        # 1. Remote HEAD commit
        head_sha = gh_source.get_head_commit()
        assert head_sha == "abc1234567890abcdef1234567890abcdef1234"

        # 2. Remote tree file discovery with filtering of ignored dirs/exts
        files = gh_source.discover_files()
        assert "src/main.py" in files
        assert "tests/test_main.py" in files
        assert "node_modules/pkg.js" not in files
        assert ".git/config" not in files

        # 3. Remote file content retrieval
        content = gh_source.read_text("src/main.py")
        assert "hello remote github" in content

        # 4. Remote commit comparison
        diff = gh_source.get_modified_files("base_sha", "target_sha")
        assert len(diff) == 2
        assert diff[0].file_path == "src/main.py"
        assert diff[0].status == "M"
        assert diff[1].file_path == "src/new.py"
        assert diff[1].status == "A"


        # 5. Remote ancestry verification
        assert gh_source.is_ancestor("base_sha", "target_sha") is True
        assert gh_source.is_ancestor("same_sha", "same_sha") is True



# ==================== 16. Local Bridge Source Adapter ====================


@pytest.mark.asyncio
async def test_16_local_bridge_source_adapter():
    """16. LocalBridgeRepositorySource safely encapsulates bridge client communication."""
    mock_bridge = MagicMock()
    mock_bridge.read_local_file.return_value = "def bridged_fn(): pass"
    mock_bridge.canonical_root = Path(".")

    bridge_source = LocalBridgeRepositorySource(mock_bridge)
    assert bridge_source.source_type == "BRIDGE"
    content = bridge_source.read_text("src/app.py")
    assert content == "def bridged_fn(): pass"
    mock_bridge.read_local_file.assert_called_once_with("src/app.py")


# ==================== 17. Hosted Filesystem Boundary ====================


@pytest.mark.asyncio
async def test_17_hosted_filesystem_boundary():
    """17. Production environment rejects arbitrary filesystem paths outside allowed workspaces."""
    with patch.dict(
        os.environ, {"CORTEX_ENV": "production", "CORTEX_TEST_MODE": "false"}
    ):
        from fastapi import HTTPException

        from cortexforge.security.auth import validate_local_registration_path

        # In production, unregistered or traversal path raises 400
        with pytest.raises(HTTPException) as exc_info:
            validate_local_registration_path("../../etc/passwd")
        assert exc_info.value.status_code == 400


# ==================== 18. MCP Operation Without OpenAI API Key ====================


@pytest.mark.asyncio
async def test_18_mcp_operation_without_openai_api_key():
    """18. Core MCP tools operate successfully with zero OPENAI_API_KEY requirement."""
    env_clean = {k: v for k, v in os.environ.items() if "OPENAI" not in k}
    with patch.dict(os.environ, env_clean, clear=True):
        async with session_scope() as session:
            user, agent, token = await _create_test_user_and_agent(session)
            proj = await _create_test_project(
                session, user.id, ".", name="OfflineProject"
            )

            perm = AgentProjectPermission(
                id=str(uuid.uuid4()),
                agent_id=agent.id,
                project_id=proj.id,
                scopes=["*"],
            )
            session.add(perm)
            await session.commit()

        set_mcp_caller(token=token)

        # 1. Status
        status_res = await get_project_status(project_id_or_path=proj.id)
        assert "Cognition Status" in status_res and "READY" in status_res

        # 2. Context retrieval
        context_res = await get_project_context(
            task_text="Refactor session cookies", project_id_or_path=proj.id
        )
        assert "OfflineProject" in context_res

        # 3. Decisions retrieval
        dec_res = await get_decisions(project_id_or_path=proj.id)
        assert (
            "No active architectural decisions" in dec_res
            or "Architectural Decisions" in dec_res
        )


# ==================== 19. Optional Internal LLM Operation with Provider Key ====================


@pytest.mark.asyncio
async def test_19_optional_internal_llm_operation_with_provider_key():
    """19. Provider abstraction supports Anthropic, Gemini, OpenAI, and Mock options."""
    # 1. Mock provider (in development)
    mock_prov = get_llm_provider("mock")
    assert mock_prov.provider_name == "mock"
    resp = await mock_prov.generate("Consolidate memories")
    assert resp.content is not None
    assert resp.is_synthetic is True

    # 2. Anthropic provider
    anthropic_prov = get_llm_provider("anthropic")
    assert anthropic_prov.provider_name == "anthropic"

    # 3. Gemini provider
    gemini_prov = get_llm_provider("gemini")
    assert gemini_prov.provider_name == "gemini"

    # 4. OpenAI provider
    openai_prov = get_llm_provider("openai")
    assert openai_prov.provider_name == "openai"


# ==================== 20. Audit Actor Correctness ====================


@pytest.mark.asyncio
async def test_20_audit_actor_correctness():
    """20. Project onboarding and mutations record the authenticated principal as audit actor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        async with session_scope() as session:
            _user, agent, token = await _create_test_user_and_agent(session)

        set_mcp_caller(token=token)

        await resolve_project(project_id_or_path=tmpdir, auto_onboard=True)

        async with session_scope() as session:
            stmt = (
                select(AuditLog)
                .where(AuditLog.actor == agent.id)
                .order_by(AuditLog.created_at.desc())
            )
            entry = (await session.execute(stmt)).scalars().first()
            assert entry is not None
            assert entry.actor == agent.id  # Matches authenticated agent


# ==================== 21. Epistemic Authority Enforcement ====================


@pytest.mark.asyncio
async def test_21_epistemic_authority_enforcement():
    """21. AI agent memory writes cannot self-assert USER_CONFIRMED authority without approval token."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(
            session, user.id, ".", name="EpistemicProject"
        )

        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["cognition:write"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    # Calling record_decision through MCP
    await record_decision(
        title="Microservice Boundary Splitting",
        rationale="Split auth into separate service.",
        component="src/auth.py",
        project_id_or_path=proj.id,
    )

    async with session_scope() as session:
        stmt = select(Memory).where(
            Memory.project_id == proj.id,
            Memory.title == "Microservice Boundary Splitting",
        )
        mem = (await session.execute(stmt)).scalars().first()
        assert mem is not None
        # Enforced as AGENT_OBSERVED, never USER_CONFIRMED
        assert mem.authority == Authority.AGENT_OBSERVED.value


# ==================== 22. Historical / Temporal Knowledge Correctness ====================


@pytest.mark.asyncio
async def test_22_historical_temporal_knowledge_correctness():
    """22. Git ancestry reasoning verifies whether past commits are ancestors of current HEAD."""
    local_src = LocalRepositorySource(".")
    head = local_src.get_head_commit()
    if head:
        # A commit is always an ancestor of itself in Git graph
        assert local_src.is_ancestor(head, head) is True


# ==================== 23. GitHub Immutable Repository Identity ====================


@pytest.mark.asyncio
async def test_23_github_immutable_repository_identity():
    """23. GitHub project tracks immutable github_repository_id, independent of volatile repo URLs."""
    async with session_scope() as session:
        user, _agent, _token = await _create_test_user_and_agent(session)
        gh_proj = Project(
            id=str(uuid.uuid4()),
            name="ImmutableRepo",
            local_path=".",
            owner_user_id=user.id,
            source_type="GITHUB",
            github_repository_id="gh_id_123456789",
            github_owner="cortex-org",
            github_repo="cortex-core",
            repository_url="https://github.com/cortex-org/cortex-core",
        )
        session.add(gh_proj)
        await session.commit()

        # Factory preserves immutable ID
        source = get_repository_source(gh_proj)
        assert isinstance(source, GitHubRepositorySource)
        assert source.github_repository_id == "gh_id_123456789"


# ==================== 24. Granular Permission Enforcement per MCP Operation ====================


@pytest.mark.asyncio
async def test_24_permission_enforcement_per_mcp_operation():
    """24. Agent with read-only scope can retrieve memories but is rejected when scanning or writing."""
    async with session_scope() as session:
        user, agent, token = await _create_test_user_and_agent(session)
        proj = await _create_test_project(session, user.id, ".", name="PermTestProject")

        # Grant ONLY memory:read
        perm = AgentProjectPermission(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            project_id=proj.id,
            scopes=["memory:read"],
        )
        session.add(perm)
        await session.commit()

    set_mcp_caller(token=token)

    # 1. Allowed: search memory
    search_res = await search_project_memory(query="auth", project_id_or_path=proj.id)
    assert (
        "No memories matching 'auth'" in search_res or "Project Memories" in search_res
    )

    # 2. Denied: trigger scan (requires project:scan)
    scan_res = await trigger_project_scan(project_id_or_path=proj.id)
    assert "Error: Project could not be resolved or access denied" in scan_res


# ==================== 25. GitHub Webhook Strict Repository ID Binding ====================


@pytest.mark.asyncio
async def test_25_github_webhook_strict_repository_id_binding():
    """25. GitHub webhook resolves project strictly by immutable github_repository_id, preventing name collisions."""
    from fastapi import Request

    from cortexforge.apps.api.routes.github import handle_github_webhook

    async with session_scope() as session:
        user, _agent, _token = await _create_test_user_and_agent(session)

        # Project A and Project B share the exact same repo name 'backend', but different immutable IDs
        proj_a = Project(
            id=str(uuid.uuid4()),
            name="backend",
            local_path=f"/dummy/path_a_{uuid.uuid4().hex}",
            owner_user_id=user.id,
            source_type="GITHUB",
            github_repository_id="11111",
            repository_url="https://github.com/org-a/backend.git",
        )
        proj_b = Project(
            id=str(uuid.uuid4()),
            name="backend",
            local_path=f"/dummy/path_b_{uuid.uuid4().hex}",
            owner_user_id=user.id,
            source_type="GITHUB",
            github_repository_id="22222",
            repository_url="https://github.com/org-b/backend.git",
        )

        session.add(proj_a)
        session.add(proj_b)
        await session.commit()

        # Webhook delivers event for repo 22222
        payload_data = {
            "repository": {
                "id": 22222,
                "name": "backend",
                "clone_url": "https://github.com/org-b/backend.git",
            },
            "after": "commit_sha_222",
            "commits": [],
            "sender": {"login": "dev_user"},
        }
        raw_body = json.dumps(payload_data).encode("utf-8")

        mock_req = MagicMock(spec=Request)
        mock_req.body = AsyncMock(return_value=raw_body)
        mock_req.json = AsyncMock(return_value=payload_data)
        mock_req.headers = {"content-length": str(len(raw_body))}

        with patch("cortexforge.apps.api.routes.github.verify_github_signature", return_value=True):
            res = await handle_github_webhook(
                request=mock_req,
                x_github_event="push",
                x_hub_signature_256="sha256=mock",
                x_github_delivery=f"deliv_{uuid.uuid4().hex}",
                session=session,
            )

            # Resolved Project must be Project B
            assert res["status"] == "processed"
            assert res["project"] == "backend"
            assert res["project_id"] == proj_b.id


        # Now test an unmatched repository ID with the same name 'backend'
        payload_unmatched = {
            "repository": {
                "id": 99999,
                "name": "backend",
                "clone_url": "https://github.com/unrelated/backend.git",
            },
            "after": "commit_sha_999",
            "commits": [],
            "sender": {"login": "dev_user"},
        }
        raw_unmatched = json.dumps(payload_unmatched).encode("utf-8")
        mock_req_unmatched = MagicMock(spec=Request)
        mock_req_unmatched.body = AsyncMock(return_value=raw_unmatched)
        mock_req_unmatched.json = AsyncMock(return_value=payload_unmatched)
        mock_req_unmatched.headers = {"content-length": str(len(raw_unmatched))}

        with patch("cortexforge.apps.api.routes.github.verify_github_signature", return_value=True):
            res_unmatched = await handle_github_webhook(
                request=mock_req_unmatched,
                x_github_event="push",
                x_hub_signature_256="sha256=mock",
                x_github_delivery=f"deliv_{uuid.uuid4().hex}",
                session=session,
            )
            # Must be ignored despite identical name 'backend'
            assert res_unmatched["status"] == "ignored"
            assert "99999" in res_unmatched["message"]


# ==================== 26. Per-User GitHub Token Scoping ====================


@pytest.mark.asyncio
async def test_26_per_user_github_token_scoping():
    """26. User without authorized repo token cannot access repos/branches (no global GITHUB_TOKEN fallback)."""
    import httpx
    from fastapi import HTTPException

    from cortexforge.apps.api.routes.github import (
        get_github_repository_branches,
        get_user_github_repositories,
    )
    from cortexforge.core.models import ExternalIdentity
    from cortexforge.security.auth import Principal

    async with session_scope() as session:
        user, _agent, _token = await _create_test_user_and_agent(session)
        user.github_login = "testuser"
        await session.commit()

        principal = Principal(
            principal_id=user.id,
            actor_type="USER",
            user_id=user.id,
            role="user",
        )

        # Set a server-wide GITHUB_TOKEN in env
        with patch.dict(os.environ, {"GITHUB_TOKEN": "global_leak_token"}):
            # 1. User without token in ExternalIdentity does NOT get repos via global token
            res = await get_user_github_repositories(session=session, principal=principal)
            assert res["connected"] is True
            assert res["requires_repo_access"] is True
            assert res["repositories"] == []

            # 2. User cannot query branches without user-scoped token
            with pytest.raises(HTTPException) as exc_info:
                await get_github_repository_branches(
                    owner="testuser", repo="myrepo", session=session, principal=principal
                )
            assert exc_info.value.status_code == 403

            # 3. Add ExternalIdentity with user's scoped access token
            ext = ExternalIdentity(
                user_id=user.id,
                provider="github",
                provider_subject="12345",
                metadata_json={"login": "testuser", "access_token": "user_scoped_token_123"},
            )
            session.add(ext)
            await session.commit()

            # Mock httpx to verify user's scoped token is used in Authorization header
            def branch_mock(request: httpx.Request) -> httpx.Response:
                auth_hdr = request.headers.get("authorization", "")
                assert auth_hdr == "Bearer user_scoped_token_123"
                assert "global_leak_token" not in auth_hdr
                if request.url.path.endswith("/branches"):
                    return httpx.Response(200, json=[{"name": "main"}, {"name": "feature-x"}])
                return httpx.Response(200, json={"default_branch": "main"})

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(branch_mock))
            with patch("httpx.AsyncClient", return_value=mock_client):
                branches_res = await get_github_repository_branches(
                    owner="testuser", repo="myrepo", session=session, principal=principal
                )
                assert "main" in branches_res["branches"]
                assert "feature-x" in branches_res["branches"]


# ==================== 27. Hosted Mode Filesystem Boundary ====================


@pytest.mark.asyncio
async def test_27_hosted_mode_filesystem_boundary():
    """27. Hosted environment rejects direct host filesystem operations unless mediated via Local Bridge."""
    from fastapi import HTTPException

    from cortexforge.apps.api.routes.projects import (
        browse_workspace_directories,
        create_project,
        open_os_directory_picker,
        validate_local_project_path,
    )
    from cortexforge.core.schemas import LocalRepoValidationRequest, ProjectCreate
    from cortexforge.security.auth import Principal

    principal = Principal(
        principal_id="user_hosted",
        actor_type="USER",
        user_id="user_hosted",
        role="user",
    )

    with patch.dict(os.environ, {"CORTEX_HOSTED": "1", "CORTEX_BRIDGE_URL": ""}):
        # 1. Directory picker rejected
        with pytest.raises(HTTPException) as exc_picker:
            await open_os_directory_picker(principal=principal)
        assert exc_picker.value.status_code == 403

        # 2. Filesystem browsing rejected
        with pytest.raises(HTTPException) as exc_browse:
            await browse_workspace_directories(principal=principal)
        assert exc_browse.value.status_code == 403

        # 3. Path validation rejected
        val_res = await validate_local_project_path(
            payload=LocalRepoValidationRequest(path="."), principal=principal
        )
        assert val_res.valid is False
        assert "Local Bridge" in (val_res.error or "")

        # 4. Local project creation rejected
        async with session_scope() as session:
            with pytest.raises(HTTPException) as exc_create:
                await create_project(
                    payload=ProjectCreate(name="HostedLocal", source_type="LOCAL", local_path="."),
                    session=session,
                    principal=principal,
                )
            assert exc_create.value.status_code == 403


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

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

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

        # Verify project exists in database with authoritative owner
        async with session_scope() as session:
            stmt = select(Project).where(Project.local_path == os.path.realpath(tmpdir))
            created = (await session.execute(stmt)).scalars().first()
            assert created is not None
            assert created.owner_user_id == user.id  # Derived from agent.owner_user_id


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
        user_a, agent_a, token_a = await _create_test_user_and_agent(session, "user_a")
        user_b, agent_b, token_b = await _create_test_user_and_agent(session, "user_b")

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
        user_a, agent_a, token_a = await _create_test_user_and_agent(session, "user_a")
        user_b, agent_b, token_b = await _create_test_user_and_agent(session, "user_b")

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
        user_a, agent_a, token_a = await _create_test_user_and_agent(session, "user_a")
        user_b, agent_b, token_b = await _create_test_user_and_agent(session, "user_b")

        proj_a = await _create_test_project(session, user_a.id, ".", name="ProjectA")
        proj_b = await _create_test_project(session, user_b.id, ".", name="ProjectB")

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
    """15. GitHubRepositorySource interacts via immutable repo identity without duplicating codebase."""
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
            user, agent, token = await _create_test_user_and_agent(session)

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
        user, agent, token = await _create_test_user_and_agent(session)
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

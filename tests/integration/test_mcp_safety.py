"""Integration tests for MCP write safety and agent memory verification pipeline (specification section 29).

Asserts that agent-submitted memories through MCP cannot silently establish
active durable truth without verification against code grounding.
"""

import pytest
import pytest_asyncio

from cortexforge.apps.mcp.server import memory_create
from cortexforge.core import db as core_db
from cortexforge.core.db import session_scope
from cortexforge.core.models import Base, Memory, Project


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Set up an isolated SQLite test database."""
    db_file = tmp_path / "test_mcp_safety.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    engine, factory = core_db.create_cortex_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(core_db, "engine", engine)
    monkeypatch.setattr(core_db, "async_session_factory", factory)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_mcp_agent_memory_without_evidence_creates_candidate(tmp_path):
    """Agent asserting knowledge without code evidence produces CANDIDATE_CREATED."""
    repo = tmp_path / "repo"
    repo.mkdir()

    async with session_scope() as session:
        proj = Project(name="SafetyRepo", local_path=str(repo))
        session.add(proj)
        await session.commit()

    res = await memory_create(
        title="Unsubstantiated Architectural Rule",
        content="All requests must pass through Envoy proxy.",
        summary="Envoy requirement",
        memory_type="ARCHITECTURE",
        project_id_or_path=str(repo),
    )

    assert "[CANDIDATE_CREATED]" in res
    assert "Requires verification or human approval" in res

    async with session_scope() as session:
        mem = (await session.execute(Memory.__table__.select())).first()
        assert mem is not None
        assert mem.status in ("CANDIDATE", "REVIEW_REQUIRED", "UNVERIFIED")
        assert mem.status != "ACTIVE"


@pytest.mark.asyncio
async def test_mcp_agent_memory_with_fictitious_evidence_requires_verification(
    tmp_path,
):
    """Agent asserting knowledge with non-existent file produces VERIFICATION_REQUIRED."""
    repo = tmp_path / "repo2"
    repo.mkdir()

    async with session_scope() as session:
        proj = Project(name="SafetyRepo2", local_path=str(repo))
        session.add(proj)
        await session.commit()

    res = await memory_create(
        title="Hallucinated Feature",
        content="Payment module supports Solana transactions.",
        summary="Solana support",
        memory_type="LESSON",
        evidence_file="src/payments/solana.py",
        evidence_line_start=10,
        evidence_line_end=20,
        project_id_or_path=str(repo),
    )

    assert "[VERIFICATION_REQUIRED]" in res
    assert "Evidence check did not confirm active state" in res


@pytest.mark.asyncio
async def test_mcp_agent_memory_with_genuine_code_verifies_active(tmp_path):
    """Agent asserting knowledge with genuine code evidence verifies to VERIFIED_ACTIVE."""
    repo = tmp_path / "repo3"
    repo.mkdir()
    code_file = repo / "auth.py"
    code_content = (
        "def authenticate_user(token):\n    return token.startswith('secret')\n"
    )
    code_file.write_text(code_content, encoding="utf-8")

    async with session_scope() as session:
        proj = Project(name="SafetyRepo3", local_path=str(repo))
        session.add(proj)
        await session.commit()

    res = await memory_create(
        title="Token Authentication Logic",
        content="Authentication checks for secret prefix in token.",
        summary="Token authentication",
        memory_type="FACT",
        evidence_file="auth.py",
        evidence_line_start=1,
        evidence_line_end=2,
        project_id_or_path=str(repo),
    )

    assert "[VERIFIED_ACTIVE]" in res
    assert "Status: `ACTIVE`" in res


@pytest.mark.asyncio
async def test_mcp_human_approval_token_flow(tmp_path):
    """Demonstrates safe human confirmation via server-managed token."""
    from cortexforge.apps.mcp.server import memory_request_human_approval
    from cortexforge.security.approval import ApprovalService

    repo = tmp_path / "repo_approval"
    repo.mkdir()

    async with session_scope() as session:
        proj = Project(name="ApprovalRepo", local_path=str(repo))
        session.add(proj)
        await session.commit()
        project_id = proj.id

    title = "Production Database Freeze"
    content = "No migrations allowed during Q4 peak period."
    m_type = "CONSTRAINT"

    # 1. External MCP client attempts to forge approval token
    res_forged = await memory_create(
        title=title,
        content=content,
        summary="DB Freeze",
        memory_type=m_type,
        approval_token="forged-token-xyz",
        project_id_or_path=str(repo),
    )
    assert "Error: Invalid or unapproved human confirmation token" in res_forged

    # 2. External agent requests approval
    res_req = await memory_request_human_approval(
        title=title,
        content=content,
        memory_type=m_type,
        project_id_or_path=str(repo),
    )
    assert "Created pending human approval request" in res_req
    # Extract token
    import re

    tok_match = re.search(r"Token: `([^`]+)`", res_req)
    assert tok_match is not None
    token = tok_match.group(1)

    # 3. Trusted human approves via server boundary
    async with session_scope() as session:
        approved_rec = await ApprovalService.approve_request(
            session, token=token, approved_by="admin@company.com"
        )
        assert approved_rec is not None
        await session.commit()

    # 4. Agent now calls memory_create with the approved token
    res_valid = await memory_create(
        title=title,
        content=content,
        summary="DB Freeze",
        memory_type=m_type,
        approval_token=token,
        project_id_or_path=str(repo),
    )
    assert "Successfully created memory" in res_valid

    # Verify memory has USER_CONFIRMED authority
    async with session_scope() as session:
        from sqlalchemy import select

        from cortexforge.core.models import Memory

        mem = (
            await session.execute(
                select(Memory).where(
                    Memory.project_id == project_id, Memory.title == title
                )
            )
        ).scalar_one()
        assert mem.authority == "USER_CONFIRMED"

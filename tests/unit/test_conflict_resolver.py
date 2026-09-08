"""Unit tests for conflict resolution, contradiction detection, and confidence scoring."""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.confidence import ConfidenceScorer
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.mark.asyncio
async def test_confidence_scoring_hierarchy():
    """Test explainable confidence scoring enforces conceptual authority ordering."""
    untrusted = ConfidenceScorer.calculate_confidence("untrusted")
    obs = ConfidenceScorer.calculate_confidence("agent_observation")
    doc = ConfidenceScorer.calculate_confidence("doc")
    code = ConfidenceScorer.calculate_confidence("code")
    code_test = ConfidenceScorer.calculate_confidence(
        "code", is_ast_verified=True, has_passing_test=True
    )

    assert untrusted.score < obs.score
    assert obs.score < doc.score
    assert doc.score < code.score
    assert code.score < code_test.score
    assert "source_authority" in untrusted.explanation
    assert code_test.score >= 0.85


@pytest.mark.asyncio
async def test_contradiction_heuristics():
    """Test heuristic detection of opposing polarities and explicit supersession."""
    resolver = ConflictResolver()

    is_contra, reason = resolver.detect_contradiction_heuristics(
        "Redis is required for background jobs processing.",
        "Background jobs were removed and Redis is no longer required.",
    )
    assert is_contra is True
    assert "required" in reason.lower()

    is_contra2, _ = resolver.detect_contradiction_heuristics(
        "Use synchronous HTTP calls for the webhook handler.",
        "Use asynchronous async handlers for the webhook.",
    )
    assert is_contra2 is True

    # Non-contradictory complementary statements
    is_contra3, _ = resolver.detect_contradiction_heuristics(
        "Use PostgreSQL for storage.",
        "PostgreSQL indexes should be created on foreign keys.",
    )
    assert is_contra3 is False


@pytest.mark.asyncio
async def test_conflict_resolution_supersedes_outdated_memory(test_session: AsyncSession, tmp_path):
    """Test that newer code-grounded memory supersedes older contradictory documentation memory."""
    project = Project(name="ConflictProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    mem_service = MemoryService(embedding_provider=embed_provider)

    # 1. Create Memory A (old documentation claiming Redis is required)
    mem_a = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Redis background jobs",
            content="Redis is required for background queue processing.",
            summary="Redis background requirement",
            layer="L3",
            memory_type="DECISION",
            source_type="doc",
            importance=0.8,
            confidence=0.7,
        ),
    )
    assert mem_a.status == MemoryState.ACTIVE.value

    # 2. Create Memory B (newer verified code stating Redis is no longer required)
    mem_b = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Redis removal decision",
            content="Background jobs were removed and Redis is no longer required.",
            summary="Redis no longer required",
            layer="L3",
            memory_type="DECISION",
            source_type="code",
            importance=0.9,
            confidence=0.95,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="src/queue.py",
                    source_type="code",
                    line_start=1,
                    line_end=10,
                )
            ],
        ),
    )

    # Refresh Memory A from DB
    await test_session.refresh(mem_a)
    await test_session.refresh(mem_b)

    # Section 13 Invariant: Memory A must be SUPERSEDED by Memory B
    assert mem_a.status == MemoryState.SUPERSEDED.value
    assert mem_a.superseded_by_id == mem_b.id
    assert mem_b.supersedes_id == mem_a.id
    assert mem_b.status == MemoryState.ACTIVE.value


@pytest.mark.asyncio
async def test_unresolved_conflict_marks_both_conflicted(test_session: AsyncSession, tmp_path):
    """Test that two unsupported contradictory claims are both placed into CONFLICTED state."""
    project = Project(name="UnresolvedProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    mem_service = MemoryService(embedding_provider=embed_provider)

    # Agent Observation 1
    mem_c = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Cache engine choice",
            content="Synchronous caching with Memcached is required for session state.",
            summary="Memcached required",
            layer="L3",
            memory_type="DECISION",
            source_type="agent_observation",
            importance=0.7,
            confidence=0.5,
        ),
    )

    # Agent Observation 2 contradicts Observation 1 without code evidence
    mem_d = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Session cache alternative",
            content="Memcached is deprecated and asynchronous redis is required for sessions.",
            summary="Redis async required",
            layer="L3",
            memory_type="DECISION",
            source_type="agent_observation",
            importance=0.7,
            confidence=0.5,
        ),
    )

    await test_session.refresh(mem_c)
    await test_session.refresh(mem_d)

    # Both must be marked CONFLICTED with a shared conflict_group
    assert mem_c.status == MemoryState.CONFLICTED.value
    assert mem_d.status == MemoryState.CONFLICTED.value
    assert mem_c.conflict_group is not None
    assert mem_c.conflict_group == mem_d.conflict_group

    # Verify retrieval penalizes CONFLICTED memory
    retrieval_engine = HybridRetrievalEngine(embedding_provider=embed_provider)
    results = await retrieval_engine.retrieve(
        session=test_session,
        project_id=project.id,
        query="caching session state",
        limit=5,
    )
    for r in results:
        if r.id in (mem_c.id, mem_d.id):
            assert r.status == "CONFLICTED"

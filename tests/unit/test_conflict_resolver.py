"""Unit tests for conflict resolution, contradiction detection, and confidence scoring."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.confidence import ConfidenceScorer, EvidenceSummary
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.mark.asyncio
async def test_confidence_scoring_hierarchy():
    """Confidence ordering must follow the specified authority hierarchy.

    Note the ordering of `agent_observation` above `documentation`: an agent that
    watched the code run is a better source than prose in the repository, which is
    untrusted input. An earlier ad-hoc weight table had these reversed.
    """
    untrusted = ConfidenceScorer.calculate_confidence("untrusted")
    repo_text = ConfidenceScorer.calculate_confidence("doc")
    llm = ConfidenceScorer.calculate_confidence("llm")
    obs = ConfidenceScorer.calculate_confidence("agent_observation")
    git = ConfidenceScorer.calculate_confidence("git")
    code = ConfidenceScorer.calculate_confidence("code")
    user = ConfidenceScorer.calculate_confidence("user")

    assert untrusted.score < repo_text.score < llm.score < obs.score < git.score
    assert git.score < code.score < user.score
    assert "authority" in untrusted.explanation


@pytest.mark.asyncio
async def test_authority_alone_does_not_produce_high_confidence():
    """A well-sourced statement with no evidence is not a confident one.

    Authority answers "who says so"; confidence answers "how well supported is
    it". Conflating them is what lets an unevidenced assertion from a trusted
    source present itself as established fact.
    """
    bare = ConfidenceScorer.calculate_confidence("code")
    assert bare.score < 0.5, "source type alone must not confer high confidence"

    grounded = ConfidenceScorer.score(
        authority="CODE_VERIFIED",
        evidence=EvidenceSummary(supporting=2, independent_supporting=2),
        last_outcome="VERIFIED",
        last_verified_at=datetime.now(UTC),
        has_passing_test=True,
    )
    assert grounded.score > bare.score
    # Nothing reaches certainty: the ceiling is deliberate.
    assert grounded.score <= 0.97


@pytest.mark.asyncio
async def test_repeated_verification_does_not_inflate_confidence():
    """Re-checking unchanged evidence must produce an identical score.

    This is the regression guard for the removed `confidence += 0.05` behaviour,
    where verifying the same untouched file enough times drove any memory to 1.0.
    """
    kwargs = {
        "authority": "CODE_VERIFIED",
        "evidence": EvidenceSummary(supporting=1, independent_supporting=1),
        "last_outcome": "VERIFIED",
        "last_verified_at": datetime.now(UTC),
    }
    scores = [ConfidenceScorer.score(**kwargs).score for _ in range(25)]
    assert len(set(scores)) == 1, f"confidence drifted across identical checks: {set(scores)}"

    # Nor may piling up copies of one source stand in for corroboration.
    duplicated = ConfidenceScorer.score(
        authority="CODE_VERIFIED",
        evidence=EvidenceSummary(supporting=50, independent_supporting=1),
        last_outcome="VERIFIED",
        last_verified_at=kwargs["last_verified_at"],
    )
    assert duplicated.score == scores[0]


@pytest.mark.asyncio
async def test_higher_authority_contradiction_dominates():
    """Contradicting evidence from a higher authority collapses confidence."""
    supported = ConfidenceScorer.score(
        authority="LLM_GENERATED",
        evidence=EvidenceSummary(supporting=1, independent_supporting=1),
        last_outcome="VERIFIED",
        last_verified_at=datetime.now(UTC),
    )
    refuted = ConfidenceScorer.score(
        authority="LLM_GENERATED",
        evidence=EvidenceSummary(
            supporting=1,
            independent_supporting=1,
            contradicting=1,
            max_supporting_authority="LLM_GENERATED",
            max_contradicting_authority="TEST_VERIFIED",
        ),
        last_outcome="VERIFIED",
        last_verified_at=datetime.now(UTC),
    )
    assert refuted.score < supported.score
    assert "contradicted" in refuted.explanation


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
    # Repository prose is untrusted input, so it enters as a candidate rather
    # than as believed project truth (specification section 23).
    assert mem_a.status == MemoryState.CANDIDATE.value

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

    # Memory A was never believed -- repository prose enters as a candidate -- so
    # being refuted by higher-authority code evidence rejects it outright.
    # Recording it as SUPERSEDED would imply the project once held that position.
    assert mem_a.status == MemoryState.INVALIDATED.value
    assert mem_b.supersedes_id == mem_a.id
    assert mem_a.conflict_group is not None
    assert mem_a.conflict_group == mem_b.conflict_group

    # Memory B is code-authority *and* presents code evidence, so it is activated.
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

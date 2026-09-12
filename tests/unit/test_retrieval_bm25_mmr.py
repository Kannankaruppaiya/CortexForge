"""Unit tests for BM25 lexical ranking and MMR diversity in HybridRetrievalEngine."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.embeddings.provider import FastDeterministicEmbeddingProvider
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.bm25 import BM25Scorer
from cortexforge.retrieval.engine import HybridRetrievalEngine, RetrievalWeights


def test_bm25_scorer_tf_idf_properties():
    """Verify BM25 gives higher weights to discriminative terms."""
    corpus = [
        "The quick brown fox jumps over the lazy dog",
        "A fast brown canine leaps across another lazy dog",
        "Specialized quantum computing algorithms for cryptographic signatures",
    ]
    scorer = BM25Scorer().fit(corpus)

    # "quantum" only appears in document 2, so it should score highest on doc 2
    scores = scorer.score_all("quantum")
    assert scores[2] > scores[0]
    assert scores[0] == 0.0

    # Common word "dog" appears in doc 0 and doc 1
    scores_dog = scorer.score_all("dog")
    assert scores_dog[0] > 0.0
    assert scores_dog[1] > 0.0
    assert scores_dog[2] == 0.0


@pytest.mark.asyncio
async def test_retrieval_filters_superseded_and_applies_layer_filter(
    test_session: AsyncSession, tmp_path
):
    """Verify retrieval excludes superseded memories and honors layer filters."""
    project = Project(name="RetrievalProj", local_path=str(tmp_path))
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    embed_provider = FastDeterministicEmbeddingProvider(dim=64)
    mem_service = MemoryService(embedding_provider=embed_provider)

    # 1. Create L1 Structural memory
    await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="FastAPI Web Framework",
            content="FastAPI is used for HTTP REST routing and dependency injection.",
            summary="FastAPI router",
            layer="L1",
            memory_type="ARCHITECTURE",
            importance=0.9,
        ),
    )

    # 2. Create L3 Decision memory
    mem_dec = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Async SQLAlchemy",
            content="Use async session maker with PostgreSQL and aiosqlite for non-blocking I/O.",
            summary="Async SQLAlchemy DB",
            layer="L3",
            memory_type="DECISION",
            importance=0.9,
        ),
    )

    # 3. Create Superseded memory
    mem_sup = await mem_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            title="Synchronous DB calls",
            content="Legacy sync DB calls using raw SQLite connection.",
            summary="Sync SQLite legacy",
            layer="L3",
            memory_type="DECISION",
            importance=0.5,
        ),
    )
    # Explicitly supersede it
    await mem_service.deprecate_memory(
        test_session,
        mem_sup.id,
        superseded_by_id=mem_dec.id,
        reason="Replaced by async",
    )

    retrieval_engine = HybridRetrievalEngine(
        embedding_provider=embed_provider,
        weights=RetrievalWeights(w_sem=0.3, w_lex=0.3, mmr_lambda=0.7),
    )

    # Query without layer filter
    all_res = await retrieval_engine.retrieve(
        session=test_session,
        project_id=project.id,
        query="database connection session",
        limit=10,
    )
    retrieved_ids = [r.id for r in all_res]

    # Section 19 invariant: superseded memory MUST NOT appear in active retrieval
    assert mem_sup.id not in retrieved_ids
    assert mem_dec.id in retrieved_ids

    # Query with layer filter L1: should only return L1 memory
    l1_res = await retrieval_engine.retrieve(
        session=test_session,
        project_id=project.id,
        query="database routing",
        layer="L1",
        limit=10,
    )
    for item in l1_res:
        assert item.layer == "L1"

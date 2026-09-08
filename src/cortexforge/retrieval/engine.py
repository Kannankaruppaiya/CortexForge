"""Multi-signal hybrid retrieval engine combining BM25 lexical, vector semantic, and graph signals."""

import math
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import CodeEntity, Memory
from cortexforge.embeddings.provider import EmbeddingProvider, get_embedding_provider
from cortexforge.graph.service import GraphService
from cortexforge.memory.conflict_resolver import cosine_similarity
from cortexforge.retrieval.bm25 import BM25Scorer


class RetrievalWeights(BaseModel):
    w_sem: float = 0.28
    w_lex: float = 0.22
    w_graph: float = 0.20
    w_task: float = 0.12
    w_fresh: float = 0.08
    w_conf: float = 0.05
    w_prov: float = 0.05
    p_contra: float = 0.50
    p_stale: float = 0.40
    p_redundancy: float = 0.30
    mmr_lambda: float = 0.70


class ScoredItem(BaseModel):
    id: str
    item_type: str  # "memory" or "entity"
    title: str
    content: str
    summary: str
    score: float
    breakdown: dict[str, float]
    provenance: str | None = None
    status: str = "ACTIVE"
    memory_type: str | None = None
    layer: str | None = None


class HybridRetrievalEngine:
    """Multi-signal retrieval engine fusing BM25 lexical, dense semantic, and graph signals."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        graph_service: GraphService | None = None,
        weights: RetrievalWeights | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.graph_service = graph_service or GraphService()
        self.weights = weights or RetrievalWeights()

    async def retrieve(
        self,
        session: AsyncSession,
        project_id: str,
        query: str,
        layer: str | None = None,
        memory_type: str | None = None,
        task_type: str | None = None,
        target_files: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScoredItem]:
        """Perform multi-signal retrieval over project memories and code entities."""
        # 1. Embed query
        query_embed = await self.embedding_provider.embed_text(query)
        qvec = query_embed.vector

        # 2. Query active and recoverable memories from DB with metadata filtering
        mem_stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                # Explicitly exclude SUPERSEDED, INVALIDATED, and ARCHIVED memories from active retrieval
                Memory.status.in_(["ACTIVE", "UNVERIFIED", "STALE", "CONFLICTED"]),
            )
        )
        if layer:
            mem_stmt = mem_stmt.where(Memory.layer == layer.upper())
        if memory_type:
            mem_stmt = mem_stmt.where(Memory.memory_type == memory_type.upper())

        mem_res = await session.execute(mem_stmt)
        memories = list(mem_res.scalars().all())

        if not memories:
            return []

        # 3. Fit BM25 lexical scorer across memory corpus
        doc_texts = [f"{m.title} {m.summary} {m.content}" for m in memories]
        bm25 = BM25Scorer().fit(doc_texts)
        bm25_scores = bm25.score_all(query)

        # 4. Fetch code entities for graph proximity
        ent_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        ent_res = await session.execute(ent_stmt)
        entities = list(ent_res.scalars().all())

        # Build graph proximity set if target_files provided
        graph_proximate_names: set[str] = set()
        if target_files:
            for tf in target_files:
                for ent in entities:
                    if ent.file_path == tf or tf in ent.file_path:
                        graph_proximate_names.add(ent.qualified_name)
                        graph_proximate_names.add(ent.name)
                        # Expand 1-hop
                        deps = await self.graph_service.get_dependencies(session, project_id, ent.id, depth=1)
                        for d in deps:
                            graph_proximate_names.add(d["name"])

        candidates: list[tuple[ScoredItem, list[float] | None]] = []
        now = datetime.now(UTC)

        # Score memories
        for idx, mem in enumerate(memories):
            mvec = (mem.embedding or {}).get("vector")
            sim_sem = cosine_similarity(qvec, mvec) if mvec else 0.0

            # Okapi BM25 lexical score
            sim_lex = bm25_scores[idx]

            # Graph relevance
            rel_graph = 0.0
            if mem.evidences:
                for ev in mem.evidences:
                    if any(ev.file_path in p or p in ev.file_path for p in graph_proximate_names):
                        rel_graph = 1.0
                        break

            # Task relevance
            match_task = 0.0
            if task_type:
                tt_lower = task_type.lower()
                if ("fail" in tt_lower and mem.memory_type in ("FAILURE", "FIX")) or (
                    "decision" in tt_lower and mem.memory_type == "DECISION"
                ) or ("constraint" in tt_lower and mem.memory_type == "CONSTRAINT"):
                    match_task = 1.0

            # Freshness decay
            created_dt = mem.created_at
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=UTC)
            delta_days = (now - created_dt).total_seconds() / 86400.0
            freshness = math.exp(-0.02 * delta_days)

            # Confidence & Provenance quality
            conf = mem.confidence
            prov = min(1.0, len(mem.evidences) * 0.5)

            # Status penalties
            p_contra = self.weights.p_contra if mem.status == "CONFLICTED" else 0.0
            p_stale = self.weights.p_stale if mem.status == "STALE" else 0.0

            final_score = (
                (self.weights.w_sem * sim_sem)
                + (self.weights.w_lex * sim_lex)
                + (self.weights.w_graph * rel_graph)
                + (self.weights.w_task * match_task)
                + (self.weights.w_fresh * freshness)
                + (self.weights.w_conf * conf)
                + (self.weights.w_prov * prov)
                - p_contra
                - p_stale
            )

            item = ScoredItem(
                id=mem.id,
                item_type="memory",
                title=f"[{mem.memory_type}] {mem.title}",
                content=mem.content,
                summary=mem.summary,
                score=round(final_score, 4),
                breakdown={
                    "sem": round(sim_sem, 3),
                    "lex_bm25": round(sim_lex, 3),
                    "graph": round(rel_graph, 3),
                    "conf": round(conf, 3),
                    "fresh": round(freshness, 3),
                },
                provenance=mem.source_reference or (mem.evidences[0].file_path if mem.evidences else None),
                status=mem.status,
                memory_type=mem.memory_type,
                layer=mem.layer,
            )
            candidates.append((item, mvec))

        # 5. Rerank using MMR (Maximal Marginal Relevance) for diversity and redundancy reduction
        selected = self._apply_mmr(candidates, limit=limit)
        return selected

    def _apply_mmr(
        self,
        candidates: list[tuple[ScoredItem, list[float] | None]],
        limit: int,
    ) -> list[ScoredItem]:
        """Classic Maximal Marginal Relevance (MMR) algorithm."""
        if not candidates:
            return []

        # Sort pool by raw composite score
        pool = sorted(candidates, key=lambda x: x[0].score, reverse=True)

        selected: list[ScoredItem] = []
        selected_vectors: list[list[float] | None] = []
        remaining = pool.copy()

        # Seed with highest scoring candidate
        first_item, first_vec = remaining.pop(0)
        selected.append(first_item)
        selected_vectors.append(first_vec)

        lam = self.weights.mmr_lambda

        while remaining and len(selected) < limit:
            best_idx = 0
            best_mmr_score = -float("inf")

            for idx, (cand_item, cand_vec) in enumerate(remaining):
                # Calculate max similarity to any already selected item
                max_sim_to_selected = 0.0
                for sel_vec, sel_item in zip(selected_vectors, selected, strict=False):
                    if cand_vec and sel_vec:
                        sim = cosine_similarity(cand_vec, sel_vec)
                    else:
                        # Fallback to Jaccard overlap on summary
                        c_words = set(cand_item.summary.lower().split())
                        s_words = set(sel_item.summary.lower().split())
                        sim = len(c_words & s_words) / max(1, len(c_words | s_words))
                    max_sim_to_selected = max(max_sim_to_selected, sim)

                mmr_val = (lam * cand_item.score) - ((1.0 - lam) * max_sim_to_selected)
                if mmr_val > best_mmr_score:
                    best_mmr_score = mmr_val
                    best_idx = idx

            chosen_item, chosen_vec = remaining.pop(best_idx)
            selected.append(chosen_item)
            selected_vectors.append(chosen_vec)

        return selected

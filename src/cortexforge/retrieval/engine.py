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
from cortexforge.retrieval.vector_store import RETRIEVABLE_STATUSES, VectorStore


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

    # How much larger than the requested result count the candidate pool is.
    # Reranking needs room to promote a candidate the vector search ranked lower;
    # a multiplier of 1 would make the other signals decorative.
    candidate_multiplier = 5
    min_candidate_pool = 50

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        graph_service: GraphService | None = None,
        weights: RetrievalWeights | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()
        self.graph_service = graph_service or GraphService()
        self.weights = weights or RetrievalWeights()
        self.vector_store = vector_store or VectorStore()
        # Which candidate-selection strategy the last query used. Exposed so that
        # a caller can report whether an indexed search or the Python fallback
        # ran, rather than assuming.
        self.last_search_strategy: str | None = None

    async def _unembedded_candidates(
        self,
        session: AsyncSession,
        project_id: str,
        layer: str | None,
        memory_type: str | None,
        limit: int,
    ) -> list[Memory]:
        """Retrievable memories for a project, regardless of embedding state."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(RETRIEVABLE_STATUSES),
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(limit)
        )
        if layer:
            stmt = stmt.where(Memory.layer == layer.upper())
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def _entities_for_files(
        session: AsyncSession, project_id: str, target_files: list[str] | None
    ) -> list[CodeEntity]:
        """Code entities in the files a task targets, or none when it targets none.

        Graph proximity only ever consults entities in the target files and their
        one-hop neighbours, so loading the project's whole entity table was pure
        waste.
        """
        if not target_files:
            return []
        normalized = [f.replace("\\", "/") for f in target_files]
        stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        result = await session.execute(stmt.where(CodeEntity.file_path.in_(normalized)))
        entities = list(result.scalars().all())
        if entities:
            return entities

        # Documentation and callers sometimes give a suffix ("services/auth.py")
        # where the indexed path is longer. Fall back to a scoped scan rather
        # than returning nothing.
        all_entities = list((await session.execute(stmt)).scalars().all())
        return [
            entity
            for entity in all_entities
            if any(
                entity.file_path.replace("\\", "/") == target
                or target.endswith(entity.file_path.replace("\\", "/"))
                or entity.file_path.replace("\\", "/").endswith(target)
                for target in normalized
            )
        ]

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
        """Retrieve and rank memories for a task, narrowing candidates in the database.

        Candidate selection is bounded: the vector store returns at most
        ``candidate_pool`` memories, ranked by embedding similarity in the
        database where the database can do it. Reranking then happens over that
        pool rather than over everything the project has ever learned.

        The pool is larger than ``limit`` because lexical, graph and freshness
        signals can promote something the vector search ranked lower -- but it is
        still a bound, which the previous "load everything" approach was not.
        """
        # 1. Embed query
        query_embed = await self.embedding_provider.embed_text(query)
        qvec = query_embed.vector

        # 2. Select a bounded candidate pool, filtered and ranked in the database.
        pool_size = max(limit * self.candidate_multiplier, self.min_candidate_pool)
        search = await self.vector_store.search(
            session,
            project_id=project_id,
            query_vector=qvec,
            embedding_model=query_embed.model,
            layer=layer,
            memory_type=memory_type,
            limit=pool_size,
        )
        memories = search.memories
        self.last_search_strategy = search.strategy

        if not memories:
            # A project may hold memories that carry no embedding at all -- one
            # written before embeddings were configured, for instance. Falling
            # back to a metadata query means those are still findable lexically
            # rather than being invisible to retrieval.
            memories = await self._unembedded_candidates(
                session, project_id, layer, memory_type, pool_size
            )
            if not memories:
                return []

        # 3. Fit BM25 over the candidate pool.
        doc_texts = [f"{m.title} {m.summary} {m.content}" for m in memories]
        bm25 = BM25Scorer().fit(doc_texts)
        bm25_scores = bm25.score_all(query)

        # 4. Fetch only the code entities graph proximity actually needs. Loading
        #    every entity in the project was the other half of the O(N) problem.
        entities = await self._entities_for_files(session, project_id, target_files)

        # Build graph proximity set if target_files provided
        proximate_entity_ids: set[str] = set()
        proximate_files: set[str] = set()
        if target_files:
            norm_targets = {tf.replace("\\", "/").strip("/") for tf in target_files}
            for ent in entities:
                ent_fp = ent.file_path.replace("\\", "/").strip("/")
                if ent_fp in norm_targets or any(
                    ent_fp.endswith("/" + t) or t.endswith("/" + ent_fp)
                    for t in norm_targets
                ):
                    proximate_entity_ids.add(ent.id)
                    proximate_files.add(ent_fp)
                    # Expand 1-hop dependencies
                    deps = await self.graph_service.get_dependencies(
                        session, project_id, ent.id, depth=1
                    )
                    for d in deps:
                        if d.get("id"):
                            proximate_entity_ids.add(d["id"])
                        if d.get("file"):
                            proximate_files.add(d["file"].replace("\\", "/").strip("/"))
                    # Expand 1-hop callers / dependents
                    callers = await self.graph_service.get_dependents(
                        session, project_id, ent.id, depth=1
                    )
                    for c in callers:
                        if c.get("id"):
                            proximate_entity_ids.add(c["id"])
                        if c.get("file"):
                            proximate_files.add(c["file"].replace("\\", "/").strip("/"))

        candidates: list[tuple[ScoredItem, list[float] | None]] = []
        now = datetime.now(UTC)

        # Score memories
        for idx, mem in enumerate(memories):
            mvec = (mem.embedding or {}).get("vector")
            # Prefer the similarity the database computed; fall back to Python
            # only for candidates the vector search did not score.
            sim_sem = search.similarities.get(mem.id)
            if sim_sem is None:
                sim_sem = cosine_similarity(qvec, mvec) if mvec else 0.0

            # Okapi BM25 lexical score
            sim_lex = bm25_scores[idx]

            # Graph relevance (strictly based on entity IDs and exact file paths)
            rel_graph = 0.0
            if mem.evidences and (proximate_entity_ids or proximate_files):
                for ev in mem.evidences:
                    if ev.symbol_id and ev.symbol_id in proximate_entity_ids:
                        rel_graph = 1.0
                        break
                    if ev.file_path:
                        ev_norm = ev.file_path.replace("\\", "/").strip("/")
                        if ev_norm in proximate_files or any(
                            ev_norm.endswith("/" + pf) or pf.endswith("/" + ev_norm)
                            for pf in proximate_files
                        ):
                            rel_graph = 1.0
                            break

            # Task relevance
            match_task = 0.0
            if task_type:
                tt_lower = task_type.lower()
                if (
                    ("fail" in tt_lower and mem.memory_type in ("FAILURE", "FIX"))
                    or ("decision" in tt_lower and mem.memory_type == "DECISION")
                    or ("constraint" in tt_lower and mem.memory_type == "CONSTRAINT")
                ):
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
                provenance=mem.source_reference
                or (mem.evidences[0].file_path if mem.evidences else None),
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

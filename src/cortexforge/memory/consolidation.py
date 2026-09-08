"""Safe memory consolidation engine for synthesizing episodic events into durable knowledge."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import Memory, MemoryEvidence, MemoryRelation
from cortexforge.llm.provider import LLMProvider, get_llm_provider
from cortexforge.memory.conflict_resolver import ConflictResolver, cosine_similarity
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState
from cortexforge.memory.service import MemoryService


class MemoryConsolidationEngine:
    """Consolidates episodic task memories into compact, durable project lessons (L5)."""

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        llm_provider: LLMProvider | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ) -> None:
        self.memory_service = memory_service or MemoryService()
        self.llm_provider = llm_provider or get_llm_provider()
        self.conflict_resolver = conflict_resolver or ConflictResolver()

    async def consolidate_project_memories(
        self, session: AsyncSession, project_id: str
    ) -> dict[str, Any]:
        """Alias for consolidate_project."""
        return await self.consolidate_project(session, project_id)

    async def consolidate_project(
        self, session: AsyncSession, project_id: str
    ) -> dict[str, Any]:
        """Perform safe, provenance-preserving memory consolidation."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status == MemoryState.ACTIVE.value,
                Memory.memory_type.in_(["EPISODE", "FAILURE", "FIX", "TASK_STATE"]),
            )
        )
        res = await session.execute(stmt)
        episodic_memories = list(res.scalars().all())

        if len(episodic_memories) < 2:
            return {
                "clusters_consolidated": 0,
                "durable_memories_created": 0,
                "memories_archived": 0,
                "contradictions_detected": 0,
                "message": "Not enough episodic memories to trigger consolidation (minimum 2).",
            }

        # 1. Cluster memories using evaluated semantic similarity & term overlap
        clusters: list[list[Memory]] = []
        assigned: set[str] = set()

        for i, m1 in enumerate(episodic_memories):
            if m1.id in assigned:
                continue
            v1 = (m1.embedding or {}).get("vector")
            if not v1:
                continue

            current_cluster = [m1]
            assigned.add(m1.id)

            for j, m2 in enumerate(episodic_memories):
                if m2.id in assigned or i == j:
                    continue
                v2 = (m2.embedding or {}).get("vector")
                if not v2:
                    continue

                sim = cosine_similarity(v1, v2)
                t1_words = {w for w in f"{m1.title} {m1.summary}".lower().split() if len(w) > 3}
                t2_words = {w for w in f"{m2.title} {m2.summary}".lower().split() if len(w) > 3}
                overlap = len(t1_words & t2_words) / max(1, len(t1_words | t2_words))

                # Section 17: Evaluated threshold (sim >= 0.50 or substantial content overlap)
                if sim >= 0.50 or overlap >= 0.25 or len(t1_words & t2_words) >= 2:
                    current_cluster.append(m2)
                    assigned.add(m2.id)

            if len(current_cluster) >= 2:
                clusters.append(current_cluster)

        durable_created = 0
        archived_count = 0
        contradictions_count = 0

        # 2. Synthesize each cluster into a durable LESSON or CONSTRAINT (L5)
        for cluster in clusters:
            # Check for internal contradiction within cluster
            has_cluster_conflict = False
            for c_i, ep_a in enumerate(cluster):
                for ep_b in cluster[c_i + 1:]:
                    is_contra, _ = self.conflict_resolver.detect_contradiction_heuristics(
                        ep_a.content, ep_b.content
                    )
                    if is_contra:
                        has_cluster_conflict = True
                        contradictions_count += 1
                        break
                if has_cluster_conflict:
                    break

            if has_cluster_conflict:
                # Do not blindly consolidate contradictory episodes into a single false consensus
                continue

            summaries = "\n".join(f"- {m.title}: {m.content}" for m in cluster)
            prompt = (
                "You are an expert software architect. Given the following related episodes and failures "
                "from a software engineering project, synthesize a durable architectural lesson or constraint:\n\n"
                f"{summaries}\n\n"
                "Output format:\n"
                "TITLE: <Concise Rule Title>\n"
                "SUMMARY: <Single-sentence durable invariant>\n"
                "CONTENT: <Explanation of failure cause, invariant constraint, and verified prevention>"
            )

            llm_res = await self.llm_provider.generate(
                prompt=prompt,
                system_prompt="Extract durable software engineering principles.",
            )

            # Parse synthesized text
            lines = llm_res.content.split("\n")
            title = f"Consolidated Guideline from {len(cluster)} episodes"
            summary = "Consolidated operational invariant"
            content = llm_res.content

            for line in lines:
                if line.startswith("TITLE:"):
                    title = line.replace("TITLE:", "").strip()
                elif line.startswith("SUMMARY:"):
                    summary = line.replace("SUMMARY:", "").strip()
                elif line.startswith("CONTENT:"):
                    content = line.replace("CONTENT:", "").strip()

            # Create new consolidated memory with explicit layer L5
            embed_res = await self.memory_service.embedding_provider.embed_text(f"{title}\n{content}")
            consolidated_mem = Memory(
                project_id=project_id,
                layer="L5",
                memory_type="LESSON",
                title=title,
                content=content,
                summary=summary,
                status=MemoryState.ACTIVE.value,
                confidence=0.90,
                importance=0.85,
                source_type="consolidation",
                source_reference=f"Consolidated from {len(cluster)} episodes",
                created_by="consolidation_engine",
                version=1,
                embedding={"vector": embed_res.vector},
                embedding_model=embed_res.model,
                embedding_version=embed_res.version,
            )
            session.add(consolidated_mem)
            await session.flush()

            # Inherit and preserve grounded code evidences from all member episodes
            for ep in cluster:
                if ep.evidences:
                    for ev in ep.evidences:
                        ev_copy = MemoryEvidence(
                            memory_id=consolidated_mem.id,
                            source_type=ev.source_type,
                            source_id=ev.source_id,
                            file_path=ev.file_path,
                            symbol_id=ev.symbol_id,
                            commit_sha=ev.commit_sha,
                            line_start=ev.line_start,
                            line_end=ev.line_end,
                            evidence_hash=ev.evidence_hash,
                            snippet_hash=ev.snippet_hash,
                            ast_fingerprint=ev.ast_fingerprint,
                            confidence=ev.confidence,
                        )
                        session.add(ev_copy)

                # Archive member episode via lifecycle manager and record provenance relation
                ver = MemoryLifecycleManager.transition(
                    ep,
                    MemoryState.ARCHIVED.value,
                    reason=f"Safely consolidated into durable L5 memory {consolidated_mem.id}",
                )
                if ver:
                    session.add(ver)

                rel = MemoryRelation(
                    source_memory_id=consolidated_mem.id,
                    target_memory_id=ep.id,
                    relation_type="derived_from",
                    confidence=1.0,
                )
                session.add(rel)
                archived_count += 1

            durable_created += 1

        await session.commit()
        return {
            "clusters_consolidated": len(clusters),
            "durable_memories_created": durable_created,
            "memories_archived": archived_count,
            "contradictions_detected": contradictions_count,
        }

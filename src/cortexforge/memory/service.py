"""Layered Memory Service (L0-L6) for CortexForge."""

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import (
    Memory,
    MemoryEvidence,
    MemoryRelation,
    MemoryVersion,
)
from cortexforge.core.schemas import MemoryCreate
from cortexforge.embeddings.provider import EmbeddingProvider, get_embedding_provider


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm1 * norm2)))


class MemoryService:
    """Core memory engine managing layered project memory and lifecycle."""

    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def create_memory(
        self, session: AsyncSession, project_id: str, payload: MemoryCreate
    ) -> Memory:
        """Create a new grounded project memory with versioning and embeddings."""
        # Generate embedding for memory content
        embed_res = await self.embedding_provider.embed_text(f"{payload.title}\n{payload.content}")

        status = "ACTIVE" if payload.importance >= 0.3 else "UNVERIFIED"
        memory = Memory(
            project_id=project_id,
            memory_type=payload.memory_type.upper(),
            title=payload.title,
            content=payload.content,
            summary=payload.summary,
            status=status,
            confidence=1.0,
            importance=payload.importance,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
            created_by=payload.created_by,
            version=1,
            embedding={"vector": embed_res.vector},
            embedding_model=embed_res.model,
            embedding_version=embed_res.version,
            last_verified_at=datetime.now(UTC),
        )
        session.add(memory)
        await session.flush()

        # Attach initial version audit record
        version_record = MemoryVersion(
            memory_id=memory.id,
            version=1,
            previous_version=None,
            content=memory.content,
            change_reason="Initial memory creation",
        )
        session.add(version_record)

        # Attach evidences
        if payload.evidence:
            for ev in payload.evidence:
                ev_obj = MemoryEvidence(
                    memory_id=memory.id,
                    source_type=ev.source_type,
                    source_id=ev.source_id,
                    file_path=ev.file_path,
                    commit_sha=ev.commit_sha,
                    line_start=ev.line_start,
                    line_end=ev.line_end,
                    evidence_hash=f"{ev.file_path}:{ev.line_start or 0}",
                    confidence=ev.confidence,
                )
                session.add(ev_obj)

        await session.commit()
        await session.refresh(memory)
        return memory

    async def get_memory(self, session: AsyncSession, memory_id: str) -> Memory | None:
        """Fetch memory with evidences and versions loaded."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences), selectinload(Memory.versions))
            .where(Memory.id == memory_id)
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    async def update_memory(
        self,
        session: AsyncSession,
        memory_id: str,
        content: str,
        change_reason: str,
        title: str | None = None,
        summary: str | None = None,
    ) -> Memory | None:
        """Mutate memory content and increment version audit trail."""
        memory = await self.get_memory(session, memory_id)
        if not memory:
            return None

        prev_version = memory.version
        memory.version += 1
        memory.content = content
        if title:
            memory.title = title
        if summary:
            memory.summary = summary

        # Recompute embedding
        embed_res = await self.embedding_provider.embed_text(f"{memory.title}\n{memory.content}")
        memory.embedding = {"vector": embed_res.vector}
        memory.updated_at = datetime.now(UTC)

        version_record = MemoryVersion(
            memory_id=memory.id,
            version=memory.version,
            previous_version=prev_version,
            content=content,
            change_reason=change_reason,
        )
        session.add(version_record)
        await session.commit()
        await session.refresh(memory)
        return memory

    async def deprecate_memory(
        self,
        session: AsyncSession,
        memory_id: str,
        superseded_by_id: str | None = None,
        reason: str = "Explicitly deprecated",
    ) -> Memory | None:
        """Mark memory as deprecated and link superseding memory if given."""
        memory = await self.get_memory(session, memory_id)
        if not memory:
            return None

        memory.status = "DEPRECATED"
        memory.updated_at = datetime.now(UTC)

        if superseded_by_id:
            rel = MemoryRelation(
                source_memory_id=superseded_by_id,
                target_memory_id=memory.id,
                relation_type="supersedes",
                confidence=1.0,
            )
            session.add(rel)

        ver = MemoryVersion(
            memory_id=memory.id,
            version=memory.version + 1,
            previous_version=memory.version,
            content=memory.content,
            change_reason=f"Deprecated: {reason}",
        )
        memory.version += 1
        session.add(ver)

        await session.commit()
        await session.refresh(memory)
        return memory

    async def list_memories(
        self,
        session: AsyncSession,
        project_id: str,
        memory_type: str | None = None,
        status: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Memory]:
        """Query memories with multi-attribute filtering."""
        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(Memory.project_id == project_id)
        )
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())
        if status:
            stmt = stmt.where(Memory.status == status.upper())
        if min_importance > 0.0:
            stmt = stmt.where(Memory.importance >= min_importance)

        stmt = stmt.order_by(Memory.importance.desc(), Memory.created_at.desc()).offset(offset).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def search_memories(
        self,
        session: AsyncSession,
        project_id: str,
        query: str,
        memory_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Vector semantic similarity search over project memories."""
        query_embed = await self.embedding_provider.embed_text(query)
        qvec = query_embed.vector

        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(["ACTIVE", "UNVERIFIED", "STALE"]),
            )
        )
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type.upper())

        res = await session.execute(stmt)
        memories = list(res.scalars().all())

        scored: list[dict[str, Any]] = []
        for mem in memories:
            emb_data = mem.embedding or {}
            mvec = emb_data.get("vector")
            sim = cosine_similarity(qvec, mvec) if mvec else 0.0

            # Lexical boost
            q_terms = set(query.lower().split())
            content_lower = f"{mem.title} {mem.summary} {mem.content}".lower()
            overlap = sum(1 for t in q_terms if t in content_lower)
            lex_score = overlap / max(1, len(q_terms))

            combined_score = (0.65 * sim) + (0.35 * lex_score)
            scored.append({
                "memory": mem,
                "similarity": round(sim, 4),
                "combined_score": round(combined_score, 4),
            })

        scored.sort(key=lambda x: x["combined_score"], reverse=True)
        return scored[:limit]

    async def get_decisions(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch all active architectural decisions."""
        return await self.list_memories(session, project_id, memory_type="DECISION", status="ACTIVE")

    async def get_failures(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch historical failures and anti-patterns."""
        return await self.list_memories(session, project_id, memory_type="FAILURE")

    async def get_constraints(self, session: AsyncSession, project_id: str) -> list[Memory]:
        """Fetch operational and architectural constraints."""
        return await self.list_memories(session, project_id, memory_type="CONSTRAINT", status="ACTIVE")

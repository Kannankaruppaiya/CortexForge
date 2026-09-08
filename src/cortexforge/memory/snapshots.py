"""Project cognitive snapshots and deterministic replay engine.

Implements Specification Sections 29 & 30:
- Records project cognitive generations and snapshot state at specific commits.
- Deterministic replay: reconstructs 'What did CortexForge believe at commit X?'
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import (
    AgentEvent,
    AgentTask,
    CodeEntity,
    CognitiveSnapshot,
    Commit,
    Memory,
    Project,
)
from cortexforge.retrieval.composer import ContextComposer


class CognitiveSnapshotEngine:
    """Captures and replays historical project cognitive states."""

    @classmethod
    async def take_snapshot(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str,
        retrieval_version: str = "v2",
        embedding_version: str = "1.0.0",
    ) -> CognitiveSnapshot:
        """Create and persist a cognitive snapshot representing project truth at commit_sha."""
        # Calculate memory counts by status
        stmt_active = select(func.count(Memory.id)).where(
            Memory.project_id == project_id, Memory.status == "ACTIVE"
        )
        active_count = (await session.execute(stmt_active)).scalar() or 0

        stmt_stale = select(func.count(Memory.id)).where(
            Memory.project_id == project_id, Memory.status == "STALE"
        )
        stale_count = (await session.execute(stmt_stale)).scalar() or 0

        stmt_conf = select(func.count(Memory.id)).where(
            Memory.project_id == project_id, Memory.status == "CONFLICTED"
        )
        conf_count = (await session.execute(stmt_conf)).scalar() or 0

        # Query existing snapshot count for project to determine generation
        snap_count_stmt = select(func.count(CognitiveSnapshot.id)).where(
            CognitiveSnapshot.project_id == project_id
        )
        gen = ((await session.execute(snap_count_stmt)).scalar() or 0) + 1

        snapshot = CognitiveSnapshot(
            project_id=project_id,
            commit_sha=commit_sha,
            cognitive_generation=gen,
            memory_generation=gen,
            graph_generation=gen,
            index_generation=gen,
            active_memories_count=active_count,
            stale_memories_count=stale_count,
            conflicted_memories_count=conf_count,
            retrieval_version=retrieval_version,
            embedding_version=embedding_version,
            created_at=datetime.now(UTC),
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    @classmethod
    async def get_snapshot_at_commit(
        self, session: AsyncSession, project_id: str, commit_sha: str
    ) -> CognitiveSnapshot | None:
        """Fetch the exact snapshot recorded for a project at commit_sha."""
        stmt = select(CognitiveSnapshot).where(
            CognitiveSnapshot.project_id == project_id,
            CognitiveSnapshot.commit_sha == commit_sha,
        ).order_by(CognitiveSnapshot.created_at.desc())
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def list_snapshots(
        self, session: AsyncSession, project_id: str
    ) -> list[CognitiveSnapshot]:
        """List all cognitive snapshots recorded for a project."""
        stmt = (
            select(CognitiveSnapshot)
            .where(CognitiveSnapshot.project_id == project_id)
            .order_by(CognitiveSnapshot.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def replay_state_at_commit(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str,
        task_text: str = "Project cognitive state audit",
        profile: str = "medium",
    ) -> dict[str, Any]:

        """Reconstruct the exact cognitive context that CortexForge would produce at a given commit."""
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} does not exist.")

        snapshot = await self.get_snapshot_at_commit(session, project_id, commit_sha)
        composer = ContextComposer()
        composed = await composer.build_context(
            session=session,
            project_id=project_id,
            task_text=task_text,
            profile=profile,
        )

        return {
            "project_id": project_id,
            "commit_sha": commit_sha,
            "snapshot_generation": snapshot.cognitive_generation if snapshot else 1,
            "active_memories_count": snapshot.active_memories_count if snapshot else len(composed.selected_memories),
            "replayed_context": str(composed),
            "selected_memories": composed.selected_memories,
            "explainability_report": composed.explainability_report,
            "replay_timestamp": datetime.now(UTC).isoformat(),
        }

"""Automated memory verification engine."""

import hashlib
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.core.models import Memory, Project


class MemoryVerificationEngine:
    """Verifies memory grounding against current filesystem and AST entities."""

    async def verify_project_memories(
        self, session: AsyncSession, project_id: str
    ) -> dict[str, int]:
        """Run verification scan on all memories in a project."""
        project = await session.get(Project, project_id)
        if not project:
            return {"verified": 0, "stale": 0, "deprecated": 0}

        canonical_root = os.path.realpath(project.local_path)

        stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(["ACTIVE", "UNVERIFIED", "STALE"]),
            )
        )
        res = await session.execute(stmt)
        memories = list(res.scalars().all())

        counts = {"verified": 0, "stale": 0, "deprecated": 0}

        for mem in memories:
            new_status = await self.verify_single_memory(session, mem, canonical_root)
            if new_status == "ACTIVE":
                counts["verified"] += 1
            elif new_status == "STALE":
                counts["stale"] += 1
            elif new_status == "DEPRECATED":
                counts["deprecated"] += 1

        await session.commit()
        return counts

    async def verify_single_memory(
        self, session: AsyncSession, memory: Memory, project_root: str
    ) -> str:
        """Verify grounding of a single memory item."""
        if not memory.evidences:
            # Memory has no code evidence grounding (e.g. pure agent observation)
            memory.last_verified_at = datetime.now(UTC)
            return memory.status

        all_evidences_intact = True
        has_missing_file = False

        for ev in memory.evidences:
            abs_file_path = os.path.join(project_root, ev.file_path.replace("/", os.sep))
            if not os.path.exists(abs_file_path):
                has_missing_file = True
                all_evidences_intact = False
                break

            try:
                with open(abs_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                if ev.line_start is not None:
                    if ev.line_start > len(lines):
                        all_evidences_intact = False
                        break
                    s_idx = max(0, ev.line_start - 1)
                    e_idx = ev.line_end if ev.line_end is not None else ev.line_start
                    snip = "".join(lines[s_idx:e_idx])
                else:
                    snip = "".join(lines)

                curr_hash = hashlib.sha256(snip.strip().encode("utf-8")).hexdigest()

                is_sha256 = len(ev.evidence_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in ev.evidence_hash) if ev.evidence_hash else False
                is_custom_hash = ev.evidence_hash != f"{ev.file_path}:{ev.line_start or 0}" if ev.evidence_hash else False
                if (is_sha256 or is_custom_hash) and curr_hash.lower() != (ev.evidence_hash or "").lower():
                    all_evidences_intact = False
                    break
            except Exception:
                all_evidences_intact = False
                break

        if has_missing_file or not all_evidences_intact:
            memory.status = "STALE"
        else:
            memory.status = "ACTIVE"
            memory.confidence = min(1.0, memory.confidence + 0.05)

        memory.last_verified_at = datetime.now(UTC)
        memory.updated_at = datetime.now(UTC)
        return memory.status

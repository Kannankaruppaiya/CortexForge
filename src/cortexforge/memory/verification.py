"""Automated semantic memory verification engine."""

import hashlib
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider
from cortexforge.core.models import CodeEntity, Memory, Project
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState


class MemoryVerificationEngine:
    """Verifies memory grounding against current filesystem and AST entities."""

    def __init__(self, parser_provider: TreeSitterProvider | None = None) -> None:
        self.parser = parser_provider or TreeSitterProvider()

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
            if new_status == MemoryState.ACTIVE.value:
                counts["verified"] += 1
            elif new_status == MemoryState.STALE.value:
                counts["stale"] += 1
            elif new_status in (MemoryState.SUPERSEDED.value, MemoryState.INVALIDATED.value):
                counts["deprecated"] += 1

        await session.commit()
        return counts

    async def verify_single_memory(
        self, session: AsyncSession, memory: Memory, project_root: str
    ) -> str:
        """Verify grounding of a single memory item using AST and snippet checks."""
        if not memory.evidences:
            # Memory has no code evidence grounding (e.g. user specification or pure observation)
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

            evidence_verified = False

            # Strategy 1: AST Semantic Verification (if symbol_id or parsable code file)
            if ev.symbol_id:
                ent = await session.get(CodeEntity, ev.symbol_id)
                if ent and self.parser.can_parse(ev.file_path):
                    try:
                        with open(abs_file_path, "rb") as f:
                            raw_content = f.read()
                        parsed = self.parser.parse_source(ev.file_path, raw_content)
                        # Look for entity in current AST
                        for sym in parsed.symbols:
                            if (sym.qualified_name == ent.qualified_name or sym.name == ent.name) and sym.entity_type == ent.entity_type:
                                # Check if AST hash matches
                                target_hash = ev.ast_fingerprint or ent.content_hash
                                if sym.content_hash == target_hash:
                                    # AST is semantically unchanged! Re-align line numbers
                                    ev.line_start = sym.start_line
                                    ev.line_end = sym.end_line
                                    evidence_verified = True
                                    break
                    except Exception as exc:
                        logger.debug("AST verification error: %s", exc)

            # Strategy 2: Snippet Hash Verification
            if not evidence_verified:
                try:
                    with open(abs_file_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()

                    if ev.line_start is not None and ev.line_start <= len(lines):
                        s_idx = max(0, ev.line_start - 1)
                        e_idx = ev.line_end if ev.line_end is not None else ev.line_start
                        snip = "".join(lines[s_idx:e_idx])
                    else:
                        snip = "".join(lines)

                    curr_hash = hashlib.sha256(snip.strip().encode("utf-8")).hexdigest()
                    is_sha256 = (
                        len(ev.evidence_hash) == 64
                        and all(c in "0123456789abcdefABCDEF" for c in ev.evidence_hash)
                        if ev.evidence_hash
                        else False
                    )
                    is_custom_hash = (
                        ev.evidence_hash != f"{ev.file_path}:{ev.line_start or 0}"
                        if ev.evidence_hash
                        else False
                    )
                    expected_hash = (ev.snippet_hash or ev.evidence_hash or "").lower()

                    if (is_sha256 or is_custom_hash) and curr_hash.lower() == expected_hash:
                        evidence_verified = True
                except Exception:
                    evidence_verified = False

            if not evidence_verified:
                all_evidences_intact = False
                break

        target_state = MemoryState.ACTIVE.value if all_evidences_intact else MemoryState.STALE.value
        if has_missing_file:
            target_state = MemoryState.STALE.value

        reason = (
            "Grounded evidence intact and verified against AST"
            if all_evidences_intact
            else "Grounded evidence modified or file missing"
        )

        try:
            ver = MemoryLifecycleManager.transition(memory, target_state, reason=reason)
            if ver:
                session.add(ver)
        except Exception:
            memory.status = target_state

        if all_evidences_intact:
            memory.confidence = min(1.0, memory.confidence + 0.05)

        memory.last_verified_at = datetime.now(UTC)
        memory.updated_at = datetime.now(UTC)
        return memory.status

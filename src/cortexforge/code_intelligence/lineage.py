"""Durable symbol identity across renames and moves (specification section 11).

Name, qualified name and line number are all unstable: they change when someone
renames a function or moves it to another file, while the function itself is the
same function. Identifying symbols by those fields turns every rename into a
delete plus a create, which destroys the grounding of every memory anchored to it.

``SymbolLineage`` gives a symbol a ``logical_id`` that survives those changes. A
rename writes a new row carrying the *same* ``logical_id`` and pointing back at
its predecessor, so the question "what is ``foo()`` called now?" is answerable
from the database by any process, not only by the one that happened to observe
the rename in memory.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.treesitter.semantic_diff import (
    SemanticChange,
    SemanticChangeType,
)
from cortexforge.core.models import CodeEntity, SymbolLineage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineageStep:
    """One observed position of a logical symbol in the project's history."""

    logical_id: str
    qualified_name: str
    file_path: str
    change_kind: str
    predecessor_qualified_name: str | None = None


class SymbolLineageTracker:
    """Maintains logical symbol identity across renames, moves and deletions."""

    async def observe_entities(
        self,
        session: AsyncSession,
        project_id: str,
        entities: list[CodeEntity],
        commit_sha: str | None = None,
        branch: str | None = None,
    ) -> int:
        """Record a lineage origin for any symbol not yet tracked.

        Called after indexing. Symbols already tracked are left alone, so this is
        safe to run on every scan: re-observing an unchanged project writes nothing.
        """
        if not entities:
            return 0

        tracked = await self._tracked_qualified_names(project_id, session)
        created = 0

        for entity in entities:
            if entity.entity_type == "file" or entity.qualified_name in tracked:
                continue
            session.add(
                SymbolLineage(
                    project_id=project_id,
                    logical_id=str(uuid.uuid4()),
                    entity_id=entity.id,
                    name=entity.name,
                    qualified_name=entity.qualified_name,
                    file_path=entity.file_path,
                    entity_type=entity.entity_type,
                    signature=entity.signature,
                    content_hash=entity.content_hash,
                    change_kind="OBSERVED",
                    valid_from_commit=commit_sha,
                    branch=branch,
                )
            )
            tracked.add(entity.qualified_name)
            created += 1

        await session.flush()
        return created

    async def apply_semantic_changes(
        self,
        session: AsyncSession,
        project_id: str,
        semantic_changes: list[SemanticChange],
        commit_sha: str | None = None,
        branch: str | None = None,
    ) -> list[LineageStep]:
        """Extend lineage for renamed, moved and removed symbols.

        A rename or move closes the previous lineage row's validity window and
        opens a new one under the same ``logical_id``: the symbol continued to
        exist, it simply answers to a different name. A removal closes the window
        without opening a successor, which is what makes "this symbol is gone" a
        different fact from "this symbol was renamed".
        """
        steps: list[LineageStep] = []

        for change in semantic_changes:
            if change.change_type in (
                SemanticChangeType.SYMBOL_RENAMED,
                SemanticChangeType.SYMBOL_MOVED,
            ):
                previous_name = (
                    change.details.get("renamed_from")
                    or change.details.get("old_qualified_name")
                    or change.details.get("old_name")
                )
                step = await self._continue_lineage(
                    session, project_id, change, previous_name, commit_sha, branch
                )
                if step is not None:
                    steps.append(step)

            elif change.change_type == SemanticChangeType.SYMBOL_REMOVED:
                closed = await self._close_lineage(
                    session, project_id, change.qualified_name, commit_sha
                )
                if closed is not None:
                    steps.append(
                        LineageStep(
                            logical_id=closed.logical_id,
                            qualified_name=change.qualified_name,
                            file_path=change.file_path,
                            change_kind="REMOVED",
                        )
                    )

        await session.flush()
        return steps

    async def resolve_current_name(
        self, session: AsyncSession, project_id: str, qualified_name: str
    ) -> str | None:
        """Where a symbol lives now, given a name it used to have.

        Returns ``None`` when the symbol was removed rather than renamed, which is
        the distinction that lets reconciliation invalidate a memory about a
        deleted symbol while merely re-anchoring one about a renamed symbol.
        """
        origin = await self._latest_row(session, project_id, qualified_name)
        if origin is None:
            return None

        latest = await session.execute(
            select(SymbolLineage)
            .where(
                SymbolLineage.project_id == project_id,
                SymbolLineage.logical_id == origin.logical_id,
                SymbolLineage.valid_to_commit.is_(None),
            )
            .order_by(SymbolLineage.created_at.desc())
            .limit(1)
        )
        row = latest.scalars().first()
        return row.qualified_name if row is not None else None

    async def history(
        self, session: AsyncSession, project_id: str, qualified_name: str
    ) -> list[SymbolLineage]:
        """Every recorded position of the logical symbol behind a given name."""
        origin = await self._latest_row(session, project_id, qualified_name)
        if origin is None:
            return []
        res = await session.execute(
            select(SymbolLineage)
            .where(
                SymbolLineage.project_id == project_id,
                SymbolLineage.logical_id == origin.logical_id,
            )
            .order_by(SymbolLineage.created_at)
        )
        return list(res.scalars().all())

    # ---------------------------------------------------------------- helpers

    async def _tracked_qualified_names(
        self, project_id: str, session: AsyncSession
    ) -> set[str]:
        res = await session.execute(
            select(SymbolLineage.qualified_name).where(
                SymbolLineage.project_id == project_id,
                SymbolLineage.valid_to_commit.is_(None),
            )
        )
        return set(res.scalars().all())

    async def _latest_row(
        self, session: AsyncSession, project_id: str, qualified_name: str
    ) -> SymbolLineage | None:
        res = await session.execute(
            select(SymbolLineage)
            .where(
                SymbolLineage.project_id == project_id,
                SymbolLineage.qualified_name == qualified_name,
            )
            .order_by(SymbolLineage.created_at.desc())
            .limit(1)
        )
        return res.scalars().first()

    async def _close_lineage(
        self,
        session: AsyncSession,
        project_id: str,
        qualified_name: str,
        commit_sha: str | None,
    ) -> SymbolLineage | None:
        row = await self._latest_row(session, project_id, qualified_name)
        if row is None:
            return None
        row.valid_to_commit = commit_sha or row.valid_to_commit or "unknown"
        return row

    async def _continue_lineage(
        self,
        session: AsyncSession,
        project_id: str,
        change: SemanticChange,
        previous_name: str | None,
        commit_sha: str | None,
        branch: str | None,
    ) -> LineageStep | None:
        predecessor = (
            await self._latest_row(session, project_id, previous_name)
            if previous_name
            else None
        )

        # A rename of a symbol nobody tracked yet still gets an identity, so that
        # the *next* rename has something to continue from.
        logical_id = predecessor.logical_id if predecessor else str(uuid.uuid4())
        if predecessor is not None:
            predecessor.valid_to_commit = commit_sha or "unknown"

        entity = await session.execute(
            select(CodeEntity).where(
                CodeEntity.project_id == project_id,
                CodeEntity.qualified_name == change.qualified_name,
            )
        )
        found = entity.scalars().first()

        session.add(
            SymbolLineage(
                project_id=project_id,
                logical_id=logical_id,
                entity_id=found.id if found else None,
                name=change.symbol_name,
                qualified_name=change.qualified_name,
                file_path=change.file_path,
                entity_type=change.entity_type,
                signature=change.after_signature,
                content_hash=change.after_fingerprint,
                change_kind=change.change_type.value,
                predecessor_id=predecessor.id if predecessor else None,
                valid_from_commit=commit_sha,
                branch=branch,
            )
        )

        return LineageStep(
            logical_id=logical_id,
            qualified_name=change.qualified_name,
            file_path=change.file_path,
            change_kind=change.change_type.value,
            predecessor_qualified_name=previous_name,
        )

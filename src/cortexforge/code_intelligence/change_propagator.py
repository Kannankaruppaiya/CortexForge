"""Semantic change propagation engine mapping AST diffs to code graph and memories.

Implements fine-grained symbol-level change propagation:
- Traverses AST semantic diffs (symbol additions, removals, renames, signature mutations, body changes).
- Supports both Git commit diffs and database-snapshot diffing for untracked working trees.
- Propagates blast radius across the entity dependency and caller graph.
- Evaluates memory grounding at the symbol and line-range level:
  A memory about function_a() does NOT become stale merely because function_b() in the same file changed.
- Enforces valid lifecycle state transitions via MemoryLifecycleManager.
"""

import os
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.git_provider import GitProvider
from cortexforge.code_intelligence.treesitter.semantic_diff import (
    ASTSemanticDiffer,
    SemanticChange,
    SemanticChangeType,
)
from cortexforge.core.models import CodeEntity, Memory, Project
from cortexforge.graph.service import GraphService
from cortexforge.memory.lifecycle import MemoryLifecycleManager, MemoryState


@dataclass
class ChangeImpactReport:
    modified_files: list[str]
    directly_changed_entities: list[str]
    affected_dependents: list[str]
    memories_flagged_stale: list[str]
    critical_constraints: list[str]
    semantic_changes: list[SemanticChange] = field(default_factory=list)
    memories_retained_active: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SemanticChangePropagator:
    """Propagates code modifications across the graph to invalidate or flag stale memories with symbol precision."""

    def __init__(
        self,
        graph_service: GraphService | None = None,
        differ: ASTSemanticDiffer | None = None,
    ) -> None:
        self.graph_service = graph_service or GraphService()
        self.differ = differ or ASTSemanticDiffer()

    async def analyze_change(
        self,
        session: AsyncSession,
        project_id: str,
        modified_files: list[str],
        mark_stale: bool = True,
        base_commit: str | None = None,
    ) -> ChangeImpactReport:
        """Alias for propagate_changes."""
        return await self.propagate_changes(
            session, project_id, modified_files, mark_stale=mark_stale, base_commit=base_commit
        )

    async def propagate_changes(
        self,
        session: AsyncSession,
        project_id: str,
        modified_files: list[str] | None = None,
        mark_stale: bool = True,
        base_commit: str | None = None,
        changed_files: list[str] | None = None,
        commit_base: str | None = None,
    ) -> ChangeImpactReport:
        """Analyze impact of modified files and update affected memory states with symbol-level precision."""
        target_files = modified_files if modified_files is not None else (changed_files or [])
        base = base_commit if base_commit is not None else commit_base
        normalized_files = [f.replace("\\", "/") for f in target_files]

        project = await session.get(Project, project_id)

        # 1. Fetch all existing entities from DB
        entities_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        res = await session.execute(entities_stmt)
        all_entities = list(res.scalars().all())

        # 2. Compute fine-grained AST semantic changes per file
        all_semantic_changes: list[SemanticChange] = []
        if project and project.local_path and os.path.exists(project.local_path):
            git = GitProvider(project.local_path)
            target_base = base or project.last_indexed_commit or "HEAD"

            for rel_file in normalized_files:

                abs_file = os.path.join(project.local_path, rel_file.replace("/", os.sep))
                after_content: bytes | None = None
                if os.path.exists(abs_file):
                    try:
                        with open(abs_file, "rb") as f:
                            after_content = f.read()
                    except OSError:
                        after_content = None

                before_content = git.get_file_content_at_commit(target_base, rel_file)
                db_file_entities = [
                    e for e in all_entities
                    if e.file_path == rel_file or rel_file.endswith(e.file_path.replace("\\", "/"))
                ]

                # If git has no before_content, but database already has entities for this file:
                # Compare against the database entity state!
                if before_content is None and db_file_entities and after_content is not None:
                    if self.differ.parser.can_parse(rel_file):
                        parsed_after = self.differ.parser.parse_source(rel_file, after_content)
                        after_sym_map = {
                            s.qualified_name: s for s in parsed_after.symbols if s.entity_type != "file"
                        }
                        # Compare each DB entity against the newly parsed symbols
                        for ent in db_file_entities:
                            if ent.entity_type == "file":
                                continue
                            matching_sym = after_sym_map.get(ent.qualified_name) or next(
                                (s for s in parsed_after.symbols if s.name == ent.name and s.entity_type == ent.entity_type),
                                None
                            )
                            if not matching_sym:
                                all_semantic_changes.append(
                                    SemanticChange(
                                        commit_sha=target_base,
                                        file_path=rel_file,
                                        change_type=SemanticChangeType.SYMBOL_REMOVED,
                                        symbol_name=ent.name,
                                        qualified_name=ent.qualified_name,
                                        entity_type=ent.entity_type,
                                        before_fingerprint=ent.content_hash,
                                        before_signature=ent.signature,
                                        before_line_range=(ent.start_line, ent.end_line),
                                    )
                                )
                            elif matching_sym.content_hash != ent.content_hash or matching_sym.signature != ent.signature:
                                sig_changed = matching_sym.signature != ent.signature
                                c_type = (
                                    SemanticChangeType.SIGNATURE_CHANGED
                                    if sig_changed
                                    else SemanticChangeType.BODY_CHANGED
                                )
                                all_semantic_changes.append(
                                    SemanticChange(
                                        commit_sha=target_base,
                                        file_path=rel_file,
                                        change_type=c_type,
                                        symbol_name=matching_sym.name,
                                        qualified_name=matching_sym.qualified_name,
                                        entity_type=matching_sym.entity_type,
                                        before_fingerprint=ent.content_hash,
                                        after_fingerprint=matching_sym.content_hash,
                                        before_signature=ent.signature,
                                        after_signature=matching_sym.signature,
                                        before_line_range=(ent.start_line, ent.end_line),
                                        after_line_range=(matching_sym.start_line, matching_sym.end_line),
                                    )
                                )
                else:
                    changes = self.differ.diff_file_contents(
                        file_path=rel_file,
                        before_content=before_content,
                        after_content=after_content,
                        commit_sha=target_base,
                    )
                    all_semantic_changes.extend(changes)

        # 3. Extract changed symbols and line ranges
        changed_symbol_qnames: set[str] = set()
        changed_symbol_names: set[str] = set()
        changed_file_ranges: dict[str, list[tuple[int, int]]] = {}

        for ch in all_semantic_changes:
            if ch.change_type in (
                SemanticChangeType.SYMBOL_REMOVED,
                SemanticChangeType.SIGNATURE_CHANGED,
                SemanticChangeType.BODY_CHANGED,
                SemanticChangeType.CLASS_CHANGED,
                SemanticChangeType.INHERITANCE_CHANGED,
                SemanticChangeType.ROUTE_CHANGED,
            ):
                changed_symbol_qnames.add(ch.qualified_name)
                changed_symbol_names.add(ch.symbol_name)
                if ch.after_line_range:
                    changed_file_ranges.setdefault(ch.file_path, []).append(ch.after_line_range)
                elif ch.before_line_range:
                    changed_file_ranges.setdefault(ch.file_path, []).append(ch.before_line_range)

        # 4. Match directly changed entities in database
        directly_changed_entities: list[CodeEntity] = []
        for ent in all_entities:
            ent_file_norm = ent.file_path.replace("\\", "/")
            if any(ent_file_norm == nf or nf.endswith(ent_file_norm) for nf in normalized_files):
                directly_changed_entities.append(ent)

        # 5. Graph propagation: compute blast radius (dependents and upstream callers)
        affected_dependent_names: set[str] = set()
        for cent in directly_changed_entities:
            callers = await self.graph_service.get_dependents(
                session, project_id=project_id, entity_name_or_id=cent.id, depth=2
            )
            for c in callers:
                affected_dependent_names.add(f"{c['name']} ({c['file']})")

        # 6. Evaluate affected memories grounded in changed symbols or files
        memories_stmt = (
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(["ACTIVE", "UNVERIFIED"]),
            )
        )
        mres = await session.execute(memories_stmt)
        active_memories = list(mres.scalars().all())

        stale_memory_titles: list[str] = []
        retained_active_titles: list[str] = []
        critical_constraints: list[str] = []
        warnings: list[str] = []

        for mem in active_memories:
            grounded_in_change = False
            relevant_to_file = False

            if mem.evidences:
                for ev in mem.evidences:
                    ev_norm = ev.file_path.replace("\\", "/")
                    is_file_match = any(ev_norm == nf or nf.endswith(ev_norm) for nf in normalized_files)
                    if not is_file_match:
                        continue

                    relevant_to_file = True

                    # Check 1: Direct symbol grounding
                    if ev.symbol_id:
                        matched_ent = next((e for e in all_entities if e.id == ev.symbol_id), None)
                        if matched_ent:
                            if matched_ent.qualified_name in changed_symbol_qnames or matched_ent.name in changed_symbol_names:
                                grounded_in_change = True
                                break
                            # Grounded symbol was verified untouched!
                            continue

                    # Check 2: Line range overlap with changed AST nodes
                    file_ranges = changed_file_ranges.get(ev_norm, [])
                    if file_ranges and ev.line_start is not None:
                        ev_s = ev.line_start
                        ev_e = ev.line_end or ev.line_start
                        overlaps = any(not (ev_e < r_start or ev_s > r_end) for (r_start, r_end) in file_ranges)
                        if overlaps:
                            grounded_in_change = True
                            break
                        # No overlap with modified nodes in this file
                        continue

                    # Check 3: File modified with changes, treat as grounded change
                    grounded_in_change = True
                    break

            if grounded_in_change:
                stale_memory_titles.append(f"[{mem.memory_type}] {mem.title}")
                if mem.memory_type == "CONSTRAINT":
                    critical_constraints.append(f"CONSTRAINT: {mem.title} - {mem.summary}")
                elif mem.memory_type == "FAILURE":
                    warnings.append(f"KNOWN FAILURE AREA: {mem.title} - {mem.summary}")

                if mark_stale:
                    ver = MemoryLifecycleManager.transition(
                        mem,
                        new_state=MemoryState.STALE.value,
                        reason="Grounded code symbol was modified in recent diff",
                    )
                    if ver:
                        session.add(ver)
            elif relevant_to_file:
                retained_active_titles.append(f"[{mem.memory_type}] {mem.title}")

        if mark_stale and stale_memory_titles:
            await session.commit()

        return ChangeImpactReport(
            modified_files=normalized_files,
            directly_changed_entities=[e.qualified_name for e in directly_changed_entities],
            affected_dependents=sorted(affected_dependent_names),
            memories_flagged_stale=stale_memory_titles,
            critical_constraints=critical_constraints,
            semantic_changes=all_semantic_changes,
            memories_retained_active=retained_active_titles,
            warnings=warnings,
        )

"""Change impact analysis: what a diff touched (specification sections 10 and 12).

This module answers a mechanical question -- given these modified files, which
symbols changed, which graph neighbours are downstream, and what should the
durable ``ChangeSet`` record say? It deliberately no longer answers the cognitive
question of what to *believe* afterwards; that belongs to
:mod:`cortexforge.reconciliation.engine`, which records its decisions with reasons.

Keeping the two apart is what makes each testable. Change analysis is compared
against the repository; reconciliation is compared against expected cognitive
outcomes (section 50). Previously a single function did both, and its conclusions
existed only as strings in a returned dataclass.

Diffing works from git history where it exists and from the indexed entity state
otherwise, so an uncommitted working tree is analysed with the same precision as a
commit.
"""

import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.architecture.invariants import ArchitectureInvariantEngine
from cortexforge.code_intelligence.changesets import ChangeSetRecorder
from cortexforge.code_intelligence.git_provider import GitProvider
from cortexforge.code_intelligence.lineage import SymbolLineageTracker
from cortexforge.code_intelligence.treesitter.semantic_diff import (
    ASTSemanticDiffer,
    SemanticChange,
    SemanticChangeType,
    are_symbols_lineage_match,
)
from cortexforge.cognition.epistemics import DecisionCode
from cortexforge.core.models import CodeEntity, Project
from cortexforge.graph.service import GraphService
from cortexforge.reconciliation.engine import MemoryReconciliationEngine


@dataclass
class ChangeImpactReport:
    """What the change touched, and what reconciliation concluded about it."""

    modified_files: list[str]
    directly_changed_entities: list[str]
    affected_dependents: list[str]
    memories_flagged_stale: list[str]
    critical_constraints: list[str]
    semantic_changes: list[SemanticChange] = field(default_factory=list)
    memories_retained_active: list[str] = field(default_factory=list)
    memories_reanchored: list[str] = field(default_factory=list)
    memories_invalidated: list[str] = field(default_factory=list)
    # Memories whose fate could not be decided from the repository. Reported
    # explicitly rather than being silently counted as unaffected (section 30).
    memories_unknown: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    propagation_confidence: str = (
        "EXACT"  # "EXACT" | "INFERRED" | "UNKNOWN_CHANGE_SCOPE" (Item 13)
    )
    change_set_id: str | None = None
    verification_run_id: str | None = None
    # The full reconciliation record: decision code, reason code and reason per
    # memory, so callers can explain the outcome rather than infer it.
    decisions: list[dict[str, Any]] = field(default_factory=list)
    reanchored_symbols: list[str] = field(default_factory=list)


class SemanticChangePropagator:
    """Computes the blast radius of a change and hands it to reconciliation."""

    def __init__(
        self,
        graph_service: GraphService | None = None,
        differ: ASTSemanticDiffer | None = None,
        reconciler: MemoryReconciliationEngine | None = None,
        changesets: ChangeSetRecorder | None = None,
        lineage: SymbolLineageTracker | None = None,
    ) -> None:
        self.graph_service = graph_service or GraphService()
        self.differ = differ or ASTSemanticDiffer()
        self.reconciler = reconciler or MemoryReconciliationEngine()
        self.changesets = changesets or ChangeSetRecorder()
        self.lineage = lineage or SymbolLineageTracker()

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
            session,
            project_id,
            modified_files,
            mark_stale=mark_stale,
            base_commit=base_commit,
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
        branch: str | None = None,
        workspace: str | None = None,
    ) -> ChangeImpactReport:
        """Analyse a set of modified files and reconcile project memory against it."""
        target_files = (
            modified_files if modified_files is not None else (changed_files or [])
        )
        base = base_commit if base_commit is not None else commit_base
        normalized_files = [f.replace("\\", "/") for f in target_files]

        project = await session.get(Project, project_id)

        # `target_base` is the state being compared *from*; `head_commit` the state
        # compared *to*. When the base resolves to HEAD there is no committed
        # baseline, so this is a working-tree comparison -- knowledge derived from
        # it is provisional, not committed truth (section 19).
        head_commit = project.last_indexed_commit if project else None
        is_working_tree = False

        # 1. Fetch all existing entities from DB
        entities_stmt = select(CodeEntity).where(CodeEntity.project_id == project_id)
        res = await session.execute(entities_stmt)
        all_entities = list(res.scalars().all())

        # 2. Compute fine-grained AST semantic changes per file
        all_semantic_changes: list[SemanticChange] = []
        target_base = base or head_commit or "HEAD"
        if project and project.local_path and os.path.exists(project.local_path):
            git = GitProvider(project.local_path)
            target_base = base or project.last_indexed_commit or "HEAD"
            is_working_tree = target_base == "HEAD"
            head_commit = git.get_head_commit() or head_commit

            for rel_file in normalized_files:
                abs_file = os.path.join(
                    project.local_path, rel_file.replace("/", os.sep)
                )
                after_content: bytes | None = None
                if os.path.exists(abs_file):
                    try:
                        with open(abs_file, "rb") as f:
                            after_content = f.read()
                    except OSError:
                        after_content = None

                before_content = git.get_file_content_at_commit(target_base, rel_file)
                db_file_entities = [
                    e
                    for e in all_entities
                    if e.file_path == rel_file
                    or rel_file.endswith(e.file_path.replace("\\", "/"))
                ]

                # If git has no before_content, but database already has entities for this file:
                # Compare against the database entity state!
                if (
                    before_content is None
                    and db_file_entities
                    and after_content is not None
                ):
                    if self.differ.parser.can_parse(rel_file):
                        parsed_after = self.differ.parser.parse_source(
                            rel_file, after_content
                        )
                        after_sym_map = {
                            s.qualified_name: s
                            for s in parsed_after.symbols
                            if s.entity_type != "file"
                        }
                        # Compare each DB entity against the newly parsed symbols
                        for ent in db_file_entities:
                            if ent.entity_type == "file":
                                continue
                            matching_sym = after_sym_map.get(
                                ent.qualified_name
                            ) or next(
                                (
                                    s
                                    for s in parsed_after.symbols
                                    if s.name == ent.name
                                    and s.entity_type == ent.entity_type
                                ),
                                None,
                            )
                            if not matching_sym:
                                # Check if symbol was renamed (same body hash or signature)
                                renamed_to = next(
                                    (
                                        s
                                        for s in parsed_after.symbols
                                        if are_symbols_lineage_match(ent, s)
                                    ),
                                    None,
                                )
                                if renamed_to:
                                    all_semantic_changes.append(
                                        SemanticChange(
                                            commit_sha=target_base,
                                            file_path=rel_file,
                                            change_type=SemanticChangeType.SYMBOL_RENAMED,
                                            symbol_name=renamed_to.name,
                                            qualified_name=renamed_to.qualified_name,
                                            entity_type=renamed_to.entity_type,
                                            before_fingerprint=ent.content_hash,
                                            after_fingerprint=renamed_to.content_hash,
                                            before_signature=ent.signature,
                                            after_signature=renamed_to.signature,
                                            before_line_range=(
                                                ent.start_line,
                                                ent.end_line,
                                            ),
                                            after_line_range=(
                                                renamed_to.start_line,
                                                renamed_to.end_line,
                                            ),
                                            details={
                                                "renamed_from": ent.qualified_name,
                                                "old_name": ent.name,
                                            },
                                        )
                                    )
                                else:
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
                                            before_line_range=(
                                                ent.start_line,
                                                ent.end_line,
                                            ),
                                        )
                                    )
                            elif (
                                matching_sym.content_hash != ent.content_hash
                                or matching_sym.signature != ent.signature
                            ):
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
                                        before_line_range=(
                                            ent.start_line,
                                            ent.end_line,
                                        ),
                                        after_line_range=(
                                            matching_sym.start_line,
                                            matching_sym.end_line,
                                        ),
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
                    changed_file_ranges.setdefault(ch.file_path, []).append(
                        ch.after_line_range
                    )
                elif ch.before_line_range:
                    changed_file_ranges.setdefault(ch.file_path, []).append(
                        ch.before_line_range
                    )

        # 4. Match directly changed entities in database
        directly_changed_entities: list[CodeEntity] = []
        for ent in all_entities:
            ent_file_norm = ent.file_path.replace("\\", "/")
            if any(
                ent_file_norm == nf or nf.endswith(ent_file_norm)
                for nf in normalized_files
            ):
                directly_changed_entities.append(ent)

        # 5. Graph propagation: compute blast radius (dependents and upstream callers)
        affected_dependent_names: set[str] = set()
        for cent in directly_changed_entities:
            callers = await self.graph_service.get_dependents(
                session, project_id=project_id, entity_name_or_id=cent.id, depth=2
            )
            for c in callers:
                affected_dependent_names.add(f"{c['name']} ({c['file']})")

        # 6. Cognitive reconciliation.
        #
        # Deciding what to believe is a separate concern from working out what the
        # diff touched, and it lives in its own engine. This function's job ends at
        # producing an accurate description of the change; the reconciliation
        # engine decides -- and records, with reasons -- what that change means for
        # each memory.
        change_set, _ = await self.changesets.record(
            session,
            project_id=project_id,
            file_paths=normalized_files,
            semantic_changes=all_semantic_changes,
            base_commit_sha=None if target_base == "HEAD" else target_base,
            target_commit_sha=head_commit or "HEAD",
            is_working_tree=is_working_tree,
            branch=branch,
            workspace=workspace,
            existing_files={
                nf
                for nf in normalized_files
                if project
                and os.path.exists(
                    os.path.join(project.local_path, nf.replace("/", os.sep))
                )
            },
        )

        # 7. Extend symbol lineage so that a rename stays a rename for every later
        #    process, not just for this one in-memory pass.
        await self.lineage.apply_semantic_changes(
            session,
            project_id,
            all_semantic_changes,
            commit_sha=head_commit,
            branch=branch,
        )

        reconciliation = await self.reconciler.reconcile(
            session,
            project_id=project_id,
            semantic_changes=all_semantic_changes,
            changed_files=normalized_files,
            change_set_id=change_set.id,
            commit_sha=head_commit,
            branch=branch,
            workspace=workspace,
            apply_transitions=mark_stale,
        )

        # 8. Architecture invariants are evaluated against the updated graph.
        arch_engine = ArchitectureInvariantEngine()
        eval_result = await arch_engine.evaluate_rules(
            session, project_id, commit_sha=target_base, persist_violations=True
        )

        critical_constraints: list[str] = []
        warnings: list[str] = []
        for violation in eval_result.violations_detected:
            critical_constraints.append(f"VIOLATION: {violation['details']}")
            warnings.append(
                f"ARCH VIOLATION ({violation['severity']}): {violation['rule_name']} - "
                f"{violation['source']} -> {violation['target']}"
            )

        # 9. Translate reconciliation decisions into the report shape callers use.
        def titles(decision: str) -> list[str]:
            return [o.memory_title for o in reconciliation.by_decision(decision)]

        for outcome in reconciliation.outcomes:
            if outcome.decision in (
                DecisionCode.REVISE.value,
                DecisionCode.STALE.value,
            ):
                warnings.append(f"{outcome.reason_code}: {outcome.reason}")
            elif outcome.decision == DecisionCode.CONFLICT.value:
                critical_constraints.append(f"CONFLICT: {outcome.reason}")

        await session.commit()

        # Determine propagation confidence (Item 13)
        semantic_files = {sc.file_path for sc in all_semantic_changes}
        if not normalized_files:
            prop_confidence = "UNKNOWN_CHANGE_SCOPE"
        elif all_semantic_changes and set(normalized_files).issubset(semantic_files):
            prop_confidence = "EXACT"
        elif all_semantic_changes or directly_changed_entities:
            prop_confidence = "INFERRED"
        else:
            prop_confidence = "UNKNOWN_CHANGE_SCOPE"

        return ChangeImpactReport(
            modified_files=normalized_files,
            directly_changed_entities=[
                e.qualified_name for e in directly_changed_entities
            ],
            affected_dependents=sorted(affected_dependent_names),
            memories_flagged_stale=titles(DecisionCode.REVISE.value)
            + titles(DecisionCode.STALE.value),
            critical_constraints=critical_constraints,
            semantic_changes=all_semantic_changes,
            memories_retained_active=titles(DecisionCode.KEEP.value),
            memories_reanchored=titles(DecisionCode.REANCHOR.value),
            memories_invalidated=titles(DecisionCode.INVALIDATE.value),
            memories_unknown=titles(DecisionCode.UNKNOWN.value),
            warnings=warnings,
            propagation_confidence=prop_confidence,
            change_set_id=change_set.id,
            verification_run_id=reconciliation.verification_run_id,
            decisions=[
                {
                    "memory_id": o.memory_id,
                    "memory_title": o.memory_title,
                    "decision": o.decision,
                    "reason_code": o.reason_code,
                    "reason": o.reason,
                    "claim_id": o.claim_id,
                    "previous_status": o.previous_status,
                    "new_status": o.new_status,
                }
                for o in reconciliation.outcomes
            ],
            reanchored_symbols=reconciliation.reanchored_symbols,
        )

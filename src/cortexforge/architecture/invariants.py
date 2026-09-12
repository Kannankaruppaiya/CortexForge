"""Architecture Invariant Engine enforcing structural rules and anti-patterns.

Adheres strictly to Specification Section 8:
- controller MUST NOT access database directly
- UI MUST NOT access repository directly
- domain MUST NOT import framework infrastructure
- security-sensitive code requires specific verification
- Evaluates rules against graph relationships and detects violations with provenance.
"""

import fnmatch
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.core.models import (
    ArchitectureRule,
    CodeEntity,
    Relationship,
    RuleViolation,
)


@dataclass
class ArchitectureEvaluationResult:
    total_rules_evaluated: int
    violations_detected: list[dict[str, str]] = field(default_factory=list)
    has_critical_violations: bool = False


class ArchitectureInvariantEngine:
    """Evaluates architectural boundary rules against code entities and relationships."""

    @staticmethod
    def _matches_pattern(pattern: str, text: str) -> bool:
        """Check if pattern matches text using glob or substring match."""
        pat = pattern.strip().lower()
        txt = text.strip().lower()
        if not pat or not txt:
            return False
        # Normalize slashes
        txt = txt.replace("\\", "/")
        pat = pat.replace("\\", "/")
        if "*" in pat or "?" in pat:
            return fnmatch.fnmatch(txt, pat) or fnmatch.fnmatch(txt.split("/")[-1], pat)
        return pat in txt

    async def evaluate_rules(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str | None = None,
        persist_violations: bool = True,
    ) -> ArchitectureEvaluationResult:
        """Evaluate all active architecture rules against project graph relationships."""
        rules_stmt = select(ArchitectureRule).where(
            ArchitectureRule.project_id == project_id,
            ArchitectureRule.enforcement_status == "ACTIVE",
        )
        rules_res = await session.execute(rules_stmt)
        rules = list(rules_res.scalars().all())

        if not rules:
            return ArchitectureEvaluationResult(total_rules_evaluated=0)

        # Relationships and entities are fetched separately and joined in memory:
        # a single self-joined query would need two aliases of code_entities, and
        # the two-query form keeps the entity map reusable across every rule.
        all_rels_stmt = select(Relationship).where(
            Relationship.project_id == project_id
        )
        all_rels = list((await session.execute(all_rels_stmt)).scalars().all())

        all_entities_stmt = select(CodeEntity).where(
            CodeEntity.project_id == project_id
        )
        all_entities = list((await session.execute(all_entities_stmt)).scalars().all())
        entity_map = {e.id: e for e in all_entities}

        now = datetime.now(UTC)
        rule_ids = [r.id for r in rules]
        existing_open: dict[tuple[str, str, str], RuleViolation] = {}
        if persist_violations:
            open_stmt = select(RuleViolation).where(
                RuleViolation.rule_id.in_(rule_ids),
                RuleViolation.resolved_at.is_(None),
            )
            open_res = await session.execute(open_stmt)
            existing_open = {
                (v.rule_id, v.source_entity_id, v.target_entity_id): v
                for v in open_res.scalars().all()
            }

        detected_violations: list[dict[str, str]] = []
        detected_keys: set[tuple[str, str, str]] = set()
        has_critical = False

        for rule in rules:
            modality = (rule.modality or "MUST_NOT").upper()
            src_pat = rule.forbidden_source_pattern
            tgt_pat = rule.forbidden_target_pattern

            if modality == "MUST_NOT":
                for rel in all_rels:
                    src = entity_map.get(rel.source_entity_id)
                    tgt = entity_map.get(rel.target_entity_id)
                    if not src or not tgt:
                        continue

                    src_matches = self._matches_pattern(
                        src_pat, src.qualified_name
                    ) or self._matches_pattern(src_pat, src.file_path)
                    tgt_matches = self._matches_pattern(
                        tgt_pat, tgt.qualified_name
                    ) or self._matches_pattern(tgt_pat, tgt.file_path)

                    if src_matches and tgt_matches:
                        details = (
                            f"Architecture violation [{rule.severity}] [MUST_NOT]: '{src.qualified_name}' "
                            f"({src.file_path}) has forbidden '{rel.relationship_type}' relationship "
                            f"to '{tgt.qualified_name}' ({tgt.file_path}) violating rule '{rule.rule_name}'."
                        )
                        v_key = (rule.id, src.id, tgt.id)
                        detected_keys.add(v_key)
                        if v_key not in existing_open and persist_violations:
                            violation = RuleViolation(
                                rule_id=rule.id,
                                source_entity_id=src.id,
                                target_entity_id=tgt.id,
                                commit_sha=commit_sha,
                                violation_details=details,
                            )
                            session.add(violation)

                        detected_violations.append(
                            {
                                "rule_id": rule.id,
                                "rule_name": rule.rule_name,
                                "severity": rule.severity,
                                "source": src.qualified_name,
                                "target": tgt.qualified_name,
                                "details": details,
                            }
                        )

                        if rule.severity.upper() in ("ERROR", "CRITICAL"):
                            has_critical = True

            elif modality == "ONLY_IF":
                for rel in all_rels:
                    src = entity_map.get(rel.source_entity_id)
                    tgt = entity_map.get(rel.target_entity_id)
                    if not src or not tgt:
                        continue

                    tgt_matches = self._matches_pattern(
                        tgt_pat, tgt.qualified_name
                    ) or self._matches_pattern(tgt_pat, tgt.file_path)
                    if tgt_matches:
                        src_matches = self._matches_pattern(
                            src_pat, src.qualified_name
                        ) or self._matches_pattern(src_pat, src.file_path)
                        if not src_matches:
                            details = (
                                f"Architecture violation [{rule.severity}] [ONLY_IF]: '{tgt.qualified_name}' "
                                f"({tgt.file_path}) may ONLY be accessed by '{src_pat}', "
                                f"but was accessed via '{rel.relationship_type}' by '{src.qualified_name}' ({src.file_path}) "
                                f"violating rule '{rule.rule_name}'."
                            )
                            v_key = (rule.id, src.id, tgt.id)
                            detected_keys.add(v_key)
                            if v_key not in existing_open and persist_violations:
                                violation = RuleViolation(
                                    rule_id=rule.id,
                                    source_entity_id=src.id,
                                    target_entity_id=tgt.id,
                                    commit_sha=commit_sha,
                                    violation_details=details,
                                )
                                session.add(violation)

                            detected_violations.append(
                                {
                                    "rule_id": rule.id,
                                    "rule_name": rule.rule_name,
                                    "severity": rule.severity,
                                    "source": src.qualified_name,
                                    "target": tgt.qualified_name,
                                    "details": details,
                                }
                            )

                            if rule.severity.upper() in ("ERROR", "CRITICAL"):
                                has_critical = True

            elif modality in ("MUST", "REQUIRES"):
                matching_sources = [
                    e
                    for e in all_entities
                    if self._matches_pattern(src_pat, e.qualified_name)
                    or self._matches_pattern(src_pat, e.file_path)
                ]
                for src in matching_sources:
                    out_rels = [r for r in all_rels if r.source_entity_id == src.id]
                    has_required_target = any(
                        (
                            entity_map.get(r.target_entity_id) is not None
                            and (
                                self._matches_pattern(
                                    tgt_pat,
                                    entity_map[r.target_entity_id].qualified_name,
                                )
                                or self._matches_pattern(
                                    tgt_pat, entity_map[r.target_entity_id].file_path
                                )
                            )
                        )
                        for r in out_rels
                    )
                    if not has_required_target:
                        details = (
                            f"Architecture violation [{rule.severity}] [{modality}]: '{src.qualified_name}' "
                            f"({src.file_path}) {modality} have a relationship to a target matching "
                            f"'{tgt_pat}', but none was found violating rule '{rule.rule_name}'."
                        )
                        v_key = (rule.id, src.id, src.id)
                        detected_keys.add(v_key)
                        if v_key not in existing_open and persist_violations:
                            violation = RuleViolation(
                                rule_id=rule.id,
                                source_entity_id=src.id,
                                target_entity_id=src.id,
                                commit_sha=commit_sha,
                                violation_details=details,
                            )
                            session.add(violation)

                        detected_violations.append(
                            {
                                "rule_id": rule.id,
                                "rule_name": rule.rule_name,
                                "severity": rule.severity,
                                "source": src.qualified_name,
                                "target": "<NONE>",
                                "details": details,
                            }
                        )

                        if rule.severity.upper() in ("ERROR", "CRITICAL"):
                            has_critical = True

            elif modality == "SHOULD":
                for rel in all_rels:
                    src = entity_map.get(rel.source_entity_id)
                    tgt = entity_map.get(rel.target_entity_id)
                    if not src or not tgt:
                        continue

                    src_matches = self._matches_pattern(
                        src_pat, src.qualified_name
                    ) or self._matches_pattern(src_pat, src.file_path)
                    tgt_matches = self._matches_pattern(
                        tgt_pat, tgt.qualified_name
                    ) or self._matches_pattern(tgt_pat, tgt.file_path)

                    if src_matches and tgt_matches:
                        severity = "WARNING"
                        details = (
                            f"Architecture advisory [SHOULD]: '{src.qualified_name}' "
                            f"({src.file_path}) has discouraged '{rel.relationship_type}' relationship "
                            f"to '{tgt.qualified_name}' ({tgt.file_path}) for rule '{rule.rule_name}'."
                        )
                        v_key = (rule.id, src.id, tgt.id)
                        detected_keys.add(v_key)
                        if v_key not in existing_open and persist_violations:
                            violation = RuleViolation(
                                rule_id=rule.id,
                                source_entity_id=src.id,
                                target_entity_id=tgt.id,
                                commit_sha=commit_sha,
                                violation_details=details,
                            )
                            session.add(violation)

                        detected_violations.append(
                            {
                                "rule_id": rule.id,
                                "rule_name": rule.rule_name,
                                "severity": severity,
                                "source": src.qualified_name,
                                "target": tgt.qualified_name,
                                "details": details,
                            }
                        )

        if persist_violations:
            # Mark violations that were resolved at this evaluation (§30)
            for k, old_v in existing_open.items():
                if k not in detected_keys:
                    old_v.resolved_at = now
            await session.commit()

        return ArchitectureEvaluationResult(
            total_rules_evaluated=len(rules),
            violations_detected=detected_violations,
            has_critical_violations=has_critical,
        )

    async def check_project_invariants(
        self,
        session: AsyncSession,
        project_id: str,
        commit_sha: str | None = None,
        active_only: bool = True,
    ) -> list[RuleViolation]:
        """Evaluate rules and return list of persisted RuleViolation records."""
        await self.evaluate_rules(
            session, project_id, commit_sha=commit_sha, persist_violations=True
        )
        stmt = (
            select(RuleViolation)
            .join(ArchitectureRule, RuleViolation.rule_id == ArchitectureRule.id)
            .where(ArchitectureRule.project_id == project_id)
        )
        if active_only:
            stmt = stmt.where(RuleViolation.resolved_at.is_(None))
        res = await session.execute(stmt)
        return list(res.scalars().all())

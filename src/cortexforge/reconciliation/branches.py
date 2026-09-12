"""Branch, merge and rebase cognition (specification sections 19 and 20).

Two branches can hold different beliefs about the same project and both be right,
because they describe different code. A merge is therefore a cognitive event, not
just a code event: the resulting branch has to decide what it now believes.

The distinction that makes this tractable is the merge base. Knowledge recorded
*before* the point two branches diverged is common ground and cannot conflict.
Only what each side learned after that point can genuinely disagree, so that is
all this engine considers. Without it, every merge would look like a mass
contradiction between two copies of the same history.

Resolution never guesses. It applies, in order:

1. **Scope** -- knowledge scoped to a branch that is being deleted does not
   survive it; knowledge scoped to the project does.
2. **Commit lineage** -- if one side's grounding commit is an ancestor of the
   other's, that is succession, not conflict (section 18).
3. **Authority** -- a user decision outranks an agent observation regardless of
   which branch it was made on (section 6).
4. **Evidence** -- code-grounded knowledge outranks unevidenced assertion.

When none of these decides, both sides are marked CONFLICTED and the
disagreement is surfaced. Picking a winner by branch name, or by whichever was
merged last, would be arbitrary dressed up as a decision.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.code_intelligence.git_provider import GitProvider
from cortexforge.cognition.authority import (
    authority_from_source,
    resolve_authority_conflict,
)
from cortexforge.cognition.epistemics import DecisionCode, ReasonCode
from cortexforge.core.models import Memory, MemoryDecision, Project
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.reconciliation.engine import MemoryReconciliationEngine

logger = logging.getLogger(__name__)

# The git events this engine understands.
EVENT_MERGE = "MERGE"
EVENT_REBASE = "REBASE"
EVENT_CHERRY_PICK = "CHERRY_PICK"
EVENT_FORCE_PUSH = "FORCE_PUSH"
EVENT_BRANCH_DELETED = "BRANCH_DELETED"


@dataclass
class BranchOutcome:
    """What happened to one memory as a result of a branch event."""

    memory_id: str
    memory_title: str
    branch: str | None
    decision: str
    reason_code: str
    reason: str
    counterpart_memory_id: str | None = None


@dataclass
class BranchReconciliationReport:
    """The cognitive result of a merge, rebase or branch deletion."""

    project_id: str
    event: str
    ours: str | None = None
    theirs: str | None = None
    merge_base: str | None = None
    outcomes: list[BranchOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_decision(self, decision: str) -> list[BranchOutcome]:
        return [o for o in self.outcomes if o.decision == decision]

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.decision] = counts.get(outcome.decision, 0) + 1
        return counts


class BranchReconciliationEngine:
    """Reconciles cognitive state across merges, rebases and branch deletions."""

    def __init__(
        self,
        conflict_resolver: ConflictResolver | None = None,
        reconciler: MemoryReconciliationEngine | None = None,
    ) -> None:
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.reconciler = reconciler or MemoryReconciliationEngine()

    async def reconcile_merge(
        self,
        session: AsyncSession,
        project_id: str,
        ours: str,
        theirs: str,
        merge_commit: str | None = None,
        apply_transitions: bool = True,
    ) -> BranchReconciliationReport:
        """Reconcile what two branches learned since they diverged.

        Only knowledge recorded after the merge base is considered. Everything
        earlier is shared history and cannot be in conflict with itself.
        """
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist.")

        report = BranchReconciliationReport(
            project_id=project_id, event=EVENT_MERGE, ours=ours, theirs=theirs
        )

        git = GitProvider(project.local_path)
        merge_base = git.get_merge_base(ours, theirs)
        report.merge_base = merge_base

        if merge_base is None:
            report.notes.append(
                f"No merge base between '{ours}' and '{theirs}'. Without a shared "
                "ancestor there is no way to tell divergence from unrelated history, "
                "so no memory was reconciled."
            )
            return report

        # Commits each side made since diverging. Knowledge grounded in one of
        # these is knowledge that side learned independently.
        ours_commits = set(git.commits_between(merge_base, ours))
        theirs_commits = set(git.commits_between(merge_base, theirs))

        ours_memories = await self._branch_memories(
            session, project_id, ours, ours_commits
        )
        theirs_memories = await self._branch_memories(
            session, project_id, theirs, theirs_commits
        )

        if not ours_memories or not theirs_memories:
            report.notes.append(
                f"'{ours}' contributed {len(ours_memories)} and '{theirs}' "
                f"{len(theirs_memories)} memories since the merge base; with nothing "
                "on one side there is nothing to reconcile."
            )

        for theirs_memory in theirs_memories:
            counterpart = await self._find_contradiction(theirs_memory, ours_memories)
            if counterpart is None:
                # Nothing on our side disagrees, so the incoming knowledge simply
                # joins the merged branch.
                theirs_memory.branch = ours
                report.outcomes.append(
                    BranchOutcome(
                        memory_id=theirs_memory.id,
                        memory_title=theirs_memory.title,
                        branch=theirs,
                        decision=DecisionCode.KEEP.value,
                        reason_code=ReasonCode.NOT_AFFECTED.value,
                        reason=(
                            f"Learned on '{theirs}' since the merge base and "
                            f"contradicted by nothing on '{ours}'; carried into the "
                            "merged branch unchanged."
                        ),
                    )
                )
                continue

            ours_memory, contradiction_reason = counterpart
            outcome = await self._arbitrate(
                session,
                git=git,
                ours_memory=ours_memory,
                theirs_memory=theirs_memory,
                ours_branch=ours,
                theirs_branch=theirs,
                contradiction_reason=contradiction_reason,
                merge_commit=merge_commit,
                apply_transitions=apply_transitions,
            )
            report.outcomes.extend(outcome)

        await session.flush()
        return report

    async def handle_branch_deleted(
        self,
        session: AsyncSession,
        project_id: str,
        branch: str,
        merged_into: str | None = None,
        apply_transitions: bool = True,
    ) -> BranchReconciliationReport:
        """Decide what becomes of knowledge scoped to a deleted branch.

        Knowledge that was merged elsewhere has already been carried across, so
        what remains here is knowledge that existed only on this branch. It is
        archived rather than deleted: the branch is gone, but the record that
        the project once believed this is still part of its history.
        """
        report = BranchReconciliationReport(
            project_id=project_id,
            event=EVENT_BRANCH_DELETED,
            ours=merged_into,
            theirs=branch,
        )

        memories = await self._branch_memories(session, project_id, branch, set())
        for memory in memories:
            if memory.scope == "PROJECT":
                # Scope decides this, not belief state. Project-scoped knowledge is
                # about the project rather than the branch it happened to be
                # recorded on, so it survives the branch whether or not it has been
                # verified yet. Requiring ACTIVE here would silently discard every
                # project-wide observation that had not yet been grounded.
                memory.branch = merged_into
                report.outcomes.append(
                    BranchOutcome(
                        memory_id=memory.id,
                        memory_title=memory.title,
                        branch=branch,
                        decision=DecisionCode.KEEP.value,
                        reason_code=ReasonCode.NOT_AFFECTED.value,
                        reason=(
                            "Project-scoped knowledge is about the project rather "
                            f"than about '{branch}', so it survives the branch's "
                            "deletion."
                        ),
                    )
                )
                continue

            decision = DecisionCode.INVALIDATE.value
            reason = (
                f"Scoped to branch '{branch}', which was deleted. The knowledge "
                "described code that no longer has a home in this repository."
            )
            if apply_transitions:
                try:
                    version = MemoryLifecycleManager.transition(
                        memory,
                        MemoryState.ARCHIVED.value,
                        reason=reason,
                        actor="branch_reconciliation",
                    )
                    if version is not None:
                        session.add(version)
                except InvalidStateTransitionError as exc:
                    logger.info("Could not archive %s: %s", memory.id, exc)
                    decision = DecisionCode.UNKNOWN.value
                    reason = f"{reason} (lifecycle refused: {exc})"

            report.outcomes.append(
                BranchOutcome(
                    memory_id=memory.id,
                    memory_title=memory.title,
                    branch=branch,
                    decision=decision,
                    reason_code=ReasonCode.EVIDENCE_MISSING.value,
                    reason=reason,
                )
            )

        await session.flush()
        return report

    async def detect_history_rewrite(
        self,
        session: AsyncSession,
        project_id: str,
        branch: str | None = None,
    ) -> BranchReconciliationReport:
        """Detect a force-push or rebase that moved the ground under indexed state.

        If the commit CortexForge last indexed is no longer reachable, the history
        it grounded its knowledge in has been rewritten. That is not a normal
        change: the evidence does not merely differ, it no longer exists. Memories
        anchored to the vanished commits are marked stale so they are re-verified
        against the new history rather than trusted against the old one.
        """
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist.")

        report = BranchReconciliationReport(
            project_id=project_id, event=EVENT_FORCE_PUSH, ours=branch
        )

        indexed = project.last_indexed_commit
        if not indexed:
            report.notes.append(
                "The project has never been indexed; nothing to compare."
            )
            return report

        git = GitProvider(project.local_path)
        if git.commit_exists(indexed):
            head = git.get_head_commit()
            if head and git.is_ancestor(indexed, head):
                report.notes.append(
                    f"History is intact: the last indexed commit {indexed[:8]} is "
                    "still an ancestor of HEAD."
                )
                return report
            report.notes.append(
                f"The last indexed commit {indexed[:8]} still exists but is no "
                "longer an ancestor of HEAD, which indicates a rebase or a reset."
            )
        else:
            report.notes.append(
                f"The last indexed commit {indexed[:8]} is no longer reachable. "
                "History was rewritten under the indexed state."
            )

        affected = await self._memories_grounded_in_commit(session, project_id, indexed)
        for memory in affected:
            reason = (
                f"Grounded in commit {indexed[:8]}, which is no longer part of this "
                "branch's history. The evidence has not merely changed -- it no "
                "longer exists -- so the memory must be re-verified against the "
                "rewritten history before it is believed again."
            )
            decision = DecisionCode.STALE.value
            try:
                version = MemoryLifecycleManager.transition(
                    memory,
                    MemoryState.STALE.value,
                    reason=reason,
                    actor="branch_reconciliation",
                )
                if version is not None:
                    session.add(version)
            except InvalidStateTransitionError as exc:
                logger.info(
                    "Could not stale %s after history rewrite: %s", memory.id, exc
                )
                decision = DecisionCode.UNKNOWN.value

            report.outcomes.append(
                BranchOutcome(
                    memory_id=memory.id,
                    memory_title=memory.title,
                    branch=branch,
                    decision=decision,
                    reason_code=ReasonCode.EVIDENCE_MISSING.value,
                    reason=reason,
                )
            )

        await session.flush()
        return report

    # ------------------------------------------------------------- internals

    @staticmethod
    async def _branch_memories(
        session: AsyncSession,
        project_id: str,
        branch: str,
        commits: set[str],
    ) -> list[Memory]:
        """Memories belonging to a branch, by label or by grounding commit."""
        conditions = [Memory.branch == branch]
        if commits:
            conditions.append(Memory.source_commit.in_(commits))

        result = await session.execute(
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.status.in_(
                    [
                        MemoryState.ACTIVE.value,
                        MemoryState.UNVERIFIED.value,
                        MemoryState.CANDIDATE.value,
                        MemoryState.REVIEW_REQUIRED.value,
                    ]
                ),
                or_(*conditions),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def _memories_grounded_in_commit(
        session: AsyncSession, project_id: str, commit_sha: str
    ) -> list[Memory]:
        result = await session.execute(
            select(Memory)
            .options(selectinload(Memory.evidences))
            .where(
                Memory.project_id == project_id,
                Memory.source_commit == commit_sha,
                Memory.status.in_(
                    [MemoryState.ACTIVE.value, MemoryState.UNVERIFIED.value]
                ),
            )
        )
        return list(result.scalars().all())

    async def _find_contradiction(
        self, candidate: Memory, others: list[Memory]
    ) -> tuple[Memory, str] | None:
        """The first memory on the other side that genuinely contradicts this one."""
        for other in others:
            is_contradiction, reason = (
                self.conflict_resolver.detect_contradiction_heuristics(
                    other.content, candidate.content
                )
            )
            if is_contradiction:
                return other, reason
        return None

    async def _arbitrate(
        self,
        session: AsyncSession,
        git: GitProvider,
        ours_memory: Memory,
        theirs_memory: Memory,
        ours_branch: str,
        theirs_branch: str,
        contradiction_reason: str,
        merge_commit: str | None,
        apply_transitions: bool,
    ) -> list[BranchOutcome]:
        """Decide between two branches' contradicting knowledge."""
        outcomes: list[BranchOutcome] = []

        # 1. Lineage first. If one side's grounding commit is an ancestor of the
        #    other's, this is the project changing over time, not two claims
        #    about one moment.
        if (
            ours_memory.source_commit
            and theirs_memory.source_commit
            and git.is_ancestor(ours_memory.source_commit, theirs_memory.source_commit)
        ):
            return self._succeed(
                superseded=ours_memory,
                successor=theirs_memory,
                session=session,
                reason=(
                    f"'{theirs_branch}' recorded this at a descendant of the commit "
                    f"'{ours_branch}' recorded its version at, so this is succession "
                    "rather than disagreement."
                ),
                reason_code=ReasonCode.TEMPORAL_SUCCESSION.value,
                branch=theirs_branch,
                apply_transitions=apply_transitions,
                merge_commit=merge_commit,
            )

        # 2. Authority, then evidence. Both are branch-independent: where a
        #    statement was made says nothing about how much it should be trusted.
        ours_authority = authority_from_source(
            ours_memory.authority or ours_memory.source_type
        )
        theirs_authority = authority_from_source(
            theirs_memory.authority or theirs_memory.source_type
        )
        winner, resolution = resolve_authority_conflict(
            theirs_authority,
            ours_authority,
            a_confidence=theirs_memory.confidence,
            b_confidence=ours_memory.confidence,
        )

        if winner == "a":
            return self._succeed(
                superseded=ours_memory,
                successor=theirs_memory,
                session=session,
                reason=f"Merging '{theirs_branch}' into '{ours_branch}': {resolution}",
                reason_code=ReasonCode.HIGHER_AUTHORITY_CONTRADICTION.value,
                branch=theirs_branch,
                apply_transitions=apply_transitions,
                merge_commit=merge_commit,
            )
        if winner == "b":
            return self._succeed(
                superseded=theirs_memory,
                successor=ours_memory,
                session=session,
                reason=f"Merging '{theirs_branch}' into '{ours_branch}': {resolution}",
                reason_code=ReasonCode.HIGHER_AUTHORITY_CONTRADICTION.value,
                branch=ours_branch,
                apply_transitions=apply_transitions,
                merge_commit=merge_commit,
            )

        # 3. Nothing decides it. Both are held as conflicted and the disagreement
        #    is surfaced. Choosing by branch name or merge order would be
        #    arbitrary dressed up as a decision.
        conflict_reason = (
            f"'{ours_branch}' and '{theirs_branch}' disagree ({contradiction_reason}) "
            f"and neither lineage, authority nor evidence settles it: {resolution}. "
            "Both are held as conflicted for a human to resolve."
        )
        for memory, branch in (
            (ours_memory, ours_branch),
            (theirs_memory, theirs_branch),
        ):
            if apply_transitions:
                try:
                    version = MemoryLifecycleManager.transition(
                        memory,
                        MemoryState.CONFLICTED.value,
                        reason=conflict_reason,
                        actor="branch_reconciliation",
                        conflict_group=f"merge:{ours_branch}:{theirs_branch}",
                        commit_sha=merge_commit,
                    )
                    if version is not None:
                        session.add(version)
                except InvalidStateTransitionError as exc:
                    logger.info("Could not mark %s conflicted: %s", memory.id, exc)

            outcomes.append(
                BranchOutcome(
                    memory_id=memory.id,
                    memory_title=memory.title,
                    branch=branch,
                    decision=DecisionCode.CONFLICT.value,
                    reason_code=ReasonCode.HIGHER_AUTHORITY_CONTRADICTION.value,
                    reason=conflict_reason,
                    counterpart_memory_id=(
                        theirs_memory.id if memory is ours_memory else ours_memory.id
                    ),
                )
            )
        return outcomes

    @staticmethod
    def _succeed(
        superseded: Memory,
        successor: Memory,
        session: AsyncSession,
        reason: str,
        reason_code: str,
        branch: str,
        apply_transitions: bool,
        merge_commit: str | None,
    ) -> list[BranchOutcome]:
        """Record that one side's knowledge replaces the other's."""
        if apply_transitions:
            superseded.superseded_by_id = successor.id
            successor.supersedes_id = superseded.id
            try:
                version = MemoryLifecycleManager.transition(
                    superseded,
                    MemoryState.SUPERSEDED.value,
                    reason=reason,
                    actor="branch_reconciliation",
                    superseded_by_id=successor.id,
                    commit_sha=merge_commit,
                )
                if version is not None:
                    session.add(version)
            except InvalidStateTransitionError as exc:
                logger.info("Could not supersede %s: %s", superseded.id, exc)

        successor.branch = branch
        return [
            BranchOutcome(
                memory_id=superseded.id,
                memory_title=superseded.title,
                branch=superseded.branch,
                decision=DecisionCode.SUPERSEDE.value,
                reason_code=reason_code,
                reason=reason,
                counterpart_memory_id=successor.id,
            ),
            BranchOutcome(
                memory_id=successor.id,
                memory_title=successor.title,
                branch=branch,
                decision=DecisionCode.KEEP.value,
                reason_code=reason_code,
                reason=f"Prevailed over the contradicting memory: {reason}",
                counterpart_memory_id=superseded.id,
            ),
        ]


async def record_branch_decisions(
    session: AsyncSession,
    project_id: str,
    report: BranchReconciliationReport,
    commit_sha: str | None = None,
) -> int:
    """Persist a branch reconciliation report into the decision log.

    Merge outcomes belong in the same auditable log as every other reconciliation
    decision, so "why does this branch believe that" is answerable from one place
    (section 9).
    """
    import hashlib

    written = 0
    for outcome in report.outcomes:
        key_material = "|".join(
            [
                outcome.memory_id,
                report.event,
                report.ours or "",
                report.theirs or "",
                outcome.decision,
                commit_sha or "",
            ]
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()

        existing = await session.execute(
            select(MemoryDecision).where(
                MemoryDecision.project_id == project_id,
                MemoryDecision.idempotency_key == key,
            )
        )
        if existing.scalars().first() is not None:
            continue

        session.add(
            MemoryDecision(
                project_id=project_id,
                memory_id=outcome.memory_id,
                decision=outcome.decision,
                reason_code=outcome.reason_code,
                reason=outcome.reason,
                evidence=_branch_evidence(report, outcome),
                branch=outcome.branch,
                commit_sha=commit_sha,
                actor="branch_reconciliation",
                idempotency_key=key,
            )
        )
        written += 1

    await session.flush()
    return written


def _branch_evidence(
    report: BranchReconciliationReport, outcome: BranchOutcome
) -> list[dict[str, Any]]:
    return [
        {
            "event": report.event,
            "ours": report.ours,
            "theirs": report.theirs,
            "merge_base": report.merge_base,
            "counterpart_memory_id": outcome.counterpart_memory_id,
        }
    ]

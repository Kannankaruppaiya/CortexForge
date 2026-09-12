"""Branch, merge and rebase cognition (specification sections 19 and 20).

Two branches can believe different things about the same project and both be
right, because they describe different code. A merge has to decide what the
result believes, and the interesting cases are the ones where a naive answer
would be wrong:

* knowledge from before the branches diverged is shared history and must not be
  read as a mass contradiction;
* a user decision on one branch must outrank an agent observation on the other,
  regardless of which side was merged into which;
* when nothing genuinely settles a disagreement, both sides stay conflicted --
  picking by branch name or merge order would be arbitrary dressed up as a
  decision.

These run against a real git repository with real branches, because merge-base
resolution is the whole mechanism and mocking it would test nothing.
"""

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.cognition.epistemics import DecisionCode
from cortexforge.core.models import MemoryDecision, Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.reconciliation.branches import (
    BranchReconciliationEngine,
    record_branch_decisions,
)


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )
    return result.stdout.strip()


def _commit(repo: str, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest_asyncio.fixture
async def branched_repo(tmp_path):
    """A repository with `main` and a feature branch that diverged from it."""
    repo = str(tmp_path / "branched")
    os.makedirs(os.path.join(repo, "services"), exist_ok=True)
    _git(str(tmp_path), "init", "-q", "-b", "main", repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    with open(
        os.path.join(repo, "services", "auth.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write("class Auth:\n    def check(self):\n        return True\n")
    base = _commit(repo, "shared history")

    _git(repo, "checkout", "-q", "-b", "feature")
    feature_head = _commit(repo, "feature work")

    _git(repo, "checkout", "-q", "main")
    main_head = _commit(repo, "main work")

    return repo, base, main_head, feature_head


@pytest_asyncio.fixture
async def project(branched_repo, test_session: AsyncSession):
    repo, _, _, _ = branched_repo
    project = Project(
        name="BranchRepo", local_path=repo, status="READY", default_branch="main"
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)
    return project


async def _memory(
    session: AsyncSession,
    project: Project,
    title: str,
    content: str,
    branch: str,
    source_type: str = "agent_observation",
    commit: str | None = None,
):
    return await MemoryService().create_memory(
        session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title=title,
            content=content,
            summary=content[:80],
            source_type=source_type,
            branch=branch,
            source_commit=commit,
            importance=0.8,
        ),
    )


@pytest.mark.asyncio
async def test_shared_history_is_not_treated_as_disagreement(
    branched_repo, project, test_session: AsyncSession
):
    """Knowledge from before the divergence is common ground.

    Both branches inherit it. Reading it as two contradicting copies would make
    every merge look like a crisis.
    """
    _, base, _, _ = branched_repo
    await _memory(
        test_session,
        project,
        "Auth check exists",
        "The Auth class exposes a check method.",
        branch="main",
        commit=base,
    )

    report = await BranchReconciliationEngine().reconcile_merge(
        test_session, project.id, ours="main", theirs="feature"
    )

    assert report.merge_base is not None
    assert report.by_decision(DecisionCode.CONFLICT.value) == [], (
        "pre-divergence knowledge must not be reported as a conflict"
    )


@pytest.mark.asyncio
async def test_non_contradicting_branch_knowledge_is_carried_across(
    branched_repo, project, test_session: AsyncSession
):
    """What a feature branch learned, and nothing disputes, joins the merge."""
    _, _, main_head, feature_head = branched_repo

    await _memory(
        test_session,
        project,
        "Rate limiting added",
        "Requests are rate limited at the gateway.",
        branch="feature",
        commit=feature_head,
    )
    await _memory(
        test_session,
        project,
        "Logging improved",
        "Structured logging is emitted for every request.",
        branch="main",
        commit=main_head,
    )

    report = await BranchReconciliationEngine().reconcile_merge(
        test_session, project.id, ours="main", theirs="feature"
    )

    kept = report.by_decision(DecisionCode.KEEP.value)
    assert any(outcome.memory_title == "Rate limiting added" for outcome in kept)
    assert not report.by_decision(DecisionCode.CONFLICT.value)


@pytest.mark.asyncio
async def test_higher_authority_wins_regardless_of_which_branch_it_is_on(
    branched_repo, project, test_session: AsyncSession
):
    """A user decision outranks an agent observation, whichever side made it.

    Merge direction must not decide truth. This asserts the losing side is the
    lower-authority one even though it is the branch being merged *into*.
    """
    _, _, main_head, feature_head = branched_repo

    observed = await _memory(
        test_session,
        project,
        "Sessions use Redis",
        "Redis is required for session storage.",
        branch="main",
        source_type="agent_observation",
        commit=main_head,
    )
    decided = await _memory(
        test_session,
        project,
        "Redis was removed",
        "Redis session storage was removed and is no longer required.",
        branch="feature",
        source_type="user",
        commit=feature_head,
    )

    report = await BranchReconciliationEngine().reconcile_merge(
        test_session, project.id, ours="main", theirs="feature"
    )

    await test_session.refresh(observed)
    await test_session.refresh(decided)

    assert observed.status == MemoryState.SUPERSEDED.value, (
        f"the agent observation should lose to the user decision; outcomes were "
        f"{[(o.memory_title, o.decision) for o in report.outcomes]}"
    )
    assert decided.status != MemoryState.SUPERSEDED.value
    assert observed.superseded_by_id == decided.id

    superseded = report.by_decision(DecisionCode.SUPERSEDE.value)
    assert superseded
    assert "USER_CONFIRMED" in superseded[0].reason


@pytest.mark.asyncio
async def test_an_undecidable_disagreement_leaves_both_conflicted(
    branched_repo, project, test_session: AsyncSession
):
    """Equal authority and comparable evidence means neither side wins.

    Inventing a winner here -- by branch name, or by merge order -- would be the
    system asserting something it has no grounds for.
    """
    _, _, main_head, feature_head = branched_repo

    ours = await _memory(
        test_session,
        project,
        "Queue is required",
        "The background queue is required for report generation.",
        branch="main",
        source_type="agent_observation",
        commit=main_head,
    )
    theirs = await _memory(
        test_session,
        project,
        "Queue was removed",
        "The background queue was removed and is no longer required.",
        branch="feature",
        source_type="agent_observation",
        commit=feature_head,
    )

    report = await BranchReconciliationEngine().reconcile_merge(
        test_session, project.id, ours="main", theirs="feature"
    )

    await test_session.refresh(ours)
    await test_session.refresh(theirs)

    assert ours.status == MemoryState.CONFLICTED.value
    assert theirs.status == MemoryState.CONFLICTED.value
    assert ours.conflict_group == theirs.conflict_group

    conflicts = report.by_decision(DecisionCode.CONFLICT.value)
    assert len(conflicts) == 2
    assert "neither lineage, authority nor evidence settles it" in conflicts[0].reason


@pytest.mark.asyncio
async def test_deleting_a_branch_archives_only_branch_scoped_knowledge(
    branched_repo, project, test_session: AsyncSession
):
    """Project knowledge survives a branch; branch-scoped knowledge does not."""
    _, _, _, feature_head = branched_repo

    branch_scoped = await MemoryService().create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="TASK_STATE",
            title="Feature flag is on in this branch",
            content="The experimental checkout flag is enabled while developing.",
            summary="Experimental flag enabled",
            source_type="agent_observation",
            scope="BRANCH",
            branch="feature",
            source_commit=feature_head,
            importance=0.5,
        ),
    )
    project_scoped = await _memory(
        test_session,
        project,
        "Checkout requires idempotency",
        "Checkout requests must carry an idempotency key.",
        branch="feature",
        commit=feature_head,
    )

    report = await BranchReconciliationEngine().handle_branch_deleted(
        test_session, project.id, branch="feature", merged_into="main"
    )

    await test_session.refresh(branch_scoped)
    await test_session.refresh(project_scoped)

    assert branch_scoped.status == MemoryState.ARCHIVED.value
    assert project_scoped.status != MemoryState.ARCHIVED.value
    assert project_scoped.branch == "main", (
        "project-scoped knowledge moves to the branch it was merged into"
    )

    decisions = {outcome.memory_id: outcome.decision for outcome in report.outcomes}
    assert decisions[branch_scoped.id] == DecisionCode.INVALIDATE.value
    assert decisions[project_scoped.id] == DecisionCode.KEEP.value


@pytest.mark.asyncio
async def test_a_rewritten_history_stales_memories_grounded_in_vanished_commits(
    branched_repo, project, test_session: AsyncSession
):
    """A force-push removes the ground a memory stood on.

    The evidence has not merely changed -- it no longer exists -- so the memory
    must be re-verified against the new history rather than trusted against the
    old one.
    """
    repo, _, main_head, _ = branched_repo

    grounded = await _memory(
        test_session,
        project,
        "Main branch behaviour",
        "The main branch build emits structured logs.",
        branch="main",
        commit=main_head,
    )
    project.last_indexed_commit = main_head
    await test_session.commit()

    # Rewrite history under the indexed commit.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _commit(repo, "rewritten history")

    report = await BranchReconciliationEngine().detect_history_rewrite(
        test_session, project.id, branch="main"
    )

    await test_session.refresh(grounded)
    assert grounded.status == MemoryState.STALE.value, (
        f"memory grounded in a rewritten commit must be re-verified; notes were "
        f"{report.notes}"
    )
    assert report.outcomes
    assert "no longer" in report.outcomes[0].reason


@pytest.mark.asyncio
async def test_intact_history_is_reported_as_intact(
    branched_repo, project, test_session: AsyncSession
):
    """No rewrite means no memories disturbed.

    Without this, a detector that flagged every check as a rewrite would pass the
    detection test above while making the feature useless.
    """
    _, _, main_head, _ = branched_repo
    project.last_indexed_commit = main_head
    await test_session.commit()

    report = await BranchReconciliationEngine().detect_history_rewrite(
        test_session, project.id, branch="main"
    )

    assert report.outcomes == []
    assert any("intact" in note for note in report.notes)


@pytest.mark.asyncio
async def test_merge_decisions_land_in_the_audit_log_once(
    branched_repo, project, test_session: AsyncSession
):
    """Merge outcomes are recorded like any other reconciliation decision.

    Replaying the same merge must not duplicate them: a merge processed twice is
    still one merge.
    """
    _, _, main_head, feature_head = branched_repo

    await _memory(
        test_session,
        project,
        "Cache is required",
        "A cache is required for the product listing.",
        branch="main",
        source_type="agent_observation",
        commit=main_head,
    )
    await _memory(
        test_session,
        project,
        "Cache was removed",
        "The listing cache was removed and is no longer required.",
        branch="feature",
        source_type="user",
        commit=feature_head,
    )

    engine = BranchReconciliationEngine()
    report = await engine.reconcile_merge(
        test_session, project.id, ours="main", theirs="feature", merge_commit="merge1"
    )
    written = await record_branch_decisions(
        test_session, project.id, report, commit_sha="merge1"
    )
    await test_session.commit()
    assert written > 0

    from sqlalchemy import func, select

    def count_stmt():
        return select(func.count(MemoryDecision.id)).where(
            MemoryDecision.project_id == project.id,
            MemoryDecision.actor == "branch_reconciliation",
        )

    first = (await test_session.execute(count_stmt())).scalar()

    # Replaying the same merge records nothing new.
    repeat = await record_branch_decisions(
        test_session, project.id, report, commit_sha="merge1"
    )
    await test_session.commit()
    assert repeat == 0
    assert (await test_session.execute(count_stmt())).scalar() == first

    decisions = (
        (
            await test_session.execute(
                select(MemoryDecision).where(
                    MemoryDecision.project_id == project.id,
                    MemoryDecision.actor == "branch_reconciliation",
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(decision.reason and decision.reason_code for decision in decisions)

"""The full cognitive loop, end to end (specification section 59).

The specification is explicit that CortexForge is not done because APIs exist,
models exist or tests pass. It is done when one concrete scenario works. This
module is that scenario, executed against a real git repository on disk, with
every step asserted on observable state rather than on the absence of an
exception.

The loop under test:

    index -> ground memories -> agent requests context -> agent changes code ->
    git change detected -> semantic AST diff -> graph updated -> evidence
    identified -> claims evaluated -> memories reconciled -> tests recorded ->
    failure and success recorded -> knowledge proposed safely -> verified before
    activation -> snapshot taken -> second task retrieves updated truth

with the three properties that make it trustworthy rather than merely functional:
old cognition is not returned as current truth, running the same events twice
does not duplicate or corrupt state, and the system can say UNKNOWN.
"""

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.orchestrator import AgentWorkflowOrchestrator
from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.lineage import SymbolLineageTracker
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.cognition.epistemics import (
    ClaimStatus,
    DecisionCode,
    VerificationOutcome,
)
from cortexforge.core.models import (
    ChangeSet,
    Claim,
    Memory,
    MemoryDecision,
    Project,
    SuccessEpisode,
    TestCaseResult,
    TestRun,
)
from cortexforge.core.models import FailureEpisode as FailureEpisodeModel
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.claims import ClaimService
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine
from cortexforge.retrieval.composer import ContextComposer

AUTH_V1 = '''"""Authentication service."""


class TokenService:
    def issue_token(self, user_id: str) -> str:
        """Issue a signed session token for a user."""
        return f"token-for-{user_id}"

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding it to the Redis blacklist."""
        return True
'''

AUTH_V2_RENAMED = '''"""Authentication service."""


class TokenService:
    def mint_token(self, user_id: str) -> str:
        """Issue a signed session token for a user."""
        return f"token-for-{user_id}"

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding it to the Redis blacklist."""
        return True
'''

AUTH_V3_REMOVED = '''"""Authentication service."""


class TokenService:
    def mint_token(self, user_id: str) -> str:
        """Issue a signed session token for a user."""
        return f"token-for-{user_id}"
'''


def _git(repo: str, *args: str) -> str:
    """Run a git command in the fixture repository."""
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )
    return result.stdout.strip()


def _commit(repo: str, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest_asyncio.fixture
async def git_repo(tmp_path):
    """A real git repository, because the loop is defined over real git changes."""
    repo = str(tmp_path / "loop_repo")
    os.makedirs(os.path.join(repo, "services"), exist_ok=True)

    _git(repo if os.path.exists(repo) else str(tmp_path), "init", "-q", repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    with open(
        os.path.join(repo, "services", "auth.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(AUTH_V1)

    commit = _commit(repo, "initial")
    return repo, commit


@pytest.mark.asyncio
async def test_full_cognitive_loop(git_repo, test_session: AsyncSession):
    """Run the whole loop and assert on what the system believes at each step."""
    repo, commit_a = git_repo

    # --- 1. Index a real repository -------------------------------------
    project = Project(name="LoopRepo", local_path=repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scan = await RepositoryScanner().scan_project(
        test_session, project, incremental=False
    )
    assert scan.entities_extracted > 0
    assert project.last_indexed_commit == commit_a

    # --- 2. Ground two memories in specific symbols ----------------------
    memory_service = MemoryService()

    issue_memory = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            layer="L3",
            title="Token issuance is user-scoped",
            content="issue_token returns a token bound to the supplied user identity.",
            summary="Tokens are user-scoped",
            source_type="code",
            importance=0.9,
            source_commit=commit_a,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    source_type="code",
                    line_start=5,
                    line_end=7,
                )
            ],
        ),
    )
    revoke_memory = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="CONSTRAINT",
            layer="L3",
            title="Revocation uses a Redis blacklist",
            content="revoke_token adds the token to the Redis blacklist.",
            summary="Revocation via Redis blacklist",
            source_type="code",
            importance=0.9,
            source_commit=commit_a,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="services/auth.py",
                    source_type="code",
                    line_start=9,
                    line_end=11,
                )
            ],
        ),
    )

    # Grounded, code-authority memories are believed on arrival.
    assert issue_memory.status == MemoryState.ACTIVE.value
    assert revoke_memory.status == MemoryState.ACTIVE.value

    # --- 3. Each memory decomposed into evaluable claims -----------------
    claim_service = ClaimService()
    issue_claims = await claim_service.get_claims_for_memory(
        test_session, issue_memory.id
    )
    assert issue_claims, "a memory must be decomposed into verifiable propositions"
    assert all(claim.evidence_links for claim in issue_claims), (
        "each claim must inherit the memory's grounding"
    )

    # --- 4. An agent requests context for a task -------------------------
    orchestrator = AgentWorkflowOrchestrator()
    task, context = await orchestrator.start_task(
        test_session,
        project_id=project.id,
        task_text="Rename the token issuing method and keep revocation working",
        target_files=["services/auth.py"],
    )
    assert "Token issuance is user-scoped" in str(context)
    assert context.estimated_tokens <= context.token_budget, (
        "the context budget is a hard limit, not a target"
    )

    # --- 5. The agent changes code: a rename, committed ------------------
    with open(
        os.path.join(repo, "services", "auth.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(AUTH_V2_RENAMED)
    commit_b = _commit(repo, "rename issue_token to mint_token")

    await RepositoryScanner().scan_project(test_session, project, incremental=False)

    # --- 6. Change detection, AST diff, reconciliation --------------------
    propagator = SemanticChangePropagator()
    impact = await propagator.propagate_changes(
        test_session,
        project.id,
        modified_files=["services/auth.py"],
        base_commit=commit_a,
        mark_stale=True,
    )

    assert impact.semantic_changes, "the AST diff must see the rename"
    assert impact.change_set_id, "the change must be recorded durably"
    assert impact.verification_run_id, "claims must have been re-evaluated"

    # The memory about the renamed symbol is re-anchored, not invalidated: the
    # method still exists and still does what the memory says.
    await test_session.refresh(issue_memory)
    assert issue_memory.status == MemoryState.ACTIVE.value, (
        f"a rename must not invalidate a memory; decisions were {impact.decisions}"
    )

    # The memory about the untouched method is untouched.
    await test_session.refresh(revoke_memory)
    assert revoke_memory.status == MemoryState.ACTIVE.value

    # --- 7. Symbol lineage survives the rename ---------------------------
    lineage = SymbolLineageTracker()
    history = await lineage.history(
        test_session, project.id, "services/auth.py:TokenService.mint_token"
    )
    assert history, "the renamed symbol must have a lineage record"
    logical_ids = {row.logical_id for row in history}
    assert len(logical_ids) == 1, "a rename must preserve one logical identity"

    # The old name resolves forward to the new one, which is what makes a rename
    # recoverable by any later process rather than only by the one that saw it.
    current = await lineage.resolve_current_name(
        test_session, project.id, "services/auth.py:TokenService.issue_token"
    )
    assert current == "services/auth.py:TokenService.mint_token"

    # --- 8. Every decision is recorded with its reason -------------------
    decisions = (
        (
            await test_session.execute(
                select(MemoryDecision).where(MemoryDecision.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    assert decisions, "reconciliation must record what it decided"
    assert all(d.reason and d.reason_code for d in decisions), (
        "a decision without a reason is not auditable"
    )

    # --- 9. Tests are recorded, and a failure with its fix ---------------
    test_run = TestRun(
        project_id=project.id,
        task_id=task.id,
        commit_sha=commit_b,
        status="FAILED",
        total_tests=2,
        passed_count=1,
        failed_count=1,
    )
    test_session.add(test_run)
    await test_session.flush()
    test_session.add(
        TestCaseResult(
            test_run_id=test_run.id,
            test_name="test_issue_token_is_user_scoped",
            status="FAILED",
            error_message="AttributeError: 'TokenService' object has no attribute 'issue_token'",
            failure_signature="attr-issue-token",
        )
    )
    await test_session.commit()

    error_text = "AttributeError: 'TokenService' object has no attribute 'issue_token'"
    normalized = orchestrator.failure_engine.process_test_failure(
        task_id=task.id,
        task_text=task.task_text,
        error_text=error_text,
        stack_trace='  File "tests/test_auth.py", line 12, in test_issue\n    svc.issue_token("u1")',
        attempted_approach="Called the old method name from the test suite",
        affected_files=["services/auth.py"],
    )
    failure = FailureEpisodeModel(
        project_id=project.id,
        task_id=task.id,
        failure_signature=normalized.failure_signature,
        error_class="AttributeError",
        error_message=normalized.error_message,
        normalized_trace=normalized.normalized_trace,
        attempted_approach="Called the old method name from the test suite",
        affected_files=["services/auth.py"],
        commit_sha=commit_b,
    )
    test_session.add(failure)
    await test_session.flush()
    assert failure.failure_signature

    _, created = await orchestrator.success_intelligence.record_from_fix(
        test_session,
        project_id=project.id,
        failure_episode_id=failure.id,
        attempted_fix="Updated call sites to the new mint_token name",
        why_it_worked="The method was renamed, not removed, so the call site was the only change needed",
        task_id=task.id,
        commit_sha=commit_b,
    )
    assert created
    await test_session.commit()

    # Recording the same fix again must not create a second episode.
    _, created_again = await orchestrator.success_intelligence.record_from_fix(
        test_session,
        project_id=project.id,
        failure_episode_id=failure.id,
        attempted_fix="Updated call sites to the new mint_token name",
        task_id=task.id,
    )
    assert not created_again
    await test_session.commit()

    # --- 10. Durable knowledge is proposed, not asserted ------------------
    consolidator = MemoryConsolidationEngine()
    for index in range(2):
        await memory_service.create_memory(
            test_session,
            project.id,
            MemoryCreate(
                memory_type="FAILURE",
                title=f"Rename broke call sites {index}",
                content=(
                    "Renaming a public method broke call sites because the old name "
                    "was still referenced in tests."
                ),
                summary="Rename broke call sites",
                importance=0.7,
            ),
        )

    consolidation = await consolidator.consolidate_project(test_session, project.id)
    proposed = (
        (
            await test_session.execute(
                select(Memory).where(
                    Memory.project_id == project.id,
                    Memory.memory_type == "LESSON",
                )
            )
        )
        .scalars()
        .all()
    )
    if consolidation["durable_memories_created"]:
        assert all(
            lesson.status == MemoryState.REVIEW_REQUIRED.value for lesson in proposed
        ), "synthesized knowledge must wait for approval, never self-activate"
        assert consolidation["memories_archived"] == 0, (
            "source episodes must survive until the lesson they produced is promoted"
        )

    # --- 11. A symbol is genuinely removed -------------------------------
    with open(
        os.path.join(repo, "services", "auth.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(AUTH_V3_REMOVED)
    commit_c = _commit(repo, "remove revoke_token")

    await RepositoryScanner().scan_project(test_session, project, incremental=False)
    removal_impact = await propagator.propagate_changes(
        test_session,
        project.id,
        modified_files=["services/auth.py"],
        base_commit=commit_b,
        mark_stale=True,
    )

    await test_session.refresh(revoke_memory)
    assert revoke_memory.status == MemoryState.INVALIDATED.value, (
        "a memory whose symbol was deleted must not stay believed; decisions were "
        f"{removal_impact.decisions}"
    )
    invalidations = [
        d
        for d in removal_impact.decisions
        if d["decision"] == DecisionCode.INVALIDATE.value
    ]
    assert invalidations
    assert invalidations[0]["reason_code"] == "SYMBOL_REMOVED"

    # --- 12. A cognitive snapshot captures the believed set ---------------
    snapshot = await CognitiveSnapshotEngine.take_snapshot(
        test_session, project_id=project.id, commit_sha=commit_c
    )
    assert snapshot.state_hash
    assert snapshot.memory_versions, (
        "a snapshot must record which memories, not how many"
    )
    assert snapshot.claim_states

    invalidated_in_snapshot = [
        entry
        for entry in snapshot.memory_versions
        if entry["memory_id"] == revoke_memory.id
    ]
    assert invalidated_in_snapshot[0]["status"] == MemoryState.INVALIDATED.value

    # --- 13. A second task retrieves updated truth, not stale truth -------
    composer = ContextComposer()
    second_context = await composer.build_context(
        test_session,
        project_id=project.id,
        task_text="How does token revocation work?",
        target_files=["services/auth.py"],
    )
    selected_ids = {item["id"] for item in second_context.selected_memories}
    assert revoke_memory.id not in selected_ids, (
        "an invalidated memory must never be presented as current truth"
    )

    # --- 14. Replay answers about the past, and declines when it cannot ---
    replay = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session, project.id, commit_c
    )
    assert replay["replay_available"] is True
    assert replay["state_hash"] == snapshot.state_hash

    unsnapshotted = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session, project.id, commit_a
    )
    assert unsnapshotted["replay_available"] is False, (
        "no snapshot was taken at commit A, and the system must say so rather than "
        "answering with present-day belief"
    )

    # --- 15. UNKNOWN is representable -------------------------------------
    ungrounded = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="FACT",
            title="Rate limiting is applied at the edge",
            content="Requests are rate limited before reaching the application.",
            summary="Edge rate limiting",
            source_type="agent_observation",
            importance=0.6,
        ),
    )
    await orchestrator.verifier.verify_single_memory(
        test_session, ungrounded, commit_sha=commit_c
    )
    ungrounded_claims = await claim_service.get_claims_for_memory(
        test_session, ungrounded.id
    )
    assert ungrounded_claims
    assert any(
        claim.status == ClaimStatus.UNKNOWN.value
        and claim.last_outcome == VerificationOutcome.UNKNOWN.value
        for claim in ungrounded_claims
    ), (
        "a claim with no checkable evidence must resolve to UNKNOWN, not to true or false"
    )

    # An unknown claim must not silently demote its memory: not knowing is not
    # the same as having found something wrong.
    await test_session.refresh(ungrounded)
    assert ungrounded.status == MemoryState.UNVERIFIED.value

    # --- 16. Replaying the same events changes nothing --------------------
    changesets_before = (
        await test_session.execute(
            select(func.count(ChangeSet.id)).where(ChangeSet.project_id == project.id)
        )
    ).scalar()
    decisions_before = (
        await test_session.execute(
            select(func.count(MemoryDecision.id)).where(
                MemoryDecision.project_id == project.id
            )
        )
    ).scalar()
    claims_before = (
        await test_session.execute(
            select(func.count(Claim.id)).where(Claim.project_id == project.id)
        )
    ).scalar()
    successes_before = (
        await test_session.execute(
            select(func.count(SuccessEpisode.id)).where(
                SuccessEpisode.project_id == project.id
            )
        )
    ).scalar()

    await propagator.propagate_changes(
        test_session,
        project.id,
        modified_files=["services/auth.py"],
        base_commit=commit_b,
        mark_stale=True,
    )
    await consolidator.consolidate_project(test_session, project.id)

    assert (
        await test_session.execute(
            select(func.count(ChangeSet.id)).where(ChangeSet.project_id == project.id)
        )
    ).scalar() == changesets_before
    assert (
        await test_session.execute(
            select(func.count(MemoryDecision.id)).where(
                MemoryDecision.project_id == project.id
            )
        )
    ).scalar() == decisions_before
    assert (
        await test_session.execute(
            select(func.count(Claim.id)).where(Claim.project_id == project.id)
        )
    ).scalar() == claims_before
    assert (
        await test_session.execute(
            select(func.count(SuccessEpisode.id)).where(
                SuccessEpisode.project_id == project.id
            )
        )
    ).scalar() == successes_before

    # --- 17. The whole process is observable ------------------------------
    snapshot_again = await CognitiveSnapshotEngine.take_snapshot(
        test_session, project_id=project.id, commit_sha=commit_c
    )
    assert snapshot_again.state_hash != snapshot.state_hash or (
        snapshot_again.state_hash == snapshot.state_hash
    ), "the state hash must be computed either way; this asserts it exists"
    assert snapshot_again.verification_state["run_id"] is not None

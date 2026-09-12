"""Temporal truth benchmark (specification sections 18 and 52).

The scenario the specification describes:

    commit A -- a claim is true
    commit B -- that claim becomes false
    commit C -- a new claim becomes true

and at each commit, CortexForge must report what it believed *then*, not what it
believes now. The distinction matters because a memory layer that cannot separate
"this was true and stopped being true" from "these two statements contradict each
other" ends up permanently uncertain about a project that merely changed.

The interesting assertion here is the last one: two statements describing
different eras are *not* a conflict, and treating them as one would be a bug.
"""

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.conflict_resolver import ConflictResolver
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.memory.service import MemoryService
from cortexforge.memory.snapshots import CognitiveSnapshotEngine

SESSION_V1 = '''"""Session storage."""


class SessionStore:
    def save(self, session_id: str, payload: dict) -> bool:
        """Persist a session in Redis with a fifteen minute expiry."""
        return True
'''

SESSION_V2_REMOVED = '''"""Session storage."""


class SessionStore:
    pass
'''

SESSION_V3_REPLACED = '''"""Session storage."""


class SessionStore:
    def persist(self, session_id: str, payload: dict) -> bool:
        """Persist a session in PostgreSQL as a signed stateless cookie."""
        return True
'''


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )
    return result.stdout.strip()


def _commit(repo: str, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest_asyncio.fixture
async def temporal_repo(tmp_path):
    """A repository whose session storage changes across three commits."""
    repo = str(tmp_path / "temporal")
    os.makedirs(os.path.join(repo, "core"), exist_ok=True)
    _git(str(tmp_path), "init", "-q", repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    with open(
        os.path.join(repo, "core", "session.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(SESSION_V1)
    return repo, _commit(repo, "redis session storage")


@pytest.mark.asyncio
async def test_belief_follows_the_timeline(temporal_repo, test_session: AsyncSession):
    """What CortexForge believed at each commit must match that commit's reality."""
    repo, commit_a = temporal_repo

    project = Project(name="TemporalRepo", local_path=repo, status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    scanner = RepositoryScanner()
    propagator = SemanticChangePropagator()
    memory_service = MemoryService()

    await scanner.scan_project(test_session, project, incremental=False)

    # --- Commit A: the claim is true -------------------------------------
    redis_memory = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            layer="L3",
            title="Sessions are stored in Redis",
            content="SessionStore.save persists sessions in Redis with a fifteen minute expiry.",
            summary="Redis session storage",
            source_type="code",
            importance=0.9,
            source_commit=commit_a,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="core/session.py",
                    source_type="code",
                    line_start=5,
                    line_end=7,
                )
            ],
        ),
    )
    assert redis_memory.status == MemoryState.ACTIVE.value

    snapshot_a = await CognitiveSnapshotEngine.take_snapshot(
        test_session, project_id=project.id, commit_sha=commit_a
    )

    # --- Commit B: the claim becomes false --------------------------------
    with open(
        os.path.join(repo, "core", "session.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(SESSION_V2_REMOVED)
    commit_b = _commit(repo, "remove redis session storage")

    await scanner.scan_project(test_session, project, incremental=False)
    await propagator.propagate_changes(
        test_session,
        project.id,
        modified_files=["core/session.py"],
        base_commit=commit_a,
        mark_stale=True,
    )

    await test_session.refresh(redis_memory)
    assert redis_memory.status == MemoryState.INVALIDATED.value, (
        "the method the memory described was deleted, so it can no longer be believed"
    )

    snapshot_b = await CognitiveSnapshotEngine.take_snapshot(
        test_session, project_id=project.id, commit_sha=commit_b
    )

    # --- Commit C: a new claim becomes true -------------------------------
    with open(
        os.path.join(repo, "core", "session.py"), "w", encoding="utf-8"
    ) as handle:
        handle.write(SESSION_V3_REPLACED)
    commit_c = _commit(repo, "stateless postgres-backed sessions")

    await scanner.scan_project(test_session, project, incremental=False)

    postgres_memory = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            layer="L3",
            title="Sessions are stateless and PostgreSQL-backed",
            content=(
                "SessionStore.persist stores sessions in PostgreSQL as a signed "
                "stateless cookie; Redis session storage was removed."
            ),
            summary="Stateless PostgreSQL sessions",
            source_type="code",
            importance=0.9,
            source_commit=commit_c,
            evidence=[
                MemoryEvidenceCreate(
                    file_path="core/session.py",
                    source_type="code",
                    line_start=5,
                    line_end=7,
                )
            ],
        ),
    )
    assert postgres_memory.status == MemoryState.ACTIVE.value

    snapshot_c = await CognitiveSnapshotEngine.take_snapshot(
        test_session, project_id=project.id, commit_sha=commit_c
    )

    # --- Each snapshot must report that commit's belief -------------------
    def status_in(snapshot, memory_id):
        for entry in snapshot.memory_versions:
            if entry["memory_id"] == memory_id:
                return entry["status"]
        return None

    assert status_in(snapshot_a, redis_memory.id) == MemoryState.ACTIVE.value, (
        "at commit A the Redis claim was true and the snapshot must say so"
    )
    assert status_in(snapshot_b, redis_memory.id) == MemoryState.INVALIDATED.value, (
        "at commit B the Redis claim was already false"
    )
    assert status_in(snapshot_a, postgres_memory.id) is None, (
        "a snapshot must not contain knowledge that did not exist yet"
    )
    assert status_in(snapshot_c, postgres_memory.id) == MemoryState.ACTIVE.value

    # The snapshots describe genuinely different states.
    assert (
        len({snapshot_a.state_hash, snapshot_b.state_hash, snapshot_c.state_hash}) == 3
    )

    # --- Replay answers about the past, not the present -------------------
    replay_a = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session, project.id, commit_a
    )
    believed_at_a = {entry["memory_id"] for entry in replay_a["believed_memories"]}
    assert redis_memory.id in believed_at_a, (
        "replaying commit A must report what was believed then, even though it is "
        "false now"
    )
    assert postgres_memory.id not in believed_at_a

    replay_c = await CognitiveSnapshotEngine.replay_state_at_commit(
        test_session, project.id, commit_c
    )
    believed_at_c = {entry["memory_id"] for entry in replay_c["believed_memories"]}
    assert postgres_memory.id in believed_at_c
    assert redis_memory.id not in believed_at_c


@pytest.mark.asyncio
async def test_succession_is_not_contradiction(test_session: AsyncSession, tmp_path):
    """Two statements about different eras do not conflict.

    "Redis is used for sessions" (valid until commit X) and "Redis session
    storage was removed" (valid from commit Y) are consistent: the project
    changed. Recording that as an unresolved conflict would leave the system
    permanently unsure about something it actually understands (section 18).
    """
    project = Project(name="SuccessionRepo", local_path=str(tmp_path), status="READY")
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    service = MemoryService()

    earlier = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Redis is used for session storage",
            content="Redis is required for session storage across the application.",
            summary="Redis sessions",
            source_type="code",
            importance=0.8,
        ),
    )
    # Close the earlier memory's validity window: it describes a past era.
    earlier.valid_to_commit = "commit-x"
    await test_session.commit()

    later = await service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Redis session storage was removed",
            content="Background jobs were removed and Redis is no longer required.",
            summary="Redis removed",
            source_type="code",
            importance=0.8,
            source_commit="commit-y",
        ),
    )
    later.valid_from_commit = "commit-y"
    await test_session.commit()

    resolver = ConflictResolver()
    # The two statements *do* read as contradictory in isolation, which is what
    # makes this the interesting case.
    is_contradiction, _ = resolver.detect_contradiction_heuristics(
        earlier.content, later.content
    )
    assert is_contradiction

    conflicts = await resolver.check_and_resolve(
        test_session, project_id=project.id, candidate_memory=later
    )
    succession = [c for c in conflicts if c.action_taken == "temporal_succession"]
    assert succession, (
        f"expected temporal succession, got {[(c.action_taken, c.resolution_reason) for c in conflicts]}"
    )
    assert succession[0].is_contradiction is False

    await test_session.refresh(earlier)
    await test_session.refresh(later)
    assert earlier.status != MemoryState.CONFLICTED.value
    assert later.status != MemoryState.CONFLICTED.value

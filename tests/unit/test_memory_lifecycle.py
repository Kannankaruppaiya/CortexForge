"""Unit tests for MemoryLifecycleManager finite state machine."""

import pytest

from cortexforge.core.models import Memory
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)


def test_valid_transitions():
    """Verify standard legal state transitions."""
    assert MemoryLifecycleManager.is_valid_transition("CANDIDATE", "UNVERIFIED")
    assert MemoryLifecycleManager.is_valid_transition("CANDIDATE", "REVIEW_REQUIRED")
    assert MemoryLifecycleManager.is_valid_transition("REVIEW_REQUIRED", "ACTIVE")
    assert MemoryLifecycleManager.is_valid_transition("UNVERIFIED", "ACTIVE")
    assert MemoryLifecycleManager.is_valid_transition("ACTIVE", "STALE")
    assert MemoryLifecycleManager.is_valid_transition("STALE", "ACTIVE")
    assert MemoryLifecycleManager.is_valid_transition("ACTIVE", "CONFLICTED")
    assert MemoryLifecycleManager.is_valid_transition("CONFLICTED", "SUPERSEDED")
    assert MemoryLifecycleManager.is_valid_transition("SUPERSEDED", "ARCHIVED")
    assert MemoryLifecycleManager.is_valid_transition("STALE", "INVALIDATED")
    assert MemoryLifecycleManager.is_valid_transition("INVALIDATED", "ARCHIVED")


def test_invalid_transitions():
    """Verify that arbitrary illegal transitions are blocked."""
    # Terminal archive cannot transition to active without candidate flow
    assert not MemoryLifecycleManager.is_valid_transition("ARCHIVED", "ACTIVE")
    # Superseded cannot transition directly to active
    assert not MemoryLifecycleManager.is_valid_transition("SUPERSEDED", "ACTIVE")
    # A candidate cannot become believed without verification or approval.
    # A freshly proposed memory cannot become believed without first being
    # verified or approved -- this is what stops LLM output from self-activating.
    assert not MemoryLifecycleManager.is_valid_transition("CANDIDATE", "ACTIVE")


def _memory(status: str = MemoryState.ACTIVE.value) -> Memory:
    return Memory(
        project_id="p-1",
        layer="L3",
        memory_type="DECISION",
        title="PostgreSQL pgvector storage",
        content="We use pgvector for production vector similarity.",
        summary="Use pgvector",
        status=status,
        confidence=0.8,
        importance=0.8,
        version=1,
    )


def test_reactivation_requires_reverification():
    """A disbelieved memory cannot be quietly restored to ACTIVE.

    STALE -> ACTIVE is a legal edge in the transition graph, but taking it
    requires the caller to assert that it actually re-verified the memory. This
    is the guarantee that stops a status write from substituting for evidence.
    """
    mem = _memory(MemoryState.STALE.value)

    with pytest.raises(InvalidStateTransitionError) as unverified:
        MemoryLifecycleManager.transition(
            mem, new_state=MemoryState.ACTIVE.value, reason="looks fine now"
        )
    assert "re-verification" in str(unverified.value)
    assert mem.status == MemoryState.STALE.value, (
        "refused transition must not mutate state"
    )

    version = MemoryLifecycleManager.transition(
        mem,
        new_state=MemoryState.ACTIVE.value,
        reason="Re-verified: grounding symbol present with matching fingerprint",
        verified=True,
    )
    assert mem.status == MemoryState.ACTIVE.value
    assert version is not None


def test_invalidated_memory_can_never_be_reactivated():
    """INVALIDATED is terminal apart from archival, verified or not."""
    mem = _memory(MemoryState.INVALIDATED.value)

    for verified in (False, True):
        with pytest.raises(InvalidStateTransitionError):
            MemoryLifecycleManager.transition(
                mem,
                new_state=MemoryState.ACTIVE.value,
                reason="attempted resurrection",
                verified=verified,
            )
    assert mem.status == MemoryState.INVALIDATED.value


def test_lifecycle_manager_transition_application():
    """Verify that transition mutations update status, timestamp, and version audit."""
    mem = Memory(
        project_id="p-1",
        layer="L3",
        memory_type="DECISION",
        title="PostgreSQL pgvector storage",
        content="We use pgvector for production vector similarity.",
        summary="Use pgvector",
        status=MemoryState.ACTIVE.value,
        confidence=1.0,
        importance=0.8,
        version=1,
    )

    # 1. Valid transition: ACTIVE -> STALE
    ver1 = MemoryLifecycleManager.transition(
        mem,
        new_state=MemoryState.STALE.value,
        reason="Underlying table schema modified",
    )
    assert mem.status == MemoryState.STALE.value
    assert mem.version == 2
    assert ver1 is not None
    assert ver1.version == 2
    assert "ACTIVE -> STALE" in ver1.change_reason

    # 2. Valid transition: STALE -> ACTIVE, permitted because the caller
    #    re-verified the memory and says so.
    ver2 = MemoryLifecycleManager.transition(
        mem,
        new_state=MemoryState.ACTIVE.value,
        reason="Re-verified against new migration",
        verified=True,
    )
    assert mem.status == MemoryState.ACTIVE.value
    assert mem.version == 3
    assert ver2 is not None
    assert mem.last_verified_at is not None

    # 3. Invalid transition: ACTIVE -> CANDIDATE
    with pytest.raises(InvalidStateTransitionError):
        MemoryLifecycleManager.transition(
            mem,
            new_state=MemoryState.CANDIDATE.value,
            reason="Illegal jump back to candidate",
        )

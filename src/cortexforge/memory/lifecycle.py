"""Explicit finite state machine governing durable memory lifecycle transitions.

Specification (Section 6):
States: CANDIDATE, UNVERIFIED, ACTIVE, STALE, CONFLICTED, SUPERSEDED, INVALIDATED, ARCHIVED.
Arbitrary state transitions are strictly rejected.
"""

from datetime import UTC, datetime
from enum import Enum

from cortexforge.core.models import Memory, MemoryVersion


class MemoryState(str, Enum):
    CANDIDATE = "CANDIDATE"
    UNVERIFIED = "UNVERIFIED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"
    ARCHIVED = "ARCHIVED"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal memory state transition is attempted."""

    def __init__(self, from_state: str, to_state: str, reason: str = "") -> None:
        msg = f"Illegal memory state transition: '{from_state}' -> '{to_state}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.from_state = from_state
        self.to_state = to_state


# Formal transition graph
VALID_TRANSITIONS: dict[str, set[str]] = {
    # Newly proposed observation or candidate memory
    MemoryState.CANDIDATE.value: {
        MemoryState.UNVERIFIED.value,
        MemoryState.ACTIVE.value,
        MemoryState.INVALIDATED.value,
        MemoryState.ARCHIVED.value,
    },
    # Memory created without verified code/test evidence grounding
    MemoryState.UNVERIFIED.value: {
        MemoryState.ACTIVE.value,  # Upon evidence verification
        MemoryState.STALE.value,
        MemoryState.CONFLICTED.value,
        MemoryState.INVALIDATED.value,
        MemoryState.ARCHIVED.value,
    },
    # Fully active, verified, durable memory
    MemoryState.ACTIVE.value: {
        MemoryState.STALE.value,       # When grounded symbol/code is modified
        MemoryState.CONFLICTED.value,  # When contradictory evidence/memory is found
        MemoryState.SUPERSEDED.value,  # When higher-authority replacement is established
        MemoryState.ARCHIVED.value,    # When explicitly decommissioned
    },
    # Stale memory awaiting reverification or invalidation
    MemoryState.STALE.value: {
        MemoryState.ACTIVE.value,       # Re-verified against updated code
        MemoryState.CONFLICTED.value,
        MemoryState.SUPERSEDED.value,
        MemoryState.INVALIDATED.value,  # Symbol deleted or assertion disproven
        MemoryState.ARCHIVED.value,
    },
    # Contradicted memory under dispute
    MemoryState.CONFLICTED.value: {
        MemoryState.ACTIVE.value,       # Resolved in favor of this memory
        MemoryState.SUPERSEDED.value,   # Resolved in favor of new memory
        MemoryState.INVALIDATED.value,
        MemoryState.ARCHIVED.value,
    },
    # Outdated memory superseded by a newer version/decision
    MemoryState.SUPERSEDED.value: {
        MemoryState.ARCHIVED.value,
    },
    # Memory whose underlying premise was deleted or disproven
    MemoryState.INVALIDATED.value: {
        MemoryState.ARCHIVED.value,
    },
    # Terminal archive state
    MemoryState.ARCHIVED.value: set(),
}


class MemoryLifecycleManager:
    """Finite State Machine enforcing verifiable memory status transitions and audit logging."""

    @staticmethod
    def is_valid_transition(from_state: str, to_state: str) -> bool:
        """Check whether transition from from_state to to_state is permissible."""
        from_st = from_state.upper()
        to_st = to_state.upper()
        if from_st == to_st:
            return True
        allowed = VALID_TRANSITIONS.get(from_st, set())
        return to_st in allowed

    @staticmethod
    def transition(
        memory: Memory,
        new_state: str,
        reason: str,
        superseded_by_id: str | None = None,
        conflict_group: str | None = None,
    ) -> MemoryVersion | None:
        """Apply state transition to memory, validating constraints and generating version audit.

        Raises:
            InvalidStateTransitionError: If the transition is prohibited.
        """
        current_state = (memory.status or MemoryState.UNVERIFIED.value).upper()
        target_state = new_state.upper()

        if current_state == target_state:
            # Idempotent re-affirmation (e.g. re-verification maintaining ACTIVE)
            if target_state == MemoryState.ACTIVE.value:
                memory.last_verified_at = datetime.now(UTC)
            return None

        if not MemoryLifecycleManager.is_valid_transition(current_state, target_state):
            raise InvalidStateTransitionError(
                from_state=current_state,
                to_state=target_state,
                reason=reason,
            )

        # Apply state mutation
        memory.status = target_state
        memory.updated_at = datetime.now(UTC)

        if target_state == MemoryState.ACTIVE.value:
            memory.last_verified_at = datetime.now(UTC)

        if superseded_by_id:
            memory.superseded_by_id = superseded_by_id

        if conflict_group:
            memory.conflict_group = conflict_group

        # Record version audit
        memory.version += 1
        audit_version = MemoryVersion(
            memory_id=memory.id,
            version=memory.version,
            previous_version=memory.version - 1,
            content=memory.content,
            change_reason=f"Status transition [{current_state} -> {target_state}]: {reason}",
        )
        return audit_version

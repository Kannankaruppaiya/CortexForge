"""Explicit finite state machine governing durable memory lifecycle transitions.

Specification sections 7 and 43.

States: CANDIDATE, REVIEW_REQUIRED, UNVERIFIED, ACTIVE, STALE, CONFLICTED,
SUPERSEDED, INVALIDATED, ARCHIVED.

Two properties this machine exists to guarantee:

* ``INVALIDATED`` is terminal apart from archival -- a memory whose premise was
  deleted or disproven can never silently return to ACTIVE.
* Leaving a disbelieved state (STALE / CONFLICTED / REVIEW_REQUIRED) for ACTIVE
  requires the caller to assert ``verified=True``, which only the verification and
  approval paths do. Believing something again is therefore always the result of
  new evidence, never of a convenient status write.

Every transition records a ``MemoryVersion`` carrying the actor, commit and reason,
so lifecycle history is auditable rather than inferred.
"""

from datetime import UTC, datetime
from enum import Enum

from cortexforge.core.models import Memory, MemoryVersion


class MemoryState(str, Enum):
    CANDIDATE = "CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
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
    # Newly proposed observation or candidate memory.
    # A candidate may not jump straight to ACTIVE: it must first be verified
    # (UNVERIFIED -> ACTIVE on evidence) or approved (REVIEW_REQUIRED -> ACTIVE),
    # which is what stops LLM output from self-activating (sections 7, 23, 43).
    MemoryState.CANDIDATE.value: {
        MemoryState.REVIEW_REQUIRED.value,
        MemoryState.UNVERIFIED.value,
        MemoryState.CONFLICTED.value,
        MemoryState.SUPERSEDED.value,
        MemoryState.INVALIDATED.value,
        MemoryState.ARCHIVED.value,
    },
    # Awaiting human or review approval before it can be believed (section 43).
    MemoryState.REVIEW_REQUIRED.value: {
        MemoryState.ACTIVE.value,       # Approved by a reviewer or the user
        MemoryState.UNVERIFIED.value,   # Sent back for evidence gathering
        MemoryState.CONFLICTED.value,
        MemoryState.INVALIDATED.value,  # Rejected
        MemoryState.ARCHIVED.value,
    },
    # Memory created without verified code/test evidence grounding
    MemoryState.UNVERIFIED.value: {
        MemoryState.ACTIVE.value,       # Upon evidence verification
        MemoryState.REVIEW_REQUIRED.value,
        MemoryState.STALE.value,
        MemoryState.CONFLICTED.value,
        MemoryState.SUPERSEDED.value,   # A newer statement can replace it unread
        MemoryState.INVALIDATED.value,
        MemoryState.ARCHIVED.value,
    },
    # Fully active, verified, durable memory
    MemoryState.ACTIVE.value: {
        MemoryState.STALE.value,       # When grounded symbol/code is modified
        MemoryState.CONFLICTED.value,  # When contradictory evidence/memory is found
        MemoryState.SUPERSEDED.value,  # When higher-authority replacement is established
        MemoryState.INVALIDATED.value, # When grounded symbol/code is completely deleted
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


# States a memory can only leave for ACTIVE by presenting fresh verification.
_REQUIRES_REVERIFICATION: frozenset[str] = frozenset(
    {
        MemoryState.STALE.value,
        MemoryState.CONFLICTED.value,
        MemoryState.REVIEW_REQUIRED.value,
    }
)


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
        actor: str = "system",
        commit_sha: str | None = None,
        force_version: bool = False,
        verified: bool = False,
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
            if not force_version:
                return None

        if not MemoryLifecycleManager.is_valid_transition(current_state, target_state):
            raise InvalidStateTransitionError(
                from_state=current_state,
                to_state=target_state,
                reason=reason,
            )

        # Re-activation must be earned. Moving a memory that was previously
        # disbelieved (STALE / CONFLICTED) back to ACTIVE requires a caller that
        # actually re-verified it, and says so by passing `verified=True`. Without
        # that, the transition is refused rather than being quietly applied
        # (sections 7 and 8).
        if (
            target_state == MemoryState.ACTIVE.value
            and current_state in _REQUIRES_REVERIFICATION
            and not verified
        ):
            raise InvalidStateTransitionError(
                from_state=current_state,
                to_state=target_state,
                reason=(
                    "re-activation requires successful re-verification; "
                    "call with verified=True from a verification or approval path"
                ),
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
            old_state=current_state,
            new_state=target_state,
            actor=actor,
            commit_sha=commit_sha,
            content=memory.content,
            change_reason=f"Status transition [{current_state} -> {target_state}]: {reason}",
        )
        return audit_version

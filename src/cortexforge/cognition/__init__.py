"""Core cognitive primitives: authority, epistemic state, verification outcomes.

These are the vocabulary the rest of CortexForge reasons in. They are deliberately
free of database and I/O dependencies so that every layer -- models, services,
interfaces and tests -- shares one definition of what a claim, an authority level
or a verification outcome means.
"""

from cortexforge.cognition.authority import (
    AUTHORITY_RANK,
    Authority,
    authority_from_source,
    authority_rank,
    dominates,
    resolve_authority_conflict,
)
from cortexforge.cognition.epistemics import (
    ClaimStatus,
    DecisionCode,
    EpistemicState,
    EvidenceRelation,
    EvidenceType,
    MemoryScope,
    ReasonCode,
    VerificationOutcome,
)

__all__ = [
    "AUTHORITY_RANK",
    "Authority",
    "ClaimStatus",
    "DecisionCode",
    "EpistemicState",
    "EvidenceRelation",
    "EvidenceType",
    "MemoryScope",
    "ReasonCode",
    "VerificationOutcome",
    "authority_from_source",
    "authority_rank",
    "dominates",
    "resolve_authority_conflict",
]

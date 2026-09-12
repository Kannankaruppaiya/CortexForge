"""Epistemic vocabulary: claim status, evidence, verification outcomes, decisions.

Specification sections 3, 4, 5, 9 and 30. The central discipline these enums encode
is that CortexForge distinguishes *data*, *claims*, *evidence*, *verification* and
*decisions*, and that "I do not know" is a first-class, representable answer rather
than a coerced TRUE or FALSE.
"""

from enum import Enum


class EpistemicState(str, Enum):
    """What kind of knowledge a memory or claim represents (section 3).

    Uncertain information must not be forced into ``FACT``.
    """

    FACT = "FACT"
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    LESSON = "LESSON"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"


class MemoryScope(str, Enum):
    """The blast radius a statement applies to (section 3)."""

    PROJECT = "PROJECT"
    MODULE = "MODULE"
    FILE = "FILE"
    SYMBOL = "SYMBOL"
    FEATURE = "FEATURE"
    TASK = "TASK"
    BRANCH = "BRANCH"
    ENVIRONMENT = "ENVIRONMENT"


class ClaimStatus(str, Enum):
    """Lifecycle of an individually evaluable proposition (section 4)."""

    PROPOSED = "PROPOSED"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    REFUTED = "REFUTED"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    UNKNOWN = "UNKNOWN"
    RETIRED = "RETIRED"


class EvidenceType(str, Enum):
    """What kind of artifact an evidence item points at (section 5).

    Evidence is deliberately broader than "a file exists": configuration, schemas,
    API contracts, reviews and user confirmations all ground claims.
    """

    CODE = "CODE"
    SYMBOL = "SYMBOL"
    AST = "AST"
    GIT = "GIT"
    COMMIT = "COMMIT"
    DIFF = "DIFF"
    TEST = "TEST"
    TEST_RESULT = "TEST_RESULT"
    CONFIG = "CONFIG"
    SCHEMA = "SCHEMA"
    API_CONTRACT = "API_CONTRACT"
    DOCUMENTATION = "DOCUMENTATION"
    REVIEW = "REVIEW"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    AGENT_OBSERVATION = "AGENT_OBSERVATION"


class EvidenceRelation(str, Enum):
    """How an evidence item bears on a claim (section 5).

    Negative evidence is stored, not discarded: a claim with two supporting and one
    contradicting evidence item is a different epistemic object from one with two
    supporting items alone.
    """

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    WEAKENS = "WEAKENS"
    SUPERSEDES = "SUPERSEDES"


class EvidenceState(str, Enum):
    """Whether an evidence item still resolves against the repository."""

    INTACT = "INTACT"
    MOVED = "MOVED"
    MODIFIED = "MODIFIED"
    MISSING = "MISSING"
    UNCHECKED = "UNCHECKED"


class VerificationOutcome(str, Enum):
    """Result of evaluating a claim under a policy (section 4).

    ``UNKNOWN`` and ``NOT_APPLICABLE`` are real outcomes, not error states.
    """

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    FAILED = "FAILED"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DecisionCode(str, Enum):
    """What reconciliation decided to do with a memory (section 9)."""

    KEEP = "KEEP"
    REANCHOR = "REANCHOR"
    AMEND = "AMEND"
    REVISE = "REVISE"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    SUPERSEDE = "SUPERSEDE"
    INVALIDATE = "INVALIDATE"
    UNKNOWN = "UNKNOWN"


class ReasonCode(str, Enum):
    """Why reconciliation reached its decision (section 9).

    Reason codes are machine-comparable so that benchmarks and property tests can
    assert *why* the system reached a conclusion, not merely that it did.
    """

    SIGNATURE_CHANGED = "SIGNATURE_CHANGED"
    BODY_CHANGED = "BODY_CHANGED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    SYMBOL_REMOVED = "SYMBOL_REMOVED"
    SYMBOL_RENAMED = "SYMBOL_RENAMED"
    SYMBOL_MOVED = "SYMBOL_MOVED"
    TEST_CONTRADICTION = "TEST_CONTRADICTION"
    TEST_SUPPORT = "TEST_SUPPORT"
    ARCHITECTURE_VIOLATION = "ARCHITECTURE_VIOLATION"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_INTACT = "EVIDENCE_INTACT"
    BEHAVIOUR_UNVERIFIED = "BEHAVIOUR_UNVERIFIED"
    HIGHER_AUTHORITY_CONTRADICTION = "HIGHER_AUTHORITY_CONTRADICTION"
    TEMPORAL_SUCCESSION = "TEMPORAL_SUCCESSION"
    NO_EVIDENCE = "NO_EVIDENCE"
    NOT_AFFECTED = "NOT_AFFECTED"


class TestAttribution(str, Enum):
    """Causal relationship between a change and a test outcome (section 15)."""

    # pytest tries to collect any class whose name starts with "Test". This is a
    # domain enum, not a test case, so collection is disabled explicitly.
    __test__ = False

    INTRODUCED_BY = "INTRODUCED_BY"
    FIXED_BY = "FIXED_BY"
    REGRESSED_BY = "REGRESSED_BY"
    FLAKY = "FLAKY"
    UNRELATED = "UNRELATED"
    UNKNOWN = "UNKNOWN"


# Claim statuses that make a claim usable as current project truth.
TRUSTWORTHY_CLAIM_STATUSES: frozenset[str] = frozenset(
    {ClaimStatus.VERIFIED.value, ClaimStatus.PARTIALLY_VERIFIED.value}
)

# Verification outcomes that justify promoting a claim toward durable truth.
POSITIVE_OUTCOMES: frozenset[str] = frozenset(
    {VerificationOutcome.VERIFIED.value, VerificationOutcome.PARTIALLY_VERIFIED.value}
)

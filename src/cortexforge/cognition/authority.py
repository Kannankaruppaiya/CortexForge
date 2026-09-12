"""Explicit authority hierarchy (specification section 6).

Authority answers "how much does the *origin* of a statement entitle it to be
believed", separately from confidence (how much the accumulated evidence supports
it) and separately from truth (whether verification confirmed it).

The hierarchy is ordinal and total. An LLM-generated statement can never outrank a
user decision, an approved review, a verified test, or direct code evidence -- that
property is enforced by rank comparison, not by numeric confidence, so it cannot be
defeated by a high-confidence hallucination.
"""

from enum import Enum


class Authority(str, Enum):
    """Ordinal authority levels, highest first in ``AUTHORITY_RANK``."""

    USER_CONFIRMED = "USER_CONFIRMED"
    REVIEW_CONFIRMED = "REVIEW_CONFIRMED"
    TEST_VERIFIED = "TEST_VERIFIED"
    CODE_VERIFIED = "CODE_VERIFIED"
    GIT_DERIVED = "GIT_DERIVED"
    AGENT_OBSERVED = "AGENT_OBSERVED"
    LLM_GENERATED = "LLM_GENERATED"
    REPOSITORY_TEXT = "REPOSITORY_TEXT"
    UNTRUSTED = "UNTRUSTED"


# Rank: higher integer == higher authority. Ranks are spaced so that intermediate
# levels can be inserted later without renumbering persisted data.
AUTHORITY_RANK: dict[Authority, int] = {
    Authority.USER_CONFIRMED: 90,
    Authority.REVIEW_CONFIRMED: 80,
    Authority.TEST_VERIFIED: 70,
    Authority.CODE_VERIFIED: 60,
    Authority.GIT_DERIVED: 50,
    Authority.AGENT_OBSERVED: 40,
    Authority.LLM_GENERATED: 30,
    Authority.REPOSITORY_TEXT: 20,
    Authority.UNTRUSTED: 10,
}

# Authority levels that may never, on their own, establish durable project truth.
# Statements at these levels must pass verification before activation (section 23).
PROPOSAL_ONLY_AUTHORITIES: frozenset[Authority] = frozenset(
    {Authority.LLM_GENERATED, Authority.REPOSITORY_TEXT, Authority.UNTRUSTED}
)

# Legacy / free-form source strings mapped onto the hierarchy. Unknown sources
# deliberately fall to AGENT_OBSERVED rather than to a trusted level.
_SOURCE_ALIASES: dict[str, Authority] = {
    "user": Authority.USER_CONFIRMED,
    "user_confirmed": Authority.USER_CONFIRMED,
    "human": Authority.USER_CONFIRMED,
    "review": Authority.REVIEW_CONFIRMED,
    "code_review": Authority.REVIEW_CONFIRMED,
    "review_confirmed": Authority.REVIEW_CONFIRMED,
    "approved": Authority.REVIEW_CONFIRMED,
    "test": Authority.TEST_VERIFIED,
    "tests": Authority.TEST_VERIFIED,
    "verified_test": Authority.TEST_VERIFIED,
    "test_result": Authority.TEST_VERIFIED,
    "code": Authority.CODE_VERIFIED,
    "verified_code": Authority.CODE_VERIFIED,
    "ast": Authority.CODE_VERIFIED,
    "symbol": Authority.CODE_VERIFIED,
    "schema": Authority.CODE_VERIFIED,
    "config": Authority.CODE_VERIFIED,
    "api_contract": Authority.CODE_VERIFIED,
    "git": Authority.GIT_DERIVED,
    "commit": Authority.GIT_DERIVED,
    "diff": Authority.GIT_DERIVED,
    "agent": Authority.AGENT_OBSERVED,
    "agent_observation": Authority.AGENT_OBSERVED,
    "observation": Authority.AGENT_OBSERVED,
    "system": Authority.AGENT_OBSERVED,
    "llm": Authority.LLM_GENERATED,
    "llm_generated": Authority.LLM_GENERATED,
    "consolidation": Authority.LLM_GENERATED,
    "doc": Authority.REPOSITORY_TEXT,
    "docs": Authority.REPOSITORY_TEXT,
    "documentation": Authority.REPOSITORY_TEXT,
    "readme": Authority.REPOSITORY_TEXT,
    "repository_text": Authority.REPOSITORY_TEXT,
    "untrusted": Authority.UNTRUSTED,
    "untrusted_repository_text": Authority.UNTRUSTED,
}


def authority_from_source(source: str | Authority | None) -> Authority:
    """Map a free-form source string onto the authority hierarchy.

    Unknown or missing sources resolve to ``AGENT_OBSERVED`` -- never to a trusted
    level -- so that mislabelled or novel sources fail safe.
    """
    if isinstance(source, Authority):
        return source
    if not source:
        return Authority.AGENT_OBSERVED
    return _SOURCE_ALIASES.get(str(source).strip().lower(), Authority.AGENT_OBSERVED)


def authority_rank(authority: str | Authority | None) -> int:
    """Return the ordinal rank of an authority level."""
    if not isinstance(authority, Authority):
        try:
            authority = Authority(str(authority).strip().upper())
        except (ValueError, AttributeError):
            authority = authority_from_source(authority)
    return AUTHORITY_RANK[authority]


def dominates(a: str | Authority | None, b: str | Authority | None) -> bool:
    """True when ``a`` strictly outranks ``b``."""
    return authority_rank(a) > authority_rank(b)


def is_proposal_only(authority: str | Authority | None) -> bool:
    """True when statements from this authority may not self-activate (section 23)."""
    if not isinstance(authority, Authority):
        try:
            authority = Authority(str(authority).strip().upper())
        except (ValueError, AttributeError):
            authority = authority_from_source(authority)
    return authority in PROPOSAL_ONLY_AUTHORITIES


def resolve_authority_conflict(
    a_authority: str | Authority | None,
    b_authority: str | Authority | None,
    a_confidence: float = 0.0,
    b_confidence: float = 0.0,
    a_recency_wins: bool = False,
) -> tuple[str, str]:
    """Arbitrate between two contradicting statements.

    Returns ``(winner, reason)`` where winner is ``"a"``, ``"b"`` or ``"unresolved"``.

    Authority is consulted *first* and decides outright when the levels differ.
    Confidence is only a tie-breaker within the same authority level, so a
    high-confidence LLM statement can never defeat a lower-confidence user decision.
    When authority and confidence are both indecisive the caller is told the
    conflict is unresolved rather than being handed a fabricated winner (section 30).
    """
    rank_a = authority_rank(a_authority)
    rank_b = authority_rank(b_authority)

    if rank_a != rank_b:
        winner = "a" if rank_a > rank_b else "b"
        hi, lo = (
            (a_authority, b_authority) if winner == "a" else (b_authority, a_authority)
        )
        return winner, f"authority {_name(hi)} outranks {_name(lo)}"

    delta = a_confidence - b_confidence
    if abs(delta) >= 0.15:
        winner = "a" if delta > 0 else "b"
        detail = (
            f"equal authority {_name(a_authority)}; decided by confidence "
            f"{max(a_confidence, b_confidence):.2f} vs {min(a_confidence, b_confidence):.2f}"
        )
        return winner, detail

    if a_recency_wins:
        return (
            "a",
            f"equal authority {_name(a_authority)} and comparable confidence; newer statement preferred",
        )

    detail = (
        f"equal authority {_name(a_authority)} and comparable confidence "
        f"({a_confidence:.2f} vs {b_confidence:.2f}); insufficient grounds to decide"
    )
    return "unresolved", detail


def _name(authority: str | Authority | None) -> str:
    if isinstance(authority, Authority):
        return authority.value
    return str(authority or "UNKNOWN")

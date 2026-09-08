"""Trust levels and provenance policy enforcement for CortexForge."""

from enum import Enum


class TrustLevel(str, Enum):
    """Hierarchical trust levels for memory and evidence sources.

    Higher authority evidence dominates unsupported or low-trust claims.
    """

    USER = "user"
    VERIFIED_TEST = "verified_test"
    VERIFIED_CODE = "verified_code"
    GIT = "git"
    DOCUMENTATION = "documentation"
    AGENT_OBSERVATION = "agent_observation"
    UNTRUSTED_REPOSITORY_TEXT = "untrusted_repository_text"


# Authority score hierarchy [0.0 - 1.0]
TRUST_AUTHORITY: dict[TrustLevel, float] = {
    TrustLevel.VERIFIED_CODE: 1.00,
    TrustLevel.VERIFIED_TEST: 0.95,
    TrustLevel.GIT: 0.80,
    TrustLevel.USER: 0.70,
    TrustLevel.DOCUMENTATION: 0.65,
    TrustLevel.AGENT_OBSERVATION: 0.45,
    TrustLevel.UNTRUSTED_REPOSITORY_TEXT: 0.20,
}


def get_trust_authority(source_type: str | TrustLevel) -> float:
    """Return numeric trust authority score for a source type string."""
    if isinstance(source_type, TrustLevel):
        return TRUST_AUTHORITY.get(source_type, 0.45)

    norm = str(source_type).strip().lower()
    mapping = {
        "verified_code": TrustLevel.VERIFIED_CODE,
        "code": TrustLevel.VERIFIED_CODE,
        "verified_test": TrustLevel.VERIFIED_TEST,
        "test": TrustLevel.VERIFIED_TEST,
        "git": TrustLevel.GIT,
        "user": TrustLevel.USER,
        "doc": TrustLevel.DOCUMENTATION,
        "documentation": TrustLevel.DOCUMENTATION,
        "agent_observation": TrustLevel.AGENT_OBSERVATION,
        "observation": TrustLevel.AGENT_OBSERVATION,
        "untrusted_repository_text": TrustLevel.UNTRUSTED_REPOSITORY_TEXT,
        "untrusted": TrustLevel.UNTRUSTED_REPOSITORY_TEXT,
    }
    level = mapping.get(norm, TrustLevel.AGENT_OBSERVATION)
    return TRUST_AUTHORITY.get(level, 0.45)

"""Policy-driven claim verification (specification sections 4, 7 and 30)."""

from cortexforge.verification.engine import ClaimVerdict, ClaimVerificationEngine
from cortexforge.verification.policies import (
    DEFAULT_POLICIES,
    PolicySpec,
    ensure_default_policies,
    resolve_policies_for_claim,
)

__all__ = [
    "DEFAULT_POLICIES",
    "ClaimVerdict",
    "ClaimVerificationEngine",
    "PolicySpec",
    "ensure_default_policies",
    "resolve_policies_for_claim",
]

"""Verification policies: named, versioned strategies for evaluating claims.

A policy says *how* a claim is checked and *what a check is worth*. Making this
explicit rather than implicit matters for three reasons:

* A verification result records which policy produced it, so a result can be
  re-interpreted later when the policy changes (section 4).
* Policy versions are captured in cognitive snapshots, so replay knows what
  "verified" meant at the time (sections 35 and 36).
* Different kinds of claim need different evidence. A claim grounded in a symbol is
  checked against the AST; a claim grounded in configuration is checked against the
  config file; a claim grounded in nothing is answered ``UNKNOWN``, never guessed.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.cognition.authority import Authority
from cortexforge.cognition.epistemics import EpistemicState, EvidenceType
from cortexforge.core.models import Claim, VerificationPolicy


@dataclass(frozen=True)
class PolicySpec:
    """Declarative definition of a built-in verification policy."""

    name: str
    strategy: str
    description: str
    applies_to_evidence_type: str | None = None
    applies_to_epistemic_state: str | None = None
    minimum_authority: str = Authority.AGENT_OBSERVED.value
    version: int = 1
    parameters: dict[str, Any] = field(default_factory=dict)


# The built-in policy set. Ordered by specificity: the first policy whose filters
# match a claim's evidence is the one that decides it.
DEFAULT_POLICIES: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="symbol_ast_identity",
        strategy="symbol_ast_identity",
        description=(
            "Re-parse the grounding file and confirm the referenced symbol still "
            "exists with an unchanged AST fingerprint. A changed body yields "
            "PARTIALLY_VERIFIED, because the symbol is still there but the "
            "behaviour the claim describes is no longer proven."
        ),
        applies_to_evidence_type=EvidenceType.SYMBOL.value,
        minimum_authority=Authority.CODE_VERIFIED.value,
    ),
    PolicySpec(
        name="code_snippet_integrity",
        strategy="code_snippet_integrity",
        description=(
            "Compare the sha256 of the grounding line range against the hash "
            "recorded when the evidence was captured."
        ),
        applies_to_evidence_type=EvidenceType.CODE.value,
        minimum_authority=Authority.CODE_VERIFIED.value,
    ),
    PolicySpec(
        name="config_presence",
        strategy="file_presence",
        description=(
            "Confirm the configuration, schema or contract file a claim depends on "
            "still exists and still contains the referenced key."
        ),
        applies_to_evidence_type=EvidenceType.CONFIG.value,
        minimum_authority=Authority.CODE_VERIFIED.value,
    ),
    PolicySpec(
        name="test_outcome_support",
        strategy="test_outcome",
        description=(
            "Consult recorded test results touching the claim's symbols. A passing "
            "test verifies; a failing test refutes; a test with a history of "
            "unrelated failures is treated as inconclusive rather than as proof."
        ),
        applies_to_evidence_type=EvidenceType.TEST.value,
        minimum_authority=Authority.TEST_VERIFIED.value,
        parameters={"flaky_failure_ratio": 0.34},
    ),
    PolicySpec(
        name="declared_decision",
        strategy="declared",
        description=(
            "A decision or constraint stated by a user or an approved review is "
            "true because it was decided, not because code agrees with it. Such a "
            "claim is NOT_APPLICABLE to code verification; drift between the "
            "decision and the code is an architecture violation, not a false claim."
        ),
        applies_to_epistemic_state=EpistemicState.DECISION.value,
        minimum_authority=Authority.REVIEW_CONFIRMED.value,
    ),
    PolicySpec(
        name="ungrounded_claim",
        strategy="ungrounded",
        description=(
            "A claim with no usable evidence resolves to UNKNOWN. It is never "
            "assumed true because nothing contradicted it, and never marked false "
            "because nothing supported it (section 30)."
        ),
        minimum_authority=Authority.UNTRUSTED.value,
    ),
)


async def ensure_default_policies(
    session: AsyncSession, project_id: str | None = None
) -> list[VerificationPolicy]:
    """Idempotently install the built-in policies for a project.

    Re-running installs nothing new: policies are keyed by (project, name, version),
    so repeated setup converges on one row per policy (section 37).
    """
    existing_res = await session.execute(
        select(VerificationPolicy).where(VerificationPolicy.project_id == project_id)
    )
    existing = {(p.name, p.version): p for p in existing_res.scalars().all()}

    installed: list[VerificationPolicy] = []
    for spec in DEFAULT_POLICIES:
        found = existing.get((spec.name, spec.version))
        if found is not None:
            installed.append(found)
            continue
        policy = VerificationPolicy(
            project_id=project_id,
            name=spec.name,
            description=spec.description,
            strategy=spec.strategy,
            applies_to_evidence_type=spec.applies_to_evidence_type,
            applies_to_epistemic_state=spec.applies_to_epistemic_state,
            minimum_authority=spec.minimum_authority,
            parameters=dict(spec.parameters),
            version=spec.version,
            is_active=True,
        )
        session.add(policy)
        installed.append(policy)

    await session.flush()
    return installed


def resolve_policies_for_claim(
    claim: Claim, policies: list[VerificationPolicy]
) -> list[VerificationPolicy]:
    """Select the policies that apply to a claim, most specific first.

    Selection is on the claim's *evidence*, not on its text, so a claim is checked
    by the mechanism its grounding actually supports. A claim always matches at
    least the ungrounded policy, so every claim receives an outcome -- possibly
    ``UNKNOWN`` -- and none is silently skipped.
    """
    evidence_types = {
        (link.evidence_type or "").upper()
        for link in (claim.evidence_links or [])
        if (link.relation or "SUPPORTS").upper() != "SUPERSEDES"
    }
    epistemic = (claim.epistemic_state or "").upper()

    matched: list[VerificationPolicy] = []
    fallback: VerificationPolicy | None = None

    for policy in policies:
        if not policy.is_active:
            continue
        if policy.strategy == "ungrounded":
            fallback = policy
            continue
        if policy.applies_to_epistemic_state:
            if policy.applies_to_epistemic_state.upper() == epistemic:
                matched.append(policy)
            continue
        if (
            policy.applies_to_evidence_type
            and policy.applies_to_evidence_type.upper() in evidence_types
        ):
            matched.append(policy)

    if matched:
        return matched
    return [fallback] if fallback is not None else []

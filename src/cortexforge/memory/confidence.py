"""Explainable, evidence-derived confidence scoring (specification section 8).

Two rules govern this module, and both are load-bearing:

1. **Confidence is derived, never incremented.** There is no code path that reads
   the current confidence and adds to it. Every score is recomputed from the
   evidence and verification history as they stand, so re-checking the same
   unchanged evidence a thousand times produces the same number a thousand times.
   Repeated verification is not new information and must not look like it.

2. **The reason is persisted with the number.** Every score returns a component
   breakdown that callers store alongside the value, so any confidence in the
   system can be explained without re-running the scorer.

Confidence answers "how well supported is this", which is distinct from *authority*
("what entitles this to be believed", :mod:`cortexforge.cognition.authority`) and
from *verification outcome* ("did we check, and what happened"). A high-authority
statement with no evidence is not highly confident; it is merely well-sourced.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cortexforge.cognition.authority import (
    Authority,
    authority_from_source,
    authority_rank,
)
from cortexforge.cognition.epistemics import EvidenceRelation, VerificationOutcome

# Authority rank (10..90) mapped onto a 0..1 base credibility. This is the only
# place the ordinal hierarchy becomes a number, and it is monotonic in rank so the
# ordering guaranteed by the authority module survives into scoring.
_MAX_RANK = 90.0
_MIN_BASE = 0.15


def authority_base(authority: str | Authority | None) -> float:
    """Map an authority level onto its base credibility in [0.15, 1.0]."""
    rank = authority_rank(authority)
    return round(_MIN_BASE + (1.0 - _MIN_BASE) * (rank / _MAX_RANK), 4)


# Weighting of each contribution to the final score. They are named, exported and
# asserted on in tests so that changing the model is a visible, reviewable act.
WEIGHTS: dict[str, float] = {
    "authority": 0.40,
    "evidence": 0.25,
    "verification": 0.20,
    "tests": 0.10,
    "freshness": 0.05,
}

# Penalties are subtracted after the weighted sum. Contradiction is deliberately
# heavy: evidence against a claim matters more than evidence for it, because a
# single counterexample refutes a universal statement that any number of
# confirmations only fails to refute.
PENALTIES: dict[str, float] = {
    "contradiction": 0.45,
    "conflict": 0.30,
    "stale": 0.30,
    "invalidated": 0.75,
    "superseded": 0.65,
    "unknown": 0.15,
}

# Confidence floor and ceiling. Nothing reaches 1.0: certainty is not an output
# this system produces (section 30).
MIN_CONFIDENCE = 0.05
MAX_CONFIDENCE = 0.97

# Half-life, in days, of the freshness contribution from the last verification.
VERIFICATION_HALF_LIFE_DAYS = 30.0


@dataclass(frozen=True)
class ConfidenceScoreResult:
    """A confidence value together with the reasoning that produced it."""

    score: float
    components: dict[str, float]
    explanation: str
    # Retained for backwards compatibility with existing callers and the REST layer.
    source_authority: float = 0.0
    evidence_bonus: float = 0.0
    verification_bonus: float = 0.0
    test_bonus: float = 0.0
    staleness_penalty: float = 0.0
    conflict_penalty: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceSummary:
    """Aggregate view of the evidence bearing on a claim or memory."""

    supporting: int = 0
    contradicting: int = 0
    weakening: int = 0
    # Number of *independent* supporting items: items sharing an independence
    # group (same file, same commit, same generator) count once. Ten quotations of
    # one line are one piece of evidence, not ten.
    independent_supporting: int = 0
    # Highest authority among supporting and among contradicting evidence.
    max_supporting_authority: str = Authority.AGENT_OBSERVED.value
    max_contradicting_authority: str | None = None
    missing: int = 0

    @classmethod
    def from_items(cls, items: list[Any] | None) -> "EvidenceSummary":
        """Summarise evidence rows (``ClaimEvidence`` or ``MemoryEvidence``)."""
        if not items:
            return cls()

        supporting = contradicting = weakening = missing = 0
        support_groups: set[str] = set()
        max_support = Authority.UNTRUSTED
        max_contra: Authority | None = None

        for item in items:
            relation = str(
                getattr(item, "relation", EvidenceRelation.SUPPORTS.value)
            ).upper()
            authority = authority_from_source(
                getattr(item, "authority", None) or getattr(item, "source_type", None)
            )
            if str(getattr(item, "state", "")).upper() == "MISSING":
                missing += 1

            if relation == EvidenceRelation.CONTRADICTS.value:
                contradicting += 1
                if max_contra is None or authority_rank(authority) > authority_rank(
                    max_contra
                ):
                    max_contra = authority
            elif relation == EvidenceRelation.WEAKENS.value:
                weakening += 1
            else:
                supporting += 1
                if authority_rank(authority) > authority_rank(max_support):
                    max_support = authority
                group = (
                    getattr(item, "independence_group", None)
                    or getattr(item, "file_path", None)
                    or getattr(item, "evidence_hash", None)
                    or str(id(item))
                )
                support_groups.add(str(group))

        return cls(
            supporting=supporting,
            contradicting=contradicting,
            weakening=weakening,
            independent_supporting=len(support_groups),
            max_supporting_authority=(
                max_support.value if supporting else Authority.AGENT_OBSERVED.value
            ),
            max_contradicting_authority=max_contra.value if max_contra else None,
            missing=missing,
        )


def _evidence_contribution(summary: EvidenceSummary) -> float:
    """Diminishing-returns contribution from independent supporting evidence.

    One independent source is worth much more than zero; the fifth is worth little
    more than the fourth. Dependent duplicates contribute nothing, which is what
    stops "verify the same file repeatedly" from manufacturing confidence.
    """
    n = summary.independent_supporting
    if n <= 0:
        return 0.0
    # 1 -> 0.50, 2 -> 0.75, 3 -> 0.875, 4 -> 0.9375, asymptotic to 1.0
    return round(1.0 - (0.5**n), 4)


def _verification_contribution(
    last_outcome: str | None,
    last_verified_at: datetime | None,
) -> tuple[float, str]:
    """Contribution from the most recent verification outcome and its recency.

    Only the *latest* outcome counts. A claim verified fifty times and then
    refuted is a refuted claim; history does not accumulate into credit.
    """
    outcome = (last_outcome or "").upper()
    if outcome == VerificationOutcome.VERIFIED.value:
        base = 1.0
    elif outcome == VerificationOutcome.PARTIALLY_VERIFIED.value:
        base = 0.6
    elif outcome == VerificationOutcome.NOT_APPLICABLE.value:
        base = 0.3
    elif outcome in (
        VerificationOutcome.FAILED.value,
        VerificationOutcome.CONFLICTED.value,
    ):
        return 0.0, f"latest verification {outcome}"
    else:
        return (
            0.0,
            "never verified" if not outcome else f"latest verification {outcome}",
        )

    if last_verified_at is None:
        return round(base * 0.5, 4), f"{outcome} (no timestamp; decayed)"

    checked = (
        last_verified_at
        if last_verified_at.tzinfo
        else last_verified_at.replace(tzinfo=UTC)
    )
    age_days = max(0.0, (datetime.now(UTC) - checked).total_seconds() / 86400.0)
    decay = 0.5 ** (age_days / VERIFICATION_HALF_LIFE_DAYS)
    return round(base * decay, 4), f"{outcome} {age_days:.1f}d ago (decay {decay:.2f})"


class ConfidenceScorer:
    """Recomputes explainable confidence from authority, evidence and verification."""

    @classmethod
    def score(
        cls,
        authority: str | Authority | None,
        evidence: EvidenceSummary | list[Any] | None = None,
        last_outcome: str | None = None,
        last_verified_at: datetime | None = None,
        status: str = "ACTIVE",
        has_passing_test: bool = False,
        has_failing_test: bool = False,
        conflict_group: str | None = None,
    ) -> ConfidenceScoreResult:
        """Compute confidence in [0.05, 0.97] with a persisted component breakdown."""
        summary = (
            evidence
            if isinstance(evidence, EvidenceSummary)
            else EvidenceSummary.from_items(evidence)
        )

        norm_status = (status or "ACTIVE").upper()
        authority_level = (
            authority_from_source(authority)
            if not isinstance(authority, Authority)
            else authority
        )

        c_authority = authority_base(authority_level)
        c_evidence = _evidence_contribution(summary)
        c_verification, verification_note = _verification_contribution(
            last_outcome, last_verified_at
        )

        if has_failing_test:
            c_tests = 0.0
        elif has_passing_test:
            c_tests = 1.0
        else:
            c_tests = 0.0

        # Freshness of the *statement*, distinct from freshness of its verification.
        c_freshness = 1.0 if last_verified_at else 0.0

        weighted = (
            WEIGHTS["authority"] * c_authority
            + WEIGHTS["evidence"] * c_evidence
            + WEIGHTS["verification"] * c_verification
            + WEIGHTS["tests"] * c_tests
            + WEIGHTS["freshness"] * c_freshness
        )

        penalties = 0.0
        applied: list[str] = []

        if summary.contradicting > 0:
            # Contradicting evidence from a higher authority than any supporting
            # evidence is close to decisive (section 6).
            scale = 1.0
            if summary.max_contradicting_authority and authority_rank(
                summary.max_contradicting_authority
            ) > authority_rank(summary.max_supporting_authority):
                scale = 1.4
            hit = min(0.75, PENALTIES["contradiction"] * scale)
            penalties += hit
            applied.append(
                f"-{hit:.2f} contradicted by {summary.contradicting} item(s)"
                + (
                    f" at higher authority {summary.max_contradicting_authority}"
                    if scale > 1.0
                    else ""
                )
            )

        if summary.missing > 0:
            hit = min(0.40, 0.15 * summary.missing)
            penalties += hit
            applied.append(f"-{hit:.2f} {summary.missing} evidence item(s) missing")

        if norm_status == "STALE":
            penalties += PENALTIES["stale"]
            applied.append(f"-{PENALTIES['stale']:.2f} status STALE")
        elif norm_status == "INVALIDATED":
            penalties += PENALTIES["invalidated"]
            applied.append(f"-{PENALTIES['invalidated']:.2f} status INVALIDATED")
        elif norm_status == "SUPERSEDED":
            penalties += PENALTIES["superseded"]
            applied.append(f"-{PENALTIES['superseded']:.2f} status SUPERSEDED")

        if conflict_group or norm_status == "CONFLICTED":
            penalties += PENALTIES["conflict"]
            applied.append(f"-{PENALTIES['conflict']:.2f} unresolved conflict")

        if (last_outcome or "").upper() == VerificationOutcome.UNKNOWN.value:
            penalties += PENALTIES["unknown"]
            applied.append(
                f"-{PENALTIES['unknown']:.2f} evidence insufficient to decide"
            )

        score = max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, round(weighted - penalties, 4)))

        components = {
            "authority": c_authority,
            "evidence": c_evidence,
            "verification": c_verification,
            "tests": c_tests,
            "freshness": c_freshness,
            "weighted_sum": round(weighted, 4),
            "penalties": round(penalties, 4),
            "independent_supporting": float(summary.independent_supporting),
            "contradicting": float(summary.contradicting),
            "final": score,
        }

        parts = [
            f"authority={authority_level.value} (base {c_authority:.2f}, weight {WEIGHTS['authority']})",
            f"independent evidence={summary.independent_supporting} (contribution {c_evidence:.2f})",
            f"verification: {verification_note} (contribution {c_verification:.2f})",
        ]
        if has_passing_test:
            parts.append("supported by a passing test")
        if has_failing_test:
            parts.append("a related test is failing")
        parts.extend(applied)

        explanation = f"Confidence {score:.2f}: " + "; ".join(parts)

        return ConfidenceScoreResult(
            score=score,
            components=components,
            explanation=explanation,
            source_authority=c_authority,
            evidence_bonus=round(WEIGHTS["evidence"] * c_evidence, 4),
            verification_bonus=round(WEIGHTS["verification"] * c_verification, 4),
            test_bonus=round(WEIGHTS["tests"] * c_tests, 4),
            staleness_penalty=round(penalties if norm_status == "STALE" else 0.0, 4),
            conflict_penalty=round(PENALTIES["conflict"] if conflict_group else 0.0, 4),
            breakdown=components,
        )

    @classmethod
    def calculate_confidence(
        cls,
        source_type: str,
        evidences: list[Any] | None = None,
        is_ast_verified: bool = False,
        status: str = "ACTIVE",
        has_passing_test: bool = False,
        has_failing_test: bool = False,
        last_verified_at: datetime | None = None,
        conflict_group: str | None = None,
    ) -> ConfidenceScoreResult:
        """Backwards-compatible entry point used by the existing memory service.

        ``source_type`` is a legacy free-form string; it is mapped onto the
        authority hierarchy rather than consulted through a second, competing
        weight table.
        """
        return cls.score(
            authority=authority_from_source(source_type),
            evidence=evidences,
            last_outcome=(
                VerificationOutcome.VERIFIED.value
                if is_ast_verified
                else (VerificationOutcome.UNKNOWN.value if last_verified_at else None)
            ),
            last_verified_at=last_verified_at,
            status=status,
            has_passing_test=has_passing_test,
            has_failing_test=has_failing_test,
            conflict_group=conflict_group,
        )

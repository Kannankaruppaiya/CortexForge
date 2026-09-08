"""Explainable confidence scoring model for CortexForge memories."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SOURCE_AUTHORITY_WEIGHTS: dict[str, float] = {
    "verified_code": 1.00,
    "code": 0.90,
    "verified_test": 0.95,
    "test": 0.85,
    "git": 0.80,
    "doc": 0.65,
    "documentation": 0.65,
    "user": 0.70,
    "agent_observation": 0.45,
    "observation": 0.45,
    "untrusted": 0.20,
    "system": 0.75,
}


@dataclass(frozen=True)
class ConfidenceScoreResult:
    """Detailed result of explainable confidence calculation."""

    score: float
    source_authority: float
    evidence_bonus: float
    verification_bonus: float
    test_bonus: float
    staleness_penalty: float
    conflict_penalty: float
    breakdown: dict[str, float]
    explanation: str


class ConfidenceScorer:
    """Computes explainable, multi-factor confidence scores for project memories.

    Conceptual ordering:
    untrusted (0.2) < agent observation (0.45) < doc (0.65) < git (0.80)
    < code-supported (0.90) < code+test verified (1.00)
    """

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
        """Calculate grounded, explainable confidence score between 0.0 and 1.0."""
        # 1. Base source authority
        norm_src = (source_type or "agent_observation").strip().lower()
        base_authority = SOURCE_AUTHORITY_WEIGHTS.get(norm_src, 0.50)

        # 2. Evidence quantity and diversity bonus
        ev_count = len(evidences) if evidences else 0
        ev_bonus = 0.0
        if ev_count > 0:
            # Up to +0.15 for multiple distinct code evidences
            distinct_files = {getattr(e, "file_path", "") for e in evidences if getattr(e, "file_path", "")}
            ev_bonus = min(0.15, 0.05 + (0.03 * (len(distinct_files) - 1)))

        # 3. Verification bonus
        ver_bonus = 0.0
        if is_ast_verified:
            ver_bonus += 0.12
        elif last_verified_at:
            now = datetime.now(UTC)
            dt = last_verified_at if last_verified_at.tzinfo else last_verified_at.replace(tzinfo=UTC)
            if (now - dt).total_seconds() < 86400 * 7:  # verified within 7 days
                ver_bonus += 0.05

        # 4. Test support bonus / penalty
        test_bonus = 0.0
        if has_passing_test:
            test_bonus += 0.15
        elif has_failing_test:
            test_bonus -= 0.30

        # 5. Status penalties
        stale_penalty = 0.0
        conflict_penalty = 0.0
        norm_status = (status or "ACTIVE").upper()
        if norm_status == "STALE":
            stale_penalty = 0.35
        elif norm_status == "INVALIDATED":
            stale_penalty = 0.80
        elif norm_status == "SUPERSEDED":
            stale_penalty = 0.70

        if conflict_group or norm_status == "CONFLICTED":
            conflict_penalty = 0.40

        # Composite score
        raw_score = (
            (base_authority * 0.65)
            + ev_bonus
            + ver_bonus
            + test_bonus
            - stale_penalty
            - conflict_penalty
        )

        final_score = max(0.05, min(1.0, round(raw_score, 4)))

        # Explanatory text
        factors = [f"source_authority={base_authority} ({norm_src})"]
        if ev_bonus > 0:
            factors.append(f"+{ev_bonus:.2f} (evidence_count={ev_count})")
        if ver_bonus > 0:
            factors.append(f"+{ver_bonus:.2f} (verified)")
        if test_bonus != 0:
            factors.append(f"{test_bonus:+.2f} (tests)")
        if stale_penalty > 0:
            factors.append(f"-{stale_penalty:.2f} (stale/invalid)")
        if conflict_penalty > 0:
            factors.append(f"-{conflict_penalty:.2f} (conflicted)")

        explanation = f"Confidence {final_score:.2f} derived from: {', '.join(factors)}"

        return ConfidenceScoreResult(
            score=final_score,
            source_authority=base_authority,
            evidence_bonus=ev_bonus,
            verification_bonus=ver_bonus,
            test_bonus=test_bonus,
            staleness_penalty=stale_penalty,
            conflict_penalty=conflict_penalty,
            breakdown={
                "base_authority": base_authority,
                "evidence_bonus": ev_bonus,
                "verification_bonus": ver_bonus,
                "test_bonus": test_bonus,
                "staleness_penalty": stale_penalty,
                "conflict_penalty": conflict_penalty,
            },
            explanation=explanation,
        )

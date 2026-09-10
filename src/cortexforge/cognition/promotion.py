"""Authoritative memory promotion policy (Specification section 23).

This module enforces the non-negotiable invariant that ACTIVE status
requires verified evidence grounding and valid promotion authority.
Evidence existence != evidence validity, and high importance never promotes
a memory to active truth without verification.
"""

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cortexforge.cognition.authority import (
    Authority,
    authority_rank,
    is_proposal_only,
)
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate
from cortexforge.memory.lifecycle import MemoryState
from cortexforge.security.path_safety import PathSecurity, PathSecurityError

_REVIEW_REQUIRED_TYPES = frozenset(
    {"CONSTRAINT", "ARCHITECTURE", "SECURITY", "ARCHITECTURE_RULE"}
)


@dataclass
class PromotionDecision:
    """The result of evaluating a proposed memory against the promotion policy."""

    eligible: bool
    initial_status: str
    reason: str
    authority: Authority
    verified_evidence: list[Any] = field(default_factory=list)
    requires_human_review: bool = False
    verified_at: datetime | None = None


def evaluate_memory_promotion(
    payload: MemoryCreate,
    project: Project | None,
    authority: Authority,
) -> PromotionDecision:
    """Evaluate whether a memory is eligible for promotion to ACTIVE truth.

    Rules:
    1. Proposal-only authorities (LLM, unverified agent text, repo text) are
       strictly CANDIDATE and never ACTIVE on creation.
    2. High-impact types (DECISIONS, CONSTRAINTS) without verified grounding or
       human confirmation require human review (REVIEW_REQUIRED).
    3. Code evidence presence alone is insufficient: files must physically exist
       within the project workspace, and line ranges must be valid.
    4. Non-existent files or invalid grounding demote the memory to UNVERIFIED
       with verified_at=None.
    5. Importance alone never promotes a claim to truth.
    """
    speaks_for_itself = authority_rank(authority) >= authority_rank(
        Authority.USER_CONFIRMED
    )
    is_high_impact = payload.memory_type.upper() in _REVIEW_REQUIRED_TYPES

    # Rule 1: Proposal-only authorities cannot establish durable truth
    if is_proposal_only(authority):
        return PromotionDecision(
            eligible=False,
            initial_status=MemoryState.CANDIDATE.value,
            reason="Authority is proposal-only; external verification required before activation.",
            authority=authority,
            verified_at=None,
        )

    # Rule 2: High impact memories without grounding or human confirmation
    if is_high_impact and not payload.evidence and not speaks_for_itself:
        return PromotionDecision(
            eligible=False,
            initial_status=MemoryState.REVIEW_REQUIRED.value,
            reason="High-impact memory lacks grounding evidence and user confirmation.",
            authority=authority,
            requires_human_review=True,
            verified_at=None,
        )

    # Rule 3 & 4: Evidence validation
    if payload.evidence:
        verified_ev = []
        all_groundings_valid = True

        for ev in payload.evidence:
            if ev.file_path:
                if not project or not project.local_path:
                    all_groundings_valid = False
                    break
                try:
                    abs_p = PathSecurity.safe_resolve(project.local_path, ev.file_path)
                except (PathSecurityError, ValueError):
                    all_groundings_valid = False
                    break

                if not os.path.exists(abs_p) or not os.path.isfile(abs_p):
                    all_groundings_valid = False
                    break

                # Validate line bounds if line numbers provided
                if ev.line_start is not None and ev.line_start > 0:
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="ignore") as f:
                            total_lines = sum(1 for _ in f)
                        if ev.line_start > total_lines:
                            all_groundings_valid = False
                            break
                        if ev.line_end is not None and ev.line_end < ev.line_start:
                            all_groundings_valid = False
                            break
                    except OSError:
                        all_groundings_valid = False
                        break

                verified_ev.append(ev)
            elif ev.uri:
                # Universal URI locator (git, commit, test, runtime)
                # Validated when verified by corresponding engine
                verified_ev.append(ev)
            else:
                all_groundings_valid = False

        if not all_groundings_valid:
            # Code/test evidence cannot produce CODE_VERIFIED / TEST_VERIFIED authority if invalid/out-of-bounds
            effective_auth = authority
            if authority in (Authority.CODE_VERIFIED, Authority.TEST_VERIFIED):
                effective_auth = Authority.AGENT_OBSERVED

            return PromotionDecision(
                eligible=False,
                initial_status=MemoryState.UNVERIFIED.value,
                reason="One or more evidence groundings could not be validated against the workspace.",
                authority=effective_auth,
                verified_evidence=[],
                verified_at=None,
            )

        # Grounding files exist and line ranges are valid on disk.
        # Direct code/test/user verified sources can be promoted to ACTIVE.
        if authority_rank(authority) >= authority_rank(Authority.CODE_VERIFIED):
            return PromotionDecision(
                eligible=True,
                initial_status=MemoryState.ACTIVE.value,
                reason="Memory verified against physical code grounding in workspace.",
                authority=authority,
                verified_evidence=verified_ev,
                verified_at=datetime.now(UTC),
            )

        # Agent observations: even if files exist, an agent's claim is an observation,
        # requiring verification run or human confirmation before promotion to ACTIVE.
        return PromotionDecision(
            eligible=False,
            initial_status=MemoryState.UNVERIFIED.value,
            reason="Agent observation recorded; pending verification run before active promotion.",
            authority=authority,
            verified_evidence=verified_ev,
            verified_at=None,
        )

    # No evidence provided
    initial = (
        MemoryState.ACTIVE.value if speaks_for_itself else MemoryState.UNVERIFIED.value
    )
    return PromotionDecision(
        eligible=speaks_for_itself,
        initial_status=initial,
        reason=(
            "Direct user confirmation without code evidence."
            if speaks_for_itself
            else "Ungrounded statement recorded as unverified candidate."
        ),
        authority=authority,
        verified_at=None,
    )

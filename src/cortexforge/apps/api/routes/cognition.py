"""REST surface for the cognitive layer: claims, verification, decisions, approval.

These routes are a thin skin over the domain services -- the same services MCP and
the CLI call -- so there is one implementation of each behaviour and no interface
can drift into having its own semantics (specification section 32).

They exist so that the reasoning behind the system's beliefs is inspectable from
outside: which propositions a memory makes, what was checked and found, which
decisions were taken and why, and what is waiting for a human to approve.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortexforge.cognition.epistemics import ClaimStatus
from cortexforge.core.db import get_db_session
from cortexforge.core.models import (
    Claim,
    Memory,
    MemoryDecision,
    Project,
    SuccessEpisode,
    VerificationResult,
    VerificationRun,
)
from cortexforge.memory.consolidation import MemoryConsolidationEngine
from cortexforge.memory.lifecycle import (
    InvalidStateTransitionError,
    MemoryLifecycleManager,
    MemoryState,
)
from cortexforge.observability.audit import AuditAction, read_audit, record_audit
from cortexforge.retrieval.usefulness import RetrievalUsefulnessTracker
from cortexforge.security.auth import (
    Principal,
    RequireProjectAccess,
    get_current_principal,
)
from cortexforge.security.policy import Permission
from cortexforge.verification.engine import ClaimVerificationEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["cognition"])

verification_engine = ClaimVerificationEngine()
consolidation_engine = MemoryConsolidationEngine()
usefulness_tracker = RetrievalUsefulnessTracker()


def _claim_payload(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "memory_id": claim.memory_id,
        "text": claim.text,
        "canonical_text": claim.canonical_text,
        "claim_key": claim.claim_key,
        "status": claim.status,
        "epistemic_state": claim.epistemic_state,
        "authority": claim.authority,
        "scope": claim.scope,
        "confidence": claim.confidence,
        "confidence_explanation": (claim.confidence_components or {}).get(
            "explanation"
        ),
        "last_outcome": claim.last_outcome,
        "last_verified_at": claim.last_verified_at.isoformat()
        if claim.last_verified_at
        else None,
        "valid_from_commit": claim.valid_from_commit,
        "valid_to_commit": claim.valid_to_commit,
        "branch": claim.branch,
        "evidence": [
            {
                "id": link.id,
                "type": link.evidence_type,
                "relation": link.relation,
                "authority": link.authority,
                "file_path": link.file_path,
                "qualified_name": link.qualified_name,
                "line_start": link.line_start,
                "line_end": link.line_end,
                "state": link.state,
                "commit_sha": link.commit_sha,
            }
            for link in (claim.evidence_links or [])
        ],
    }


@router.get(
    "/projects/{project_id}/claims",
    dependencies=[Depends(RequireProjectAccess())],
)
async def list_claims(
    project_id: str,
    claim_status: str | None = Query(None, alias="status"),
    limit: int = Query(100, le=1000),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """The propositions this project holds, with their verification state."""
    stmt = (
        select(Claim)
        .options(selectinload(Claim.evidence_links))
        .where(Claim.project_id == project_id)
        .order_by(Claim.created_at.desc())
        .limit(limit)
    )
    if claim_status:
        stmt = stmt.where(Claim.status == claim_status.upper())

    res = await session.execute(stmt)
    return [_claim_payload(claim) for claim in res.scalars().all()]


@router.get("/memories/{memory_id}/claims")
async def list_memory_claims(
    memory_id: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """The individually evaluable statements inside one memory."""
    memory = await session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    RequireProjectAccess.check_access(principal, memory.project_id)

    res = await session.execute(
        select(Claim)
        .options(selectinload(Claim.evidence_links))
        .where(Claim.memory_id == memory_id, Claim.status != ClaimStatus.RETIRED.value)
        .order_by(Claim.created_at)
    )
    return [_claim_payload(claim) for claim in res.scalars().all()]


@router.post(
    "/projects/{project_id}/verify",
    dependencies=[Depends(RequireProjectAccess())],
)
async def verify_project_claims(
    project_id: str,
    commit_sha: str | None = None,
    branch: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Verify the project's claims and return the run.

    Re-verifying an unchanged project reuses the previous run rather than
    restamping every claim as freshly confirmed, so `reused` distinguishes "we
    checked again" from "nothing has changed since we last checked".
    """
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    run = await verification_engine.verify_project(
        session, project_id, commit_sha=commit_sha, branch=branch, verifier="rest-api"
    )
    await session.commit()

    return {
        "run_id": run.id,
        "reused": run.finished_at is not None and run.status == "COMPLETED",
        "commit_sha": run.commit_sha,
        "claims_evaluated": run.claims_evaluated,
        "verified": run.verified_count,
        "partially_verified": run.partially_verified_count,
        "failed": run.failed_count,
        "conflicted": run.conflicted_count,
        "unknown": run.unknown_count,
        "not_applicable": run.not_applicable_count,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get(
    "/projects/{project_id}/verification-runs",
    dependencies=[Depends(RequireProjectAccess())],
)
async def list_verification_runs(
    project_id: str,
    limit: int = Query(20, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Verification history for a project, newest first."""
    res = await session.execute(
        select(VerificationRun)
        .where(VerificationRun.project_id == project_id)
        .order_by(VerificationRun.started_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": run.id,
            "commit_sha": run.commit_sha,
            "branch": run.branch,
            "trigger": run.trigger,
            "verifier": run.verifier,
            "status": run.status,
            "claims_evaluated": run.claims_evaluated,
            "verified": run.verified_count,
            "partially_verified": run.partially_verified_count,
            "failed": run.failed_count,
            "conflicted": run.conflicted_count,
            "unknown": run.unknown_count,
            "not_applicable": run.not_applicable_count,
            "started_at": run.started_at.isoformat(),
        }
        for run in res.scalars().all()
    ]


@router.get("/claims/{claim_id}/verification-results")
async def list_claim_results(
    claim_id: str,
    limit: int = Query(20, le=200),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Every recorded verdict on one claim, with the policy and reason behind it."""
    claim = await session.get(Claim, claim_id)
    if not claim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found"
        )
    RequireProjectAccess.check_access(principal, claim.project_id)

    res = await session.execute(
        select(VerificationResult)
        .where(VerificationResult.claim_id == claim_id)
        .order_by(VerificationResult.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": result.id,
            "run_id": result.run_id,
            "policy": result.policy_name,
            "policy_version": result.policy_version,
            "outcome": result.outcome,
            "reason_code": result.reason_code,
            "reason": result.reason,
            "evidence_checked": result.evidence_checked,
            "commit_sha": result.commit_sha,
            "verifier": result.verifier,
            "created_at": result.created_at.isoformat(),
        }
        for result in res.scalars().all()
    ]


@router.get(
    "/projects/{project_id}/decisions",
    dependencies=[Depends(RequireProjectAccess())],
)
async def list_decisions(
    project_id: str,
    memory_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(100, le=1000),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Reconciliation decisions: what changed, what was decided, and why."""
    stmt = (
        select(MemoryDecision)
        .where(MemoryDecision.project_id == project_id)
        .order_by(MemoryDecision.created_at.desc())
        .limit(limit)
    )
    if memory_id:
        stmt = stmt.where(MemoryDecision.memory_id == memory_id)
    if decision:
        stmt = stmt.where(MemoryDecision.decision == decision.upper())

    res = await session.execute(stmt)
    return [
        {
            "id": row.id,
            "memory_id": row.memory_id,
            "claim_id": row.claim_id,
            "change_set_id": row.change_set_id,
            "verification_run_id": row.verification_run_id,
            "decision": row.decision,
            "reason_code": row.reason_code,
            "reason": row.reason,
            "evidence": row.evidence,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "commit_sha": row.commit_sha,
            "actor": row.actor,
            "created_at": row.created_at.isoformat(),
        }
        for row in res.scalars().all()
    ]


@router.get(
    "/projects/{project_id}/pending-approvals",
    dependencies=[Depends(RequireProjectAccess())],
)
async def list_pending_approvals(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """Memories waiting on a human decision before they can be believed."""
    res = await session.execute(
        select(Memory)
        .where(
            Memory.project_id == project_id,
            Memory.status.in_(
                [MemoryState.REVIEW_REQUIRED.value, MemoryState.CANDIDATE.value]
            ),
        )
        .order_by(Memory.created_at.desc())
    )
    return [
        {
            "id": memory.id,
            "title": memory.title,
            "summary": memory.summary,
            "content": memory.content,
            "status": memory.status,
            "memory_type": memory.memory_type,
            "layer": memory.layer,
            "authority": memory.authority,
            "confidence": memory.confidence,
            "confidence_explanation": (memory.confidence_components or {}).get(
                "explanation"
            ),
            "created_by": memory.created_by,
            "created_at": memory.created_at.isoformat(),
            "why_pending": (
                "Proposed by a source that cannot establish truth on its own"
                if memory.status == MemoryState.CANDIDATE.value
                else "High-impact knowledge requiring review before activation"
            ),
        }
        for memory in res.scalars().all()
    ]


@router.post("/memories/{memory_id}/approve")
async def approve_memory(
    memory_id: str,
    approver: str = Query(
        ..., description="Who is approving; recorded in the audit log"
    ),
    reason: str = "",
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Approve a proposed memory, activating it under the approver's authority."""
    memory = await session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    RequireProjectAccess.check_access(
        principal, memory.project_id, permission=Permission.MEMORY_VERIFY
    )

    previous_status = memory.status
    authoritative_actor = principal.email or principal.user_id or principal.principal_id
    effective_reason = reason or f"Approved by {approver or authoritative_actor}"

    if (
        memory.memory_type == "LESSON"
        and memory.status == MemoryState.REVIEW_REQUIRED.value
    ):
        # Lessons carry derived-from relations to their source episodes, so
        # approval goes through consolidation, which archives those sources.
        memory = await consolidation_engine.approve_lesson(
            session, memory_id, approver=authoritative_actor, reason=effective_reason
        )
    else:
        try:
            version = MemoryLifecycleManager.transition(
                memory,
                MemoryState.ACTIVE.value,
                reason=effective_reason,
                actor=authoritative_actor,
                verified=True,
            )
        except InvalidStateTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        if version is not None:
            session.add(version)

    await record_audit(
        session,
        action=AuditAction.MEMORY_APPROVED,
        resource_type="memory",
        resource_id=memory_id,
        actor=authoritative_actor,
        project_id=memory.project_id,
        before={"status": previous_status},
        after={"status": memory.status},
        reason=effective_reason,
    )
    await session.commit()
    await session.refresh(memory)

    return {"id": memory.id, "status": memory.status, "authority": memory.authority}


@router.post("/memories/{memory_id}/reject")
async def reject_memory(
    memory_id: str,
    reviewer: str = Query(
        ..., description="Who is rejecting; recorded in the audit log"
    ),
    reason: str = Query(..., description="Why this proposal was rejected"),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Reject a proposed memory. Rejection is terminal and always carries a reason."""
    memory = await session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    RequireProjectAccess.check_access(
        principal, memory.project_id, permission=Permission.MEMORY_VERIFY
    )

    previous_status = memory.status
    authoritative_actor = principal.email or principal.user_id or principal.principal_id
    effective_reason = f"Rejected by {reviewer or authoritative_actor}: {reason}"

    try:
        version = MemoryLifecycleManager.transition(
            memory,
            MemoryState.INVALIDATED.value,
            reason=effective_reason,
            actor=authoritative_actor,
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if version is not None:
        session.add(version)

    await record_audit(
        session,
        action=AuditAction.MEMORY_REJECTED,
        resource_type="memory",
        resource_id=memory_id,
        actor=authoritative_actor,
        project_id=memory.project_id,
        before={"status": previous_status},
        after={"status": memory.status},
        reason=effective_reason,
    )
    await session.commit()
    return {"id": memory.id, "status": memory.status}


@router.get(
    "/projects/{project_id}/successes",
    dependencies=[Depends(RequireProjectAccess())],
)
async def list_successes(
    project_id: str,
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Approaches recorded as having worked on this project."""
    res = await session.execute(
        select(SuccessEpisode)
        .where(SuccessEpisode.project_id == project_id)
        .order_by(SuccessEpisode.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": episode.id,
            "title": episode.title,
            "task_context": episode.task_context,
            "approach": episode.approach,
            "why_it_worked": episode.why_it_worked,
            "affected_files": episode.affected_files,
            "tests_passed": episode.tests_passed,
            "commit_sha": episode.commit_sha,
            "created_at": episode.created_at.isoformat(),
        }
        for episode in res.scalars().all()
    ]


@router.get(
    "/projects/{project_id}/retrieval-quality",
    dependencies=[Depends(RequireProjectAccess())],
)
async def retrieval_quality(
    project_id: str, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """Measured retrieval quality, or an explicit statement that it is unmeasured.

    Null metrics mean no evidence has been recorded for them yet. They are not
    zero, and a client must not render them as such.
    """
    metrics = await usefulness_tracker.measure(session, project_id)
    return {
        "measured": metrics.measured,
        "events": metrics.events,
        "mean_returned": metrics.mean_returned,
        "mean_selected": metrics.mean_selected,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "redundancy": metrics.redundancy,
        "stale_hit_rate": metrics.stale_hit_rate,
        "conflicted_hit_rate": metrics.conflicted_hit_rate,
        "mean_context_tokens": metrics.mean_context_tokens,
        "mean_latency_ms": metrics.mean_latency_ms,
        "task_success_rate": metrics.task_success_rate,
        "notes": metrics.notes,
    }


@router.get(
    "/projects/{project_id}/audit",
    dependencies=[Depends(RequireProjectAccess())],
)
async def project_audit_log(
    project_id: str,
    resource_type: str | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Who changed what, when, and why."""
    entries = await read_audit(
        session, project_id=project_id, resource_type=resource_type, limit=limit
    )
    return [
        {
            "id": entry.id,
            "actor": entry.actor,
            "action": entry.action,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "before": entry.before,
            "after": entry.after,
            "reason": entry.reason,
            "trace_id": entry.trace_id,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in entries
    ]

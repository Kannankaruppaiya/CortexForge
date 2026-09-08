"""GitHub webhook ingestion: signed, idempotent, and audited (sections 33 and 37).

GitHub retries deliveries. Without a delivery ledger, a retry of a push event
re-runs change propagation and re-records the change, duplicating cognitive
updates for something that happened once. Every delivery is therefore recorded by
its ``X-GitHub-Delivery`` id, and a repeat returns the original result instead of
doing the work again.
"""

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project, WebhookDelivery
from cortexforge.observability.audit import AuditAction, record_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])
scanner = RepositoryScanner()
change_propagator = SemanticChangePropagator()


def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """Verify a webhook payload against its HMAC-SHA256 signature.

    With no configured secret the endpoint accepts unsigned payloads, which is
    only tolerable outside production. In production an unset secret is a
    configuration error, not a permissive default: repository webhooks are an
    unauthenticated path into the cognitive state, and accepting anything that
    reaches the port would let anyone rewrite what the system believes.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        environment = os.environ.get(
            "CORTEX_ENV", os.environ.get("ENVIRONMENT", "development")
        ).lower()
        if environment == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "GITHUB_WEBHOOK_SECRET is not configured. Refusing to accept "
                    "unsigned webhooks in production."
                ),
            )
        logger.warning(
            "Accepting an unsigned GitHub webhook because GITHUB_WEBHOOK_SECRET is "
            "unset. This is only safe outside production."
        )
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature_header)


@router.post("/webhooks")
async def handle_github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Ingest a GitHub event and trigger incremental cognitive updates, once."""
    body_bytes = await request.body()
    if not verify_github_signature(body_bytes, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub webhook HMAC-SHA256 signature",
        )

    payload = await request.json()

    if x_github_event == "ping":
        return {"status": "pong", "zen": payload.get("zen")}

    # Deduplicate on GitHub's delivery id. When the header is absent (a hand-rolled
    # caller, or a test), the payload hash stands in: it identifies the same event
    # content, which is the property that actually matters.
    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    delivery_id = x_github_delivery or f"payload:{payload_hash}"

    already = await session.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.provider == "github",
            WebhookDelivery.delivery_id == delivery_id,
        )
    )
    previous = already.scalars().first()
    if previous is not None:
        logger.info("Ignoring duplicate GitHub delivery %s", delivery_id)
        return {
            **previous.result,
            "status": "duplicate_ignored",
            "original_status": previous.result.get("status"),
            "delivery_id": delivery_id,
        }

    # Match repository to project
    repo_info = payload.get("repository", {})
    repo_url = repo_info.get("clone_url") or repo_info.get("html_url")
    repo_name = repo_info.get("name")

    # Locate project in DB
    stmt = select(Project).where(
        (Project.repository_url == repo_url) | (Project.name == repo_name)
    )
    res = await session.execute(stmt)
    project = res.scalars().first()

    if not project:
        result = {
            "status": "ignored",
            "message": f"No registered CortexForge project matches repository '{repo_name}'",
        }
        await _record_delivery(session, delivery_id, x_github_event, None, payload_hash, result)
        await session.commit()
        return result

    modified_files = []
    head_commit = None

    if x_github_event == "push":
        head_commit = payload.get("after")
        for commit in payload.get("commits", []):
            modified_files.extend(commit.get("added", []))
            modified_files.extend(commit.get("modified", []))
            modified_files.extend(commit.get("removed", []))

    elif x_github_event == "pull_request":
        head_commit = payload.get("pull_request", {}).get("head", {}).get("sha")

    # Deduplicate files
    modified_files = list(set(modified_files))

    if modified_files:
        # Index first, then reconcile: reconciliation compares memories against the
        # graph, so it must see the post-change graph rather than the previous one.
        scan_res = await scanner.scan_project(session, project, incremental=True)
        impact_report = await change_propagator.propagate_changes(
            session, project.id, modified_files, mark_stale=True
        )

        result = {
            "status": "processed",
            "event": x_github_event,
            "project": project.name,
            "commit": head_commit,
            "files_modified": len(modified_files),
            "memories_flagged_stale": len(impact_report.memories_flagged_stale),
            "memories_invalidated": len(impact_report.memories_invalidated),
            "memories_reanchored": len(impact_report.memories_reanchored),
            "entities_extracted": scan_res.entities_extracted,
            "change_set_id": impact_report.change_set_id,
        }
    else:
        result = {
            "status": "processed",
            "event": x_github_event,
            "message": "No file changes detected.",
        }

    await _record_delivery(
        session, delivery_id, x_github_event, project.id, payload_hash, result
    )
    await record_audit(
        session,
        action=AuditAction.WEBHOOK_PROCESSED,
        resource_type="webhook_delivery",
        resource_id=delivery_id,
        actor=f"github:{payload.get('sender', {}).get('login', 'unknown')}",
        project_id=project.id,
        after=result,
        reason=f"GitHub {x_github_event} event for {repo_name}",
    )
    await session.commit()
    return result


async def _record_delivery(
    session: AsyncSession,
    delivery_id: str,
    event_type: str,
    project_id: str | None,
    payload_hash: str,
    result: dict[str, Any],
) -> None:
    """Record that this delivery was handled, so a retry does not redo the work."""
    session.add(
        WebhookDelivery(
            provider="github",
            delivery_id=delivery_id,
            event_type=event_type,
            project_id=project_id,
            payload_hash=payload_hash,
            result=result,
        )
    )

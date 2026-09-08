"""GitHub App Webhook Ingestion with HMAC-SHA256 signature verification."""

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.change_propagator import SemanticChangePropagator
from cortexforge.code_intelligence.scanner import RepositoryScanner
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project

router = APIRouter(prefix="/github", tags=["github"])
scanner = RepositoryScanner()
change_propagator = SemanticChangePropagator()


def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """Verify GitHub webhook payload against HMAC-SHA256 signature."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        # Development mode without webhook secret
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
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Ingest GitHub events (push, pull_request) and trigger incremental updates."""
    body_bytes = await request.body()
    if not verify_github_signature(body_bytes, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub webhook HMAC-SHA256 signature",
        )

    payload = await request.json()

    if x_github_event == "ping":
        return {"status": "pong", "zen": payload.get("zen")}

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
        return {
            "status": "ignored",
            "message": f"No registered CortexForge project matches repository '{repo_name}'",
        }

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
        # Run Semantic Change Propagation
        impact_report = await change_propagator.propagate_changes(
            session, project.id, modified_files, mark_stale=True
        )
        # Trigger incremental scan
        scan_res = await scanner.scan_project(session, project, incremental=True)

        return {
            "status": "processed",
            "event": x_github_event,
            "project": project.name,
            "commit": head_commit,
            "files_modified": len(modified_files),
            "memories_flagged_stale": len(impact_report.memories_flagged_stale),
            "entities_extracted": scan_res.entities_extracted,
        }

    return {"status": "processed", "event": x_github_event, "message": "No file changes detected."}

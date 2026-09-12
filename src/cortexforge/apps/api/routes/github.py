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
from cortexforge.core.models import ExternalIdentity, Project, User, WebhookDelivery
from cortexforge.observability.audit import AuditAction, record_audit
from cortexforge.security.auth import Principal, get_current_principal
from cortexforge.security.crypto import decrypt_token

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

    expected_sig = (
        "sha256="
        + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected_sig, signature_header)


MAX_WEBHOOK_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/webhooks")
async def handle_github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Ingest a GitHub event and trigger incremental cognitive updates, once."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"Webhook payload exceeds maximum size limit of {MAX_WEBHOOK_PAYLOAD_BYTES} bytes",
                )
        except ValueError:
            pass

    body_bytes = await request.body()
    if len(body_bytes) > MAX_WEBHOOK_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Webhook payload exceeds maximum size limit of {MAX_WEBHOOK_PAYLOAD_BYTES} bytes",
        )

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

    # Match repository to project strictly by immutable github_repository_id or exact URL
    repo_info = payload.get("repository", {})
    repo_id = repo_info.get("id")
    repo_id_str = str(repo_id) if repo_id is not None else None
    clone_url = repo_info.get("clone_url")
    html_url = repo_info.get("html_url")
    ssh_url = repo_info.get("ssh_url")
    repo_name = repo_info.get("name")

    project = None
    # 1. Primary match: exact immutable github_repository_id
    if repo_id_str:
        stmt = select(Project).where(Project.github_repository_id == repo_id_str)
        res = await session.execute(stmt)
        project = res.scalars().first()

    # 2. Secondary match: exact repository URL match ONLY for legacy projects where github_repository_id IS NULL (§16)
    # Never allow URL fallback to match a project that already has a different immutable repository ID bound.
    if not project:
        urls_to_match = [u.strip() for u in (clone_url, html_url, ssh_url) if u and u.strip()]
        if urls_to_match:
            stmt = select(Project).where(
                Project.github_repository_id.is_(None),
                (
                    (Project.repository_url.in_(urls_to_match))
                    | (Project.clone_url.in_(urls_to_match))
                ),
            )
            res = await session.execute(stmt)
            project = res.scalars().first()

    # Bind immutable github_repository_id if matched via legacy URL fallback
    if project and not project.github_repository_id and repo_id_str:
        project.github_repository_id = repo_id_str

    if not project:
        result = {
            "status": "ignored",
            "message": f"No registered CortexForge project matches repository '{repo_name}' (ID: {repo_id_str})",
        }
        await _record_delivery(
            session, delivery_id, x_github_event, None, payload_hash, result
        )
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
            "project_id": project.id,
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
            "project": project.name,
            "project_id": project.id,
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
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        already = await session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.provider == "github",
                WebhookDelivery.delivery_id == delivery_id,
            )
        )
        previous = already.scalars().first()
        if previous is not None:
            logger.info("Ignoring duplicate concurrent delivery %s", delivery_id)
            return {
                **previous.result,
                "status": "duplicate_ignored",
                "original_status": previous.result.get("status"),
                "delivery_id": delivery_id,
            }
        raise
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


def get_user_github_token(ext: ExternalIdentity | None) -> str | None:
    """Extract and decrypt user's GitHub OAuth token from ExternalIdentity metadata."""
    if not ext or not ext.metadata_json or not isinstance(ext.metadata_json, dict):
        return None
    encrypted_tok = ext.metadata_json.get("encrypted_access_token")
    if encrypted_tok:
        try:
            return decrypt_token(encrypted_tok)
        except Exception as exc:
            logger.warning("Failed to decrypt GitHub access token: %s", exc)
            return None
    legacy_tok = ext.metadata_json.get("access_token")
    if legacy_tok:
        return legacy_tok
    return None


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_github(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Disconnect and revoke GitHub external identity for the current user."""
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
        )
    stmt = select(ExternalIdentity).where(
        ExternalIdentity.user_id == principal.user_id,
        ExternalIdentity.provider == "github",
    )
    res = await session.execute(stmt)
    ext = res.scalars().first()
    if ext:
        await session.delete(ext)
        await session.commit()


@router.get("/repositories")
@router.get("/user/repositories")
async def list_user_github_repositories(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List accessible repositories for the authenticated user via their GitHub OAuth token (§14)."""
    import httpx

    from cortexforge.core.models import ExternalIdentity

    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to list GitHub repositories.",
        )

    user = await session.get(User, principal.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found."
        )

    gh_login = user.github_login
    user_gh_token = None

    ext_stmt = select(ExternalIdentity).where(
        ExternalIdentity.user_id == user.id,
        ExternalIdentity.provider == "github",
    )
    ext_res = await session.execute(ext_stmt)
    ext = ext_res.scalars().first()
    if ext and ext.metadata_json and isinstance(ext.metadata_json, dict):
        if not gh_login:
            gh_login = ext.metadata_json.get("login")
        user_gh_token = get_user_github_token(ext)

    if not gh_login:
        return {
            "connected": False,
            "login": None,
            "repositories": [],
            "message": "GitHub account not connected. Connect GitHub to import repositories.",
        }

    # Per-user GitHub authorization: user must have authorized repository access.
    # We do NOT leak a server-wide global GITHUB_TOKEN to access user repos.
    if not user_gh_token:
        return {
            "connected": True,
            "login": gh_login,
            "repositories": [],
            "requires_repo_access": True,
            "message": "GitHub account is connected for login, but repository access is not authorized. Please connect your GitHub account with repository permissions.",
        }

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CortexForge-App",
        "Authorization": f"Bearer {user_gh_token}",
    }

    repos: list[dict[str, Any]] = []
    error_msg: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            api_url = "https://api.github.com/user/repos?sort=updated&per_page=100"
            resp = await client.get(api_url, headers=headers)
            if resp.status_code == 200:
                raw_data = resp.json()
                if isinstance(raw_data, list):
                    for r in raw_data:
                        repos.append(
                            {
                                "id": str(r.get("id")),
                                "name": r.get("name"),
                                "full_name": r.get("full_name"),
                                "owner": r.get("owner", {}).get("login", gh_login)
                                if isinstance(r.get("owner"), dict)
                                else gh_login,
                                "default_branch": r.get("default_branch", "main"),
                                "description": r.get("description"),
                                "private": r.get("private", False),
                                "clone_url": r.get("clone_url"),
                                "language": r.get("language"),
                            }
                        )
            else:
                error_msg = (
                    f"GitHub returned status {resp.status_code}: {resp.text[:100]}"
                )
    except Exception as exc:
        logger.warning("Failed to fetch repositories from GitHub API: %s", exc)
        error_msg = str(exc)

    return {
        "connected": True,
        "login": gh_login,
        "repositories": repos,
        "error": error_msg,
    }


get_user_github_repositories = list_user_github_repositories


@router.get("/repositories/branches")
async def get_github_repository_branches(
    owner: str,
    repo: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Retrieve branches for a specified GitHub repository using user's scoped authorization (§10)."""
    import re

    import httpx

    from cortexforge.core.models import ExternalIdentity

    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    clean_owner = owner.strip()
    clean_repo = repo.strip()
    if not re.match(r"^[a-zA-Z0-9_.-]+$", clean_owner) or not re.match(
        r"^[a-zA-Z0-9_.-]+$", clean_repo
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository owner or repository name.",
        )

    ext_stmt = select(ExternalIdentity).where(
        ExternalIdentity.user_id == principal.user_id,
        ExternalIdentity.provider == "github",
    )
    ext_res = await session.execute(ext_stmt)
    ext = ext_res.scalars().first()
    gh_token = get_user_github_token(ext)

    if not gh_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Repository branch access requires authorized user GitHub connection. Please connect your GitHub account with repository permissions.",
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CortexForge-App",
        "Authorization": f"Bearer {gh_token}",
    }

    branches: list[str] = []
    default_branch = "main"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{clean_owner}/{clean_repo}/branches?per_page=100",
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    branches = [b.get("name") for b in data if b.get("name")]
            # Also get repo default branch
            r_resp = await client.get(
                f"https://api.github.com/repos/{clean_owner}/{clean_repo}",
                headers=headers,
            )
            if r_resp.status_code == 200:
                r_data = r_resp.json()
                if isinstance(r_data, dict) and r_data.get("default_branch"):
                    default_branch = r_data["default_branch"]
    except Exception as exc:
        logger.warning("Failed to fetch branches from GitHub API: %s", exc)

    if not branches:
        branches = [default_branch]

    return {
        "branches": branches,
        "default_branch": default_branch,
    }


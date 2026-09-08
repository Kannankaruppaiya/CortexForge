"""Integration tests for GitHub Webhooks and Multi-Signal Hybrid Retrieval."""

import hashlib
import hmac
import json
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.apps.api.main import app
from cortexforge.core.db import get_db_session
from cortexforge.core.models import Project
from cortexforge.core.schemas import MemoryCreate, MemoryEvidenceCreate
from cortexforge.memory.service import MemoryService
from cortexforge.retrieval.composer import ContextComposer
from cortexforge.retrieval.engine import HybridRetrievalEngine


@pytest.mark.asyncio
async def test_github_webhook_ping_and_hmac(sample_repo, test_session: AsyncSession):
    """Test GitHub webhook ping handling and HMAC-SHA256 signature verification."""
    secret = "test_webhook_secret_key_123"
    os.environ["GITHUB_WEBHOOK_SECRET"] = secret

    # Override get_db_session dependency with test_session
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ping_payload = {"zen": "Keep it logically awesome."}
        payload_bytes = json.dumps(ping_payload).encode("utf-8")

        # 1. Invalid signature should return 401
        resp_invalid = await client.post(
            "/api/v1/github/webhooks",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": "sha256=invalid_hex_digest",
                "Content-Type": "application/json",
            },
        )
        assert resp_invalid.status_code == 401

        # 2. Valid signature should return pong
        valid_sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        resp_valid = await client.post(
            "/api/v1/github/webhooks",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": valid_sig,
                "Content-Type": "application/json",
            },
        )
        assert resp_valid.status_code == 200
        data = resp_valid.json()
        assert data["status"] == "pong"
        assert data["zen"] == "Keep it logically awesome."

    del os.environ["GITHUB_WEBHOOK_SECRET"]
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_github_webhook_push_event(sample_repo, test_session: AsyncSession):
    """Test GitHub push webhook triggering change propagation and incremental scan."""
    # Register project matching the repo
    project = Project(
        name="WebhookTestRepo",
        repository_url="https://github.com/example/WebhookTestRepo.git",
        local_path=sample_repo,
        status="ACTIVE",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        push_payload = {
            "repository": {
                "name": "WebhookTestRepo",
                "clone_url": "https://github.com/example/WebhookTestRepo.git",
            },
            "after": "abc1234567890",
            "commits": [
                {
                    "added": [],
                    "modified": ["services/auth.py"],
                    "removed": [],
                }
            ],
        }

        resp = await client.post(
            "/api/v1/github/webhooks",
            json=push_payload,
            headers={"X-GitHub-Event": "push"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"
        assert data["event"] == "push"
        assert data["project"] == "WebhookTestRepo"
        assert data["files_modified"] == 1

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_hybrid_retrieval_and_mmr(sample_repo, test_session: AsyncSession):
    """Verify multi-signal hybrid scoring and MMR diversity reranking."""
    project = Project(
        name="RetrievalTestRepo",
        local_path=sample_repo,
        status="ACTIVE",
    )
    test_session.add(project)
    await test_session.commit()
    await test_session.refresh(project)

    memory_service = MemoryService()

    # Seed memories
    mem1 = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="DECISION",
            title="Use Stateless JWT Authentication",
            content="Authentication must use stateless signed JWTs with 15min expiry.",
            summary="Stateless JWT decision",
            importance=0.9,
            evidence=[
                MemoryEvidenceCreate(source_type="file", file_path="services/auth.py", confidence=1.0)
            ],
        ),
    )

    mem2 = await memory_service.create_memory(
        test_session,
        project.id,
        MemoryCreate(
            memory_type="FAILURE",
            title="Token Expiration Race Condition",
            content="Simultaneous token refresh requests produced invalidation race condition.",
            summary="Refresh token concurrency bug",
            importance=0.8,
            evidence=[
                MemoryEvidenceCreate(source_type="file", file_path="services/auth.py", confidence=0.95)
            ],
        ),
    )

    retrieval_engine = HybridRetrievalEngine()
    results = await retrieval_engine.retrieve(
        test_session,
        project_id=project.id,
        query="JWT authentication token expiration",
        target_files=["services/auth.py"],
        limit=5,
    )

    assert len(results) >= 2
    top_result = results[0]
    assert top_result.score > 0.0
    assert top_result.id in (mem1.id, mem2.id)

    # Test structured context composition
    composer = ContextComposer(retrieval_engine=retrieval_engine)
    context_md = await composer.build_context(
        test_session,
        project_id=project.id,
        task_text="Refactor token expiration handling in auth service",
        profile="small",
        target_files=["services/auth.py"],
    )

    assert "CORTEXFORGE VERIFIED PROJECT CONTEXT" in context_md
    assert "RetrievalTestRepo" in context_md
    assert "JWT" in context_md or "Token" in context_md


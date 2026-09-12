"""Integration tests for first-class cognitive REST endpoints."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.core.db import init_db, session_scope
from cortexforge.core.models import (
    CodeEntity,
    FailureEpisode,
    Memory,
    MemoryEvidence,
    Project,
    Relationship,
    TestRun,
)


@pytest.mark.asyncio
async def test_cognitive_rest_endpoints(tmp_path):
    await init_db()

    project_dir = str(tmp_path / "test_repo")
    os.makedirs(project_dir, exist_ok=True)

    async with session_scope() as session:
        project = Project(
            name="CognitiveTestRepo",
            local_path=project_dir,
            status="INDEXED",
            last_indexed_commit="c0ffee1",
        )
        session.add(project)
        await session.flush()
        project_id = project.id

        # Add entities & relationship for architecture violation test
        e1 = CodeEntity(
            project_id=project_id,
            entity_type="class",
            name="OrderController",
            qualified_name="apps.web.OrderController",
            file_path="apps/web/controllers.py",
            start_line=1,
            end_line=20,
            content_hash="hash1",
            language="python",
        )
        e2 = CodeEntity(
            project_id=project_id,
            entity_type="class",
            name="DatabaseConnection",
            qualified_name="infra.db.DatabaseConnection",
            file_path="infra/db/connection.py",
            start_line=1,
            end_line=30,
            content_hash="hash2",
            language="python",
        )
        session.add_all([e1, e2])
        await session.flush()

        rel = Relationship(
            project_id=project_id,
            source_entity_id=e1.id,
            target_entity_id=e2.id,
            relationship_type="imports",
            confidence=1.0,
            source="ast",
        )
        session.add(rel)

        # Add memory with evidence
        mem = Memory(
            project_id=project_id,
            layer="L1",
            memory_type="ARCHITECTURE",
            title="Layer Isolation Rule",
            content="Web controllers must not access database directly.",
            summary="Web isolation invariant",
            status="ACTIVE",
            confidence=0.95,
            importance=0.9,
            source_type="code",
            version=1,
            created_by="agent",
        )
        session.add(mem)
        await session.flush()

        ev = MemoryEvidence(
            project_id=project_id,
            memory_id=mem.id,
            source_type="code",
            file_path="apps/web/controllers.py",
            symbol_id=e1.id,
            commit_sha="c0ffee1",
            evidence_hash="ev1",
            confidence=0.9,
        )
        session.add(ev)

        # Add test run
        tr = TestRun(
            project_id=project_id,
            framework="pytest",
            environment="local",
            status="PASSED",
            total_tests=1,
            passed_count=1,
            failed_count=0,
            duration_ms=45.0,
        )
        session.add(tr)

        # Add failure episode
        fe = FailureEpisode(
            project_id=project_id,
            error_class="OperationalError",
            error_message="Cannot connect to database pool",
            failure_signature="OperationalError:connection_refused",
            attempted_approach="increase pool size",
            rejected_reason="connection refused from host",
            affected_files=["infra/db/connection.py"],
        )
        session.add(fe)
        await session.commit()
        memory_id = mem.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Add Architecture Rule
        rule_res = await client.post(
            f"/api/v1/projects/{project_id}/architecture/rules",
            json={
                "rule_name": "No direct DB from web",
                "description": "Web controllers must not access database directly",
                "forbidden_source_pattern": "apps/web/*",
                "forbidden_target_pattern": "infra/db/*",
                "severity": "CRITICAL",
            },
        )
        assert rule_res.status_code == 201
        rule_data = rule_res.json()
        assert rule_data["rule_name"] == "No direct DB from web"

        # 2. List Architecture Rules
        list_rules_res = await client.get(
            f"/api/v1/projects/{project_id}/architecture/rules"
        )
        assert list_rules_res.status_code == 200
        assert len(list_rules_res.json()) >= 1

        # 3. Check Architecture Violations
        viol_res = await client.get(
            f"/api/v1/projects/{project_id}/architecture/violations"
        )
        assert viol_res.status_code == 200
        viols = viol_res.json()
        assert len(viols) == 1
        assert "CRITICAL" in viols[0]["violation_details"]

        # 4. Provenance Trace
        prov_res = await client.get(f"/api/v1/memories/{memory_id}/provenance")
        assert prov_res.status_code == 200
        prov_data = prov_res.json()
        assert prov_data["memory_id"] == memory_id
        assert len(prov_data["evidences"]) == 1
        assert len(prov_data["symbols"]) == 1
        assert "c0ffee1" in prov_data["commits"]

        # 5. Take Cognitive Snapshot
        snap_res = await client.post(
            f"/api/v1/projects/{project_id}/snapshots",
            params={"commit_sha": "c0ffee1"},
        )
        assert snap_res.status_code == 201
        snap_data = snap_res.json()
        assert snap_data["commit_sha"] == "c0ffee1"

        # 6. List Snapshots
        list_snap_res = await client.get(f"/api/v1/projects/{project_id}/snapshots")
        assert list_snap_res.status_code == 200
        assert len(list_snap_res.json()) >= 1

        # 7. Replay State at Commit -- answered from the snapshot taken then.
        replay_res = await client.post(
            f"/api/v1/projects/{project_id}/snapshots/c0ffee1/replay"
        )
        assert replay_res.status_code == 200
        replay_data = replay_res.json()
        assert replay_data["commit_sha"] == "c0ffee1"
        assert replay_data["replay_available"] is True
        assert replay_data["state_hash"]
        # The believed set is recorded memory-by-memory, not summarised as a count.
        recorded = replay_data["believed_memories"] + replay_data["withheld_memories"]
        assert any(entry["memory_id"] for entry in recorded)

        # Replaying a commit that was never snapshotted must say so rather than
        # answering with present-day belief dressed up as history.
        absent_res = await client.post(
            f"/api/v1/projects/{project_id}/snapshots/deadbeef/replay"
        )
        assert absent_res.status_code == 200
        absent_data = absent_res.json()
        assert absent_data["replay_available"] is False
        assert "not captured" in absent_data["reason"]

        # 8. List Test Runs
        tests_res = await client.get(f"/api/v1/projects/{project_id}/tests")
        assert tests_res.status_code == 200
        assert len(tests_res.json()) >= 1

        # 9. List Failure Episodes
        failures_res = await client.get(f"/api/v1/projects/{project_id}/failures")
        assert failures_res.status_code == 200
        assert len(failures_res.json()) >= 1
        assert failures_res.json()[0]["error_class"] == "OperationalError"

        # 10. Mutation Benchmark Endpoint
        mut_res = await client.post(
            f"/api/v1/projects/{project_id}/mutations/benchmark"
        )
        assert mut_res.status_code == 200
        mut_data = mut_res.json()
        assert "results" in mut_data
        assert len(mut_data["results"]) == 4
        assert mut_data["all_passed"] is True

"""Authentication and Individual-User Authorization Scalability Benchmark (§29).

Labels:
- [MEASURED]: Empirical run on this hardware with measured latencies and ops/sec.
- [TARGET]: Design target specification for production capacity.
- [ESTIMATED]: Calculated projection based on measured single-core baseline.
- [NOT TESTED]: Scenarios requiring external distributed cluster / multi-node infrastructure.
"""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.apps.mcp.server import _resolve_project, set_mcp_caller
from cortexforge.core.db import session_scope
from cortexforge.core.models import (
    Agent,
    AgentProjectPermission,
    Project,
    User,
)
from cortexforge.security.crypto import (
    PasswordHasher,
    generate_agent_key,
    generate_session_token,
    hash_token,
)


@pytest.mark.asyncio
async def test_auth_throughput_and_scalability_benchmark(test_session):
    """Run honest, empirical benchmarks for authentication and authorization subsystems."""
    print("\n" + "=" * 70)
    print(" CORTEXFORGE INDIVIDUAL-USER AUTH & SCALABILITY REPORT (§29)")
    print("=" * 70)

    # 1. MEASURE: Password Hashing Latency (Scrypt / Argon2id memory-hard)
    pwd = "BenchmarkPassword123!"
    hash_start = time.perf_counter()
    pwd_hash = PasswordHasher.hash(pwd)
    hash_latency_ms = (time.perf_counter() - hash_start) * 1000

    verify_start = time.perf_counter()
    PasswordHasher.verify(pwd, pwd_hash)
    verify_latency_ms = (time.perf_counter() - verify_start) * 1000

    print(f"\n[MEASURED] Password Hashing Latency:       {hash_latency_ms:.2f} ms")
    print(f"[MEASURED] Password Verification Latency:  {verify_latency_ms:.2f} ms")

    # 2. MEASURE: Session Token Generation & Hashing Throughput
    n_tokens = 1000
    t0 = time.perf_counter()
    for _ in range(n_tokens):
        raw = generate_session_token()
        _ = hash_token(raw)
    token_elapsed = time.perf_counter() - t0
    token_throughput = n_tokens / token_elapsed

    print(
        f"[MEASURED] Session Token Hash Throughput:   {token_throughput:.0f} tokens/sec"
    )

    # 3. MEASURE: User Session Validation & Project Authorization Latency
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register benchmark user
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "bench_user@cortexforge.dev", "password": "Password123!"},
        )
        assert reg_resp.status_code == 201
        session_token = reg_resp.json()["token"]

        # Measure 100 /auth/me session checks
        n_me_checks = 100
        t0 = time.perf_counter()
        for _ in range(n_me_checks):
            me_resp = await client.get(
                "/api/v1/auth/me",
                headers={"Cookie": f"cortex_session={session_token}"},
            )
            assert me_resp.status_code == 200
        me_elapsed = time.perf_counter() - t0
        me_throughput = n_me_checks / me_elapsed
        me_avg_ms = (me_elapsed / n_me_checks) * 1000

        print(
            f"[MEASURED] Session Validation Throughput:  {me_throughput:.0f} req/sec (avg {me_avg_ms:.2f} ms)"
        )

        # Create project and measure project listing latency
        import tempfile

        tmp_p = tempfile.mkdtemp(prefix="bench_proj_")
        await client.post(
            "/api/v1/projects",
            json={"name": "Benchmark Project", "local_path": tmp_p},
            headers={"Cookie": f"cortex_session={session_token}"},
        )

        n_list_checks = 50
        t0 = time.perf_counter()
        for _ in range(n_list_checks):
            list_resp = await client.get(
                "/api/v1/projects",
                headers={"Cookie": f"cortex_session={session_token}"},
            )
            assert list_resp.status_code == 200
        list_elapsed = time.perf_counter() - t0
        list_avg_ms = (list_elapsed / n_list_checks) * 1000

        print(
            f"[MEASURED] Project Listing (Authorized):    {n_list_checks / list_elapsed:.0f} req/sec (avg {list_avg_ms:.2f} ms)"
        )

    # 4. MEASURE: MCP Agent Authorization Resolution
    async with session_scope() as session:
        # Create user and project
        u = User(
            id="bench-u1",
            email="u1@bench.local",
            display_name="Bench User 1",
            status="ACTIVE",
        )
        p = Project(
            id="bench-p1",
            name="Bench P1",
            local_path="bench_p1",
            owner_user_id="bench-u1",
            status="ACTIVE",
        )
        agent_key, agent_key_h = generate_agent_key()
        a = Agent(
            id="bench-a1",
            owner_user_id="bench-u1",
            name="Agent 1",
            type="claude",
            status="ACTIVE",
            api_key_hash=agent_key_h,
        )
        perm = AgentProjectPermission(
            agent_id="bench-a1", project_id="bench-p1", scopes=["read", "write"]
        )

        session.add_all([u, p, a, perm])
        await session.commit()

        n_mcp = 100
        t0 = time.perf_counter()
        for _ in range(n_mcp):
            set_mcp_caller(token=agent_key)
            res = await _resolve_project(session, "bench-p1")
            assert res is not None
        mcp_elapsed = time.perf_counter() - t0
        mcp_throughput = n_mcp / mcp_elapsed
        mcp_avg_ms = (mcp_elapsed / n_mcp) * 1000

        print(
            f"[MEASURED] MCP Agent Auth Resolution:      {mcp_throughput:.0f} checks/sec (avg {mcp_avg_ms:.2f} ms)"
        )

    # 5. HONEST ARCHITECTURAL TARGETS & ESTIMATIONS
    print("\n" + "-" * 70)
    print(" SYSTEM TARGET CAPACITY & ARCHITECTURAL MODEL (§29)")
    print("-" * 70)
    print(" Registered Individual Users: [TARGET]     100,000")
    print(" Active Concurrent Users:     [TARGET]      10,000")
    print(" Concurrent API Requests:     [TARGET]       1,000 req/sec")
    print(" Concurrent Scans:            [TARGET]         100 concurrent")
    print(" Concurrent MCP Workloads:    [TARGET]         100 concurrent")
    print(
        f" Session Cache Sizing:        [ESTIMATED]   ~{10000 * 0.5:.1f} MB (at 500B/session in Redis)"
    )
    print(
        " Database Connections:        [ESTIMATED]   20-50 pooled connections per API worker"
    )
    print(
        " Multi-Region Cluster Scale:  [NOT TESTED]  (Requires multi-node Kubernetes / Cloud Run setup)"
    )
    print("=" * 70 + "\n")

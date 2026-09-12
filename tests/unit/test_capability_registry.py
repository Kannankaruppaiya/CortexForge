"""Unit tests for machine-readable Capability Registry (Specification §42)."""

import pytest
from httpx import ASGITransport, AsyncClient

from cortexforge.apps.api.main import app
from cortexforge.core.capabilities import CapabilityRegistry, CapabilityStatus


def test_capability_registry_initialization():
    """Verify registry loads all capabilities with valid status classifications."""
    reg = CapabilityRegistry.get_instance()
    summary = reg.summary()

    assert summary[CapabilityStatus.IMPLEMENTED.value] > 10
    assert summary[CapabilityStatus.DISABLED.value] >= 1

    tree_sitter = reg.get_capability("tree_sitter_scan")
    assert tree_sitter is not None
    assert tree_sitter.status == CapabilityStatus.IMPLEMENTED
    assert len(tree_sitter.implementation_files) > 0
    assert len(tree_sitter.tests) > 0


def test_capability_filtering_and_retrieval():
    """Verify filtering by status and category."""
    reg = CapabilityRegistry.get_instance()

    mem_caps = reg.list_capabilities(category="memory")
    assert len(mem_caps) >= 4
    for cap in mem_caps:
        assert cap.category == "memory"

    implemented_only = reg.list_capabilities(status=CapabilityStatus.IMPLEMENTED)
    for cap in implemented_only:
        assert cap.status == CapabilityStatus.IMPLEMENTED


def test_implemented_capabilities_have_files_and_tests():
    """Verify every IMPLEMENTED capability records truthful implementation files and tests."""
    reg = CapabilityRegistry.get_instance()
    for cap in reg.list_capabilities(status=CapabilityStatus.IMPLEMENTED):
        assert len(cap.implementation_files) > 0, (
            f"{cap.capability_id} missing implementation_files"
        )
        assert len(cap.tests) > 0, f"{cap.capability_id} missing tests"


@pytest.mark.asyncio
async def test_api_capabilities_endpoint():
    """Verify REST API exposes machine-readable capability registry."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "capabilities" in data
        assert data["summary"]["IMPLEMENTED"] > 10

        # Filter by status
        resp_filtered = await client.get("/api/v1/capabilities?status=IMPLEMENTED")
        assert resp_filtered.status_code == 200
        filtered_data = resp_filtered.json()
        assert all(c["status"] == "IMPLEMENTED" for c in filtered_data["capabilities"])

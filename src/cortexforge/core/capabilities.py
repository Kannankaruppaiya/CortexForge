"""Machine-readable capability registry for CortexForge (Specification §42).

Defines the verified operational capabilities of the system, their implementation
locations, entry points, testing coverage, and truthful status classification:
IMPLEMENTED, PARTIAL, EXPERIMENTAL, DISABLED, or UNSUPPORTED.
"""

import logging
import os
import subprocess
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CapabilityStatus(str, Enum):
    """Truthful status classification for CortexForge capabilities."""

    IMPLEMENTED = "IMPLEMENTED"
    PARTIAL = "PARTIAL"
    EXPERIMENTAL = "EXPERIMENTAL"
    DISABLED = "DISABLED"
    UNSUPPORTED = "UNSUPPORTED"


def resolve_current_commit() -> str:
    """Resolve the current active commit SHA or symbolic Git reference."""
    env_commit = os.environ.get("CORTEX_BUILD_COMMIT") or os.environ.get("GIT_COMMIT")
    if env_commit:
        return env_commit.strip()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as exc:
        logger.debug("Failed to resolve git rev-parse: %s", exc)
    return "HEAD"


class CapabilityEntry(BaseModel):
    """Detailed metadata for a single system capability."""

    capability_id: str
    name: str
    category: str
    status: CapabilityStatus
    implementation_files: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    limitations: str | None = None
    last_verified_commit: str = Field(default_factory=resolve_current_commit)


class CapabilityRegistry:
    """Singleton registry tracking all system capabilities and operational states."""

    _instance: "CapabilityRegistry | None" = None

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityEntry] = {}
        self._register_all()

    @classmethod
    def get_instance(cls) -> "CapabilityRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, entry: CapabilityEntry) -> None:
        self._capabilities[entry.capability_id] = entry

    def get_capability(self, capability_id: str) -> CapabilityEntry | None:
        return self._capabilities.get(capability_id)

    def list_capabilities(
        self,
        *,
        status: CapabilityStatus | None = None,
        category: str | None = None,
    ) -> list[CapabilityEntry]:
        items = list(self._capabilities.values())
        if status is not None:
            items = [c for c in items if c.status == status]
        if category is not None:
            items = [c for c in items if c.category == category]
        return items

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in CapabilityStatus}
        for entry in self._capabilities.values():
            counts[entry.status.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "capabilities": [c.model_dump() for c in self._capabilities.values()],
        }

    def _register_all(self) -> None:
        """Register all verified capabilities with truthful implementation metadata."""
        entries = [
            # 1. Code Intelligence & Scanning
            CapabilityEntry(
                capability_id="tree_sitter_scan",
                name="Repository AST Scanning",
                category="code_intelligence",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/code_intelligence/scanner.py"],
                entry_points=[
                    "CLI: cortex scan",
                    "MCP: repo_scan",
                    "API: POST /projects/{id}/scan",
                ],
                tests=["tests/integration/test_scanner_and_graph.py"],
                limitations="Supports Python, JavaScript, TypeScript, Java, and Go.",
            ),
            CapabilityEntry(
                capability_id="incremental_graph_update",
                name="Incremental Relationship Graph Preservation",
                category="code_intelligence",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/code_intelligence/scanner.py"],
                entry_points=["RepositoryScanner.scan_project(incremental=True)"],
                tests=[
                    "tests/integration/test_scanner_and_graph.py::test_incremental_relationship_graph_preservation"
                ],
                limitations="Preserves untouched files and inbound cross-file relationships transactionally.",
            ),
            CapabilityEntry(
                capability_id="ast_semantic_diff",
                name="AST Semantic Diff & Symbol Analysis",
                category="code_intelligence",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/code_intelligence/treesitter/semantic_diff.py"
                ],
                entry_points=["SemanticDiffAnalyzer.diff_code()"],
                tests=["tests/unit/test_semantic_diff.py"],
            ),
            CapabilityEntry(
                capability_id="symbol_lineage",
                name="Symbol Lineage Tracking & Evidence Reanchoring",
                category="code_intelligence",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/code_intelligence/treesitter/semantic_diff.py",
                ],
                entry_points=["are_symbols_lineage_match()", "SymbolLineage ORM"],
                tests=["tests/unit/test_lineage_and_invariants.py"],
            ),
            # 2. Memory & Propositions
            CapabilityEntry(
                capability_id="claim_proposition_model",
                name="First-Class Claim / Proposition Domain Model",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/memory/claims.py",
                ],
                entry_points=[
                    "ClaimService",
                    "API: /projects/{id}/claims",
                    "MCP: memory_get_claims",
                ],
                tests=[
                    "tests/unit/test_claim_service.py",
                    "tests/integration/test_claim_truth_pipeline.py",
                ],
            ),
            CapabilityEntry(
                capability_id="evidence_authority_lattice",
                name="Evidence Taxonomy & Ordinal Authority Lattice",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/security/trust.py",
                    "src/cortexforge/memory/claims.py",
                ],
                entry_points=[
                    "ClaimService.add_evidence",
                    "Authority ordering in ClaimConflictResolver",
                ],
                tests=["tests/integration/test_claim_truth_pipeline.py"],
                limitations="Runtime and test verification dominate code verification and agent observation.",
            ),
            CapabilityEntry(
                capability_id="verification_history",
                name="First-Class Immutable Verification Runs",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/verification/engine.py",
                ],
                entry_points=[
                    "ClaimVerificationEngine.verify_claim",
                    "API: /claims/{id}/verify",
                ],
                tests=["tests/unit/test_verification_engine.py"],
                limitations="Repeated runs against identical evidence hashes do not inflate confidence.",
            ),
            CapabilityEntry(
                capability_id="temporal_cognition",
                name="Temporal Validity Intervals & Point-in-Time Querying",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/memory/service.py",
                    "src/cortexforge/retrieval/vector_store.py",
                ],
                entry_points=[
                    "list_memories(at_commit, as_of_time)",
                    "VectorStore.search(at_commit, as_of_time)",
                ],
                tests=[
                    "tests/evaluation/test_temporal_truth.py",
                    "tests/unit/test_temporal_cognition.py",
                ],
            ),
            CapabilityEntry(
                capability_id="branch_workspace_cognition",
                name="Branch & Workspace Isolation",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/core/models.py",
                    "src/cortexforge/memory/service.py",
                    "src/cortexforge/memory/conflict_resolver.py",
                ],
                entry_points=["MemoryService.create_memory(branch, workspace_id)"],
                tests=["tests/evaluation/test_temporal_truth.py"],
                limitations="Observations on feature branches do not pollute main branch truth.",
            ),
            CapabilityEntry(
                capability_id="optimistic_concurrency",
                name="Multi-Agent Optimistic Locking (Version CAS)",
                category="memory",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/memory/service.py",
                    "src/cortexforge/apps/api/routes/memories.py",
                    "src/cortexforge/apps/mcp/server.py",
                ],
                entry_points=[
                    "MemoryService.update_memory(expected_version)",
                    "HTTP 409 Conflict",
                ],
                tests=["tests/integration/test_concurrency.py"],
            ),
            CapabilityEntry(
                capability_id="mcp_write_safety",
                name="MCP Memory Write Safety & Verification Gate",
                category="security",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/apps/mcp/server.py"],
                entry_points=["MCP: memory_create"],
                tests=["tests/integration/test_mcp_safety.py"],
                limitations="Agent-supplied memories require verified code grounding or remain unverified candidates.",
            ),
            # 3. Retrieval
            CapabilityEntry(
                capability_id="pgvector_search",
                name="PostgreSQL pgvector Vector Similarity Search",
                category="retrieval",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/retrieval/vector_store.py"],
                entry_points=["VectorStore.search(project_id, query_vector)"],
                tests=["tests/integration/test_postgres_vector_retrieval.py"],
                limitations="Uses pgvector cosine distance operator in PostgreSQL; deterministic in-memory fallback in SQLite.",
            ),
            CapabilityEntry(
                capability_id="context_token_budget",
                name="Hard Token Budget Context Composition",
                category="retrieval",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/retrieval/composer.py"],
                entry_points=["ContextComposer.build_context"],
                tests=["tests/unit/test_context_composer.py"],
            ),
            CapabilityEntry(
                capability_id="retrieval_explainability",
                name="Retrieval Explainability (why_selected)",
                category="retrieval",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/retrieval/composer.py"],
                entry_points=["ContextComposer.build_context -> why_selected payload"],
                tests=["tests/unit/test_context_composer.py"],
            ),
            # 4. Observability & Safety
            CapabilityEntry(
                capability_id="secret_redaction",
                name="Pre-Storage & Pre-Telemetry Secret Redaction",
                category="security",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/security/redactor.py"],
                entry_points=["SecretRedactor.redact_secrets()"],
                tests=[
                    "tests/unit/test_security_redactor.py",
                    "tests/unit/test_tracing.py",
                ],
            ),
            CapabilityEntry(
                capability_id="opentelemetry_tracing",
                name="OpenTelemetry-Compatible Distributed Tracing",
                category="observability",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/observability/tracing.py"],
                entry_points=[
                    "start_span",
                    "start_async_span",
                    "API: /api/v1/traces",
                    "Header: X-Trace-ID",
                ],
                tests=["tests/unit/test_tracing.py"],
            ),
            CapabilityEntry(
                capability_id="durable_jobs",
                name="Durable Crash-Consistent Background Jobs",
                category="jobs",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=[
                    "src/cortexforge/jobs/durable.py",
                    "src/cortexforge/jobs/runner.py",
                ],
                entry_points=["DurableJobStore", "JobRunner"],
                tests=["tests/unit/test_durable_jobs.py"],
                limitations="PostgreSQL/SQLite backed with compare-and-swap leases, heartbeats, and checkpoint recovery.",
            ),
            CapabilityEntry(
                capability_id="git_hooks",
                name="Git Integration Hooks (post-commit/merge/checkout)",
                category="jobs",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/apps/cli/main.py"],
                entry_points=["cortex hooks install", "cortex hooks handle"],
                tests=["tests/integration/test_git_hooks.py"],
                limitations="Dispatches non-blocking durable background jobs to avoid developer lag.",
            ),
            CapabilityEntry(
                capability_id="architecture_rules",
                name="Architecture Boundary Rules with Formal Modalities",
                category="architecture",
                status=CapabilityStatus.IMPLEMENTED,
                implementation_files=["src/cortexforge/architecture/invariants.py"],
                entry_points=["ArchitectureInvariantEngine.evaluate_invariants()"],
                tests=[
                    "tests/unit/test_lineage_and_invariants.py::test_architecture_modalities"
                ],
                limitations="Enforces MUST, MUST_NOT, SHOULD, ONLY_IF, and REQUIRES modalities with evidence.",
            ),
            CapabilityEntry(
                capability_id="otlp_network_exporter",
                name="Direct OTLP Network Exporter Daemon",
                category="observability",
                status=CapabilityStatus.DISABLED,
                implementation_files=["src/cortexforge/observability/tracing.py"],
                entry_points=["CORTEX_TELEMETRY=otlp"],
                tests=["tests/unit/test_tracing.py"],
                limitations="In-process tracing buffer and API endpoints are active; remote gRPC/HTTP OTLP exporter daemon is disabled.",
            ),
            CapabilityEntry(
                capability_id="cross_branch_reconciliation",
                name="Automated Cross-Branch Divergence Reconciliation",
                category="memory",
                status=CapabilityStatus.PARTIAL,
                implementation_files=["src/cortexforge/memory/conflict_resolver.py"],
                entry_points=["ConflictResolver"],
                tests=["tests/evaluation/test_temporal_truth.py"],
                limitations="Branch-scoped isolation is fully enforced; automated merge conflict arbitration across diverging branches is manual.",
            ),
        ]

        for entry in entries:
            self.register(entry)

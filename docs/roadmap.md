# CortexForge Development Roadmap

The platform is developed across 15 structured phases to ensure rigorous engineering and verifiable progress without premature complexity:

```mermaid
gantt
    title CortexForge 15-Phase Development Roadmap
    dateFormat  YYYY-MM-DD
    section Core Foundation
    Phase 1 : Repository Scanner & Architecture Slice :done, 2026-09-08, 2d
    Phase 2 : Project Cognitive Model & Graph Schema   :active, 2026-09-10, 2d
    Phase 3 : PostgreSQL Memory Engine & pgvector     : 2026-09-12, 2d
    section Retrieval & Verification
    Phase 4 : Hybrid Multi-Signal Retrieval           : 2026-09-14, 2d
    Phase 5 : Git Change Tracking & Commit Hooks      : 2026-09-16, 2d
    Phase 6 : Semantic Change Propagation             : 2026-09-18, 2d
    Phase 7 : Automated Memory Verification Engine    : 2026-09-20, 2d
    section Lifecycle & Interfaces
    Phase 8 : Memory Consolidation & Archival         : 2026-09-22, 2d
    Phase 9 : Token-Budget Context Composer           : 2026-09-24, 2d
    Phase 10 : Model Context Protocol (MCP) Server     : 2026-09-26, 2d
    Phase 11 : Production CLI (`cortex`)              : 2026-09-28, 2d
    section User Interfaces & Ecosystem
    Phase 12 : Web Dashboard (Next.js & React Flow)   : 2026-09-30, 3d
    Phase 13 : GitHub App & Webhook Ingestion         : 2026-10-03, 2d
    Phase 14 : Benchmark & Evaluation Framework       : 2026-10-05, 2d
    Phase 15 : Production Hardening & Security Audit  : 2026-10-07, 2d
```

### Detailed Phase Breakdown

- **Phase 1: Repository Scanner & Architecture Slice (Current Phase)**
  - Deterministic AST extraction via Tree-sitter for TypeScript, JavaScript, Python, Java, Go.
  - Project registration and persistence of code entities and relationships in relational storage.
  - Project architecture query engine.
  - Initial REST API endpoint and CLI command (`cortex scan`, `cortex architecture`).
  - Unit and integration test suite proving end-to-end vertical slice.
- **Phase 2: Project Cognitive Model & Graph Schema**
  - Full relational graph representation and recursive dependency/dependent queries.
- **Phase 3: PostgreSQL Memory Engine & Vector Embeddings**
  - Layered memory (L0–L6) storage, versioning, evidence linkage, and pgvector search.
- **Phase 4: Hybrid Multi-Signal Retrieval**
  - Fusion of lexical, semantic, graph, recency, and provenance scores.
- **Phase 5: Git Change Tracking**
  - Safe Git CLI abstraction, commit snapshots, diff extraction, and blame tracking.
- **Phase 6: Semantic Change Propagation**
  - Git diff $\to$ affected AST entities $\to$ affected graph nodes $\to$ affected memories.
- **Phase 7: Memory Verification Engine**
  - Automated verification of memories against code entities and test outputs; status transitions.
- **Phase 8: Memory Consolidation Engine**
  - Clustering episodic events into durable knowledge principles with provenance archival.
- **Phase 9: Context Composer**
  - Token-budget-aware structured context formatting (small, medium, large).
- **Phase 10: Model Context Protocol (MCP) Server**
  - MCP stdio/SSE tools and resources implementation.
- **Phase 11: Production CLI (`cortex`)**
  - Full suite of commands: `init`, `scan`, `status`, `architecture`, `memory`, `context`, `graph`, `doctor`, `mcp`, `serve`.
- **Phase 12: Developer Web Dashboard**
  - Next.js, React Flow architecture graph, Memory Explorer, Token Economics.
- **Phase 13: GitHub App & Webhook Ingestion**
  - Webhook handlers for `push`, `pull_request`, and automated incremental scanning.
- **Phase 14: Evaluation & Benchmark Framework**
  - Multi-agent comparison harness (Baseline vs Naive RAG vs Flat vs CortexForge).
- **Phase 15: Production Hardening**
  - OpenTelemetry distributed tracing, rate limiting, RBAC, and container packaging.

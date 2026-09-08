# CortexForge Current-State Engineering Audit

**Date**: 2026-09-09  
**Lead Architect**: Antigravity AI (Google DeepMind Advanced Agentic Coding)  
**Target Repository**: [Kannankaruppaiya/CortexForge](https://github.com/Kannankaruppaiya/CortexForge)  
**Scope**: Full Codebase Audit (`src/`, `apps/web/`, `tests/`, `docs/`, `scripts/`, `Dockerfile`, `docker-compose.yml`, CI/CD, evaluation harness, MCP, REST, CLI)

---

## 1. Executive Summary & Audit Mandate

This audit establishes an objective, unvarnished baseline of the CortexForge repository. It does not trust README claims, marketing copy, benchmark tables, comments, or class names. Every significant capability has been audited against its actual implementation, invocation path, persistence, test coverage, and behavior under real code changes.

### Classification Taxonomy
Every audited subsystem is classified as exactly one of:
- **IMPLEMENTED**: Complete, integrated, persisted, tested, and working end-to-end.
- **PARTIAL**: Core logic exists, but key semantic requirements, edge cases, or integration points are incomplete.
- **STUB**: Interface or skeleton exists with minimal or no operative logic.
- **MOCK**: Implementation returns static or synthetic approximations rather than real execution data.
- **HARDCODED**: Returns fixed constants rather than computing results from underlying evidence.
- **BROKEN**: Implementation exists but fails or raises errors during normal execution.
- **UNUSED**: Code exists in the repository but is never invoked by REST, MCP, CLI, or agent workflows.
- **MISSING**: Required capability does not exist in the codebase.
- **UNPROVEN**: Code executes, but empirical claims (e.g. benchmark numbers, accuracy) cannot be reproduced from stored, verifiable evidence.

---

## 2. Capability Audit Matrix

| Subsystem / Capability | Location | Classification | Summary of Current State |
| :--- | :--- | :--- | :--- |
| **1. Database Schema & Data Model** | `src/cortexforge/core/models.py` | **PARTIAL** | Tables exist for Project, Snapshot, Entity, Relation, Memory, Evidence, Versions, Task, Event. Missing explicit `layer` column (L0-L6 distinct from `memory_type`), `source_commit`, `supersedes_id`, `superseded_by_id`, `conflict_group`, `freshness_score`. Evidence lacks direct `symbol_id` and `ast_fingerprint`. |
| **2. Database Migrations** | Root / `migrations/` | **MISSING** | No Alembic migrations configured. Database relies entirely on `Base.metadata.create_all()` in `db.py`, preventing production schema evolution without data loss. |
| **3. Tree-sitter Multi-Language Parser** | `src/cortexforge/code_intelligence/treesitter/analyzer.py` | **IMPLEMENTED** | Robust AST extraction for Python, JavaScript, TypeScript, TSX, Go, and Java. Accurately extracts classes, functions, methods, parameters, signatures, and call/import relations. |
| **4. AST Semantic Diff Engine** | `src/cortexforge/code_intelligence/` | **MISSING** | No AST diff engine exists. Git diff only detects file-level changes; does not compute symbol additions/removals, signature mutations, or AST fingerprint changes across commits. |
| **5. Repository Scanner & Indexer** | `src/cortexforge/code_intelligence/scanner.py` | **PARTIAL** | Scans repository files, hashes content, and indexes symbols into DB. Incremental mode checks file content hashes, but re-scans entire files rather than applying semantic AST diffs. |
| **6. Git Provider & Change Detection** | `src/cortexforge/code_intelligence/git_provider.py` | **PARTIAL** | Parameterized Git CLI wrapper. Detects file statuses (M, A, D, R) and head commit. Lacks hunk parsing, AST diff integration, and Git hooks. |
| **7. Code Graph & Topology Engine** | `src/cortexforge/graph/service.py` | **IMPLEMENTED** | Relational graph engine in SQLite/PostgreSQL. Traverses upstream callers, downstream dependencies, and synthesizes high-level module architecture. |
| **8. Change Impact & Blast Radius** | `src/cortexforge/code_intelligence/change_propagator.py` | **PARTIAL** | Calculates transitive blast radius across graph. However, memory invalidation is coarse-grained: marks ALL memories associated with a changed file as `STALE`, violating the fine-grained symbol grounding requirement. |
| **9. Memory Lifecycle State Machine** | `src/cortexforge/memory/service.py` | **PARTIAL** | Implements CRUD, version history, and embedding generation. Lacks formal state machine transitions (`CANDIDATE -> UNVERIFIED -> ACTIVE -> STALE -> CONFLICTED -> SUPERSEDED -> INVALIDATED -> ARCHIVED`). |
| **10. Evidence Grounding & Verification** | `src/cortexforge/memory/verification.py` | **PARTIAL** | Verifies file existence and SHA-256 snippet hashes. Lacks semantic AST-level verification (distinguishing physical line shifts from genuine semantic invalidation). |
| **11. Conflict Resolution Engine** | `src/cortexforge/memory/` | **MISSING** | No formal contradiction detection or conflict resolution between contradictory memories. No authority ranking (verified code evidence vs ungrounded natural language statements). |
| **12. Memory Consolidation Engine** | `src/cortexforge/memory/consolidation.py` | **PARTIAL** | Clusters episodic memories and synthesizes durable lessons. However, similarity thresholds are arbitrary (0.18 cosine), LLM output is immediately trusted without a verification gate, and source provenance is imperfectly preserved. |
| **13. Hybrid Retrieval Engine** | `src/cortexforge/retrieval/engine.py` | **PARTIAL** | Fuses vector similarity, lexical overlap, graph proximity, and recency. However, loads all memories/entities into Python memory (O(N) scan). Lacks database-level PostgreSQL FTS / BM25 and native vector index querying. |
| **14. Production pgvector Integration** | `src/cortexforge/core/models.py`, `db.py` | **MISSING** | In PostgreSQL mode, embeddings are stored as JSON and computed via Python cosine loops. Does not use pgvector's native `Vector` column or HNSW/IVFFlat indexes. |
| **15. Context Composer** | `src/cortexforge/retrieval/composer.py` | **IMPLEMENTED** | Assembles structured markdown packets with token-budget awareness (`small`, `medium`, `large`). Correctly prioritizes architectural decisions, constraints, and failures. |
| **16. Agent Task & Event Ingestion** | `models.py`, `mcp/server.py` | **PARTIAL** | `AgentTask` and `AgentEvent` database tables exist and MCP has basic event recording tools. However, there is no automated orchestration loop connecting file changes, test runs, and memory updates into a cohesive workflow. |
| **17. MCP Server Integration** | `src/cortexforge/apps/mcp/server.py` | **IMPLEMENTED** | Thin integration layer exposing project architecture, component details, context composition, memory search, memory creation, and impact analysis over stdio. Calls domain services directly. |
| **18. REST API Gateway** | `src/cortexforge/apps/api/` | **IMPLEMENTED** | FastAPI application with comprehensive routes for projects, scanning, graph, memories, retrieval, token economics, and webhooks. |
| **19. Command Line Interface (CLI)** | `src/cortexforge/apps/cli/main.py` | **IMPLEMENTED** | Rich CLI (`cortex init`, `scan`, `status`, `context`, `search`, `remember`, `decisions`, `failures`, `verify`, `consolidate`, `impact`, `eval`, `serve`). |
| **20. Security & Secret Redaction** | `src/cortexforge/security/redactor.py` | **IMPLEMENTED** | Sanitizes user/agent text before memory persistence, stripping OpenAI keys, AWS tokens, JWTs, generic secrets, and neutralizing prompt injections. |
| **21. LLM & Embedding Providers** | `src/cortexforge/llm/`, `embeddings/` | **PARTIAL** | Abstractions exist for local deterministic and OpenAI providers. However, production error handling must never silently degrade to mock responses without alerting the user. |
| **22. Frontend Web Dashboard** | `apps/web/` | **PARTIAL** | React 19 + TypeScript + Vite dashboard with 8 active components. Displays live backend data for overview, architecture, memories, economics, and impact. Needs updates for explicit L0-L6 layers and conflict views. |
| **23. Evaluation Harness & Benchmarks** | `src/cortexforge/evaluation/runner.py` | **UNPROVEN** | Evaluator runs empirical test tasks, but only compares 4 hardcoded modes. Does not support 7-way ablation. README claims (91.8% token reduction, 96% success rate) are unverified against real long-running tasks. |
| **24. Docker & CI/CD** | `Dockerfile`, `docker-compose.yml`, `.github/` | **IMPLEMENTED** | Multi-stage Docker build packaging backend and pre-built frontend SPA. Compose config includes PostgreSQL (pgvector) and Redis. GitHub Actions CI runs tests and linting. |

---

## 3. Detailed Execution Traces for Core Capabilities

### 3.1 Code Intelligence & Scanner Pipeline
1. **Where is it implemented?**: `src/cortexforge/code_intelligence/scanner.py` (`RepositoryScanner.scan_project`), `treesitter/analyzer.py` (`TreeSitterProvider.parse_source`).
2. **Is it actually called?**: Yes, called via CLI `cortex scan`, REST `POST /api/v1/projects/{id}/scan`, and MCP auto-project initialization.
3. **Is the output used downstream?**: Yes. Extracted `CodeEntity` and `Relationship` instances are stored in the database and queried by `GraphService`, `HybridRetrievalEngine`, and `ContextComposer`.
4. **Is it persisted?**: Yes, persisted in SQLite / PostgreSQL tables `code_entities` and `relationships`.
5. **Is it tested?**: Yes, tested in `tests/unit/test_parser.py` and `tests/integration/test_scanner_and_graph.py`.
6. **Does it work after a real repository change?**: Partially. Incremental scanning re-reads files whose content hash changed, but rescans the entire file rather than performing an AST semantic diff.
7. **Is it deterministic/reproducible?**: Yes, Tree-sitter AST parsing is strictly deterministic given the same file contents.
8. **Does it fail safely?**: Yes, skips unsupported or binary files, ignores `.gitignore` patterns, and captures syntax errors without aborting the scan.

### 3.2 Change Propagation & Memory Invalidation
1. **Where is it implemented?**: `src/cortexforge/code_intelligence/change_propagator.py` (`SemanticChangePropagator.propagate_changes`).
2. **Is it actually called?**: Yes, called via CLI `cortex impact`, REST `POST /api/v1/projects/{id}/impact`, and MCP `change_get_impact`.
3. **Is the output used downstream?**: Yes, flags affected memories as `STALE` and produces blast radius warnings for callers.
4. **Is it persisted?**: Yes, updates `Memory.status = 'STALE'` in the database when `mark_stale=True`.
5. **Is it tested?**: Yes, tested in `tests/integration/test_memory_lifecycle_and_propagation.py`.
6. **Does it work after a real repository change?**: **Flawed execution**. If `auth.py` has 5 functions and 1 function is modified, the propagator marks ALL memories referencing `auth.py` as `STALE`, regardless of which symbol was touched. This violates the core invariant in Section 11 of the specification.
7. **Is it deterministic/reproducible?**: Yes.
8. **Does it fail safely?**: Yes, rolls back session on DB failure.

### 3.3 Memory Grounding & Verification Engine
1. **Where is it implemented?**: `src/cortexforge/memory/verification.py` (`MemoryVerificationEngine.verify_project_memories`).
2. **Is it actually called?**: Yes, called via CLI `cortex memory verify`, REST `POST /api/v1/memories/verify`, and integration test suites.
3. **Is the output used downstream?**: Yes, updates `Memory.status` to `ACTIVE` or `STALE` and increments confidence.
4. **Is it persisted?**: Yes, commits status changes and updates `last_verified_at` timestamp.
5. **Is it tested?**: Yes, tested in `tests/integration/test_memory_lifecycle_and_propagation.py`.
6. **Does it work after a real repository change?**: Partially. Accurately flags missing files and modified line snippets. However, it relies on exact line ranges and SHA-256 snippet hashes; inserting comments above a function alters line numbers, causing false staleness even when the function's AST is unchanged.
7. **Is it deterministic/reproducible?**: Yes.
8. **Does it fail safely?**: Yes, handles missing files and read errors gracefully.

### 3.4 Hybrid Retrieval & Context Composition
1. **Where is it implemented?**: `src/cortexforge/retrieval/engine.py` (`HybridRetrievalEngine.retrieve`) and `retrieval/composer.py` (`ContextComposer.build_context`).
2. **Is it actually called?**: Yes, called via CLI `cortex context`, REST `POST /api/v1/retrieval/query`, and MCP `project_get_context`.
3. **Is the output used downstream?**: Yes, formats the primary context block injected into AI coding agent system prompts.
4. **Is it persisted?**: Retrieval results are transient; task execution events are logged to `agent_events`.
5. **Is it tested?**: Yes, tested in `tests/integration/test_memory_lifecycle_and_propagation.py` and `tests/integration/test_github_and_retrieval.py`.
6. **Does it work after a real repository change?**: Yes, incorporates updated memory statuses and penalizes stale memories by 40% (`p_stale = 0.40`).
7. **Is it deterministic/reproducible?**: Yes, deterministic vector embedding and scoring.
8. **Does it fail safely?**: Yes, returns empty list or fallback context on empty database.

### 3.5 Memory Consolidation & Synthesis
1. **Where is it implemented?**: `src/cortexforge/memory/consolidation.py` (`MemoryConsolidationEngine.consolidate_project`).
2. **Is it actually called?**: Yes, via CLI `cortex memory consolidate`, MCP, and REST API.
3. **Is the output used downstream?**: Creates new durable memories of type `LESSON` or `CONSTRAINT`.
4. **Is it persisted?**: Yes, creates records in `memories` and deprecates source episodic memories.
5. **Is it tested?**: Yes, tested in `tests/integration/test_memory_lifecycle_and_propagation.py`.
6. **Does it work after a real repository change?**: Weakness identified. It clusters memories with an overly permissive similarity threshold (0.18 cosine / 0.15 lexical overlap), and immediately persists the LLM output with status `ACTIVE` without a formal verification or confidence threshold gate.
7. **Is it deterministic/reproducible?**: Deterministic under local mock/embedding provider; non-deterministic if connected to live external LLMs.
8. **Does it fail safely?**: Yes, handles empty clusters gracefully.

### 3.6 Evaluation Harness & Empirical Benchmark Claims
1. **Where is it implemented?**: `src/cortexforge/evaluation/runner.py` (`EvaluationRunner.run_benchmark`).
2. **Is it actually called?**: Yes, called via CLI `cortex benchmark` and REST `POST /api/v1/projects/{id}/benchmark`.
3. **Is the output used downstream?**: Displayed in CLI tables and returned to dashboard Evaluation Harness.
4. **Is it persisted?**: **Not persisted**. Results are computed ephemerally in-memory and not stored in reproducible benchmark run files.
5. **Is it tested?**: Yes, tested in `tests/integration/test_evaluation_runner.py`.
6. **Does it work after a real repository change?**: Dynamic calculations adapt to real codebase entities and token metrics.
7. **Is it deterministic/reproducible?**: Partially. While the execution runs real queries, the README benchmark table claims:
   - 91.8% reduction in exploratory files read
   - 88.4% reduction in context tokens
   - 100% prevention of repeated failures
   - 96% task success rate
   These figures cannot be reproduced from any saved test dataset or raw evaluation artifacts in the repository. They must be categorized as **UNVERIFIED**.
8. **Does it fail safely?**: Yes.

---

## 4. Architectural Gaps & Implementation Roadmap

Based on this comprehensive audit, the following critical technical gaps must be resolved in order:

1. **Phase 1: Memory Data Model Evolution & State Machine**
   - Separate `layer` (L0–L6) as an explicit discrete field from `memory_type` (DECISION, FAILURE, LESSON, etc.).
   - Add `source_commit`, `supersedes_id`, `superseded_by_id`, `conflict_group`, `freshness_score` to `Memory`.
   - Add direct foreign key `symbol_id` (pointing to `CodeEntity`) and `ast_fingerprint` to `MemoryEvidence`.
   - Implement strict state machine transitions (`CANDIDATE -> UNVERIFIED -> ACTIVE -> STALE -> CONFLICTED -> SUPERSEDED -> INVALIDATED -> ARCHIVED`).
   - Setup Alembic database migrations.

2. **Phase 2 & 3: True Incremental Code Intelligence & AST Semantic Diff**
   - Enhance `GitProvider` to yield commit diff hunks and changed line ranges.
   - Implement `ASTSemanticDiffer` in `treesitter/` comparing AST trees across commit SHAs to detect symbol additions, removals, renames, signature changes, and body modifications.

3. **Phase 4 & 5: Fine-Grained Provenance & Symbol-Level Invalidation**
   - Update `SemanticChangePropagator` to invalidate memories only when their specific referenced symbols/AST nodes are modified, eliminating coarse file-level false staleness.

4. **Phase 6: Conflict Resolution Engine**
   - Implement contradiction detection comparing code evidence authority vs ungrounded natural language statements, setting `CONFLICTED` or `SUPERSEDED`.

5. **Phase 7: Real Hybrid Retrieval & pgvector Integration**
   - Implement native pgvector `Vector` column and index in PostgreSQL with local SQLite fallback.

6. **Phase 16: Research Benchmark Suite & Ablation Studies**
   - Expand `EvaluationRunner` to support 7-way ablation (No memory, Naive RAG, Flat memory, CortexForge retrieval-only, +provenance, +change propagation, Full CortexForge) storing raw reproducible JSON run logs.
   - Update README to label all unverified benchmark metrics accurately.

---

*Audit completed and certified by Lead Architect.*

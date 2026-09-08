# CortexForge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP: 2.x](https://img.shields.io/badge/MCP-2.x_Compliant-emerald.svg)](https://modelcontextprotocol.io)
[![FastAPI](https://img.shields.io/badge/FastAPI-Production-green.svg)](https://fastapi.tiangolo.com)
[![TypeScript: 5.7+](https://img.shields.io/badge/TypeScript-5.7+-blue.svg)](https://www.typescriptlang.org/)
[![Vite: 6.x](https://img.shields.io/badge/Vite-6.x-purple.svg)](https://vitejs.dev/)

> **A continuously evolving, verified project cognition system for AI coding agents.**

---

## ⚡ What is CortexForge?

AI coding agents generate code efficiently, but across long-running software projects they repeatedly rediscover repository structure, re-investigate architectural decisions, violate established conventions, and re-attempt solutions that previously failed.

**CortexForge is NOT a simple vector database.**  
**It is NOT a chat-history store.**  
**It is NOT ordinary text-chunk RAG.**

CortexForge maintains an evidence-grounded, provenance-aware, conflict-aware **Project Cognitive Model** that allows AI coding agents to recover accumulated understanding instantly without re-reading the entire repository.

---

## 🔄 The 20-Step Core Continuous Cognitive Loop

CortexForge guarantees this strict closed-loop execution lifecycle:

```text
  1. AGENT TASK
     ↓
  2. PROJECT IDENTIFICATION
     ↓
  3. CONTEXT RETRIEVAL (Token-budgeted, layer-partitioned L0-L6)
     ↓
  4. AGENT WORK (Tool calls, edits, searches)
     ↓
  5. FILE/TOOL EVENTS (Canonical event normalization)
     ↓
  6. GIT DIFF (Commit-driven or working-tree delta)
     ↓
  7. SEMANTIC AST DIFF (Tree-sitter symbol diffing)
     ↓
  8. SYMBOL CHANGES (Added, removed, modified, moved, signature-changed)
     ↓
  9. GRAPH CHANGES (Adjacency edge updates, blast radius evaluation)
     ↓
 10. AFFECTED MEMORIES (Identified via symbol and file evidence linkage)
     ↓
 11. EVIDENCE VALIDATION (AST fingerprints, signatures, existence checks)
     ↓
 12. MEMORY RESOLUTION (KEEP / REANCHOR / REVISE / STALE / CONFLICT / SUPERSEDE / INVALIDATE)
     ↓
 13. TEST EXECUTION (First-class TestRun and TestCaseResult tracking)
     ↓
 14. FAILURE / SUCCESS LEARNING (Normalized failure signatures & fix tracking)
     ↓
 15. SEMANTIC VERIFICATION (Multi-tier policy verification)
     ↓
 16. SAFE CONSOLIDATION (Idempotent clustering into candidate lessons)
     ↓
 17. PROVENANCE RECORDING (Full audit chain: why CortexForge believes this)
     ↓
 18. UPDATED COGNITIVE STATE (Generation bump and state snapshot)
     ↓
 19. NEXT TASK RECEIVES CORRECT, MINIMAL, RELEVANT CONTEXT
     ↓
 20. REPEAT WITH MEASURABLE REPO REDISCOVERY REDUCTION
```

---

## 🧠 The 7-Layer Cognitive Model (L0 – L6)

CortexForge stratifies project understanding into seven distinct memory tiers, each with dedicated verification criteria and eviction policies:

| Layer | Type | Description | Verification Grounding |
| :--- | :--- | :--- | :--- |
| **L0** | **Project Identity** | Repository purpose, core tech stack, runtime dependencies, branch model. | Verified against `pyproject.toml`, `package.json`, Git branch. |
| **L1** | **Structural Architecture** | Modules, packages, public symbols, call graph, and dependency topologies. | Extracted via multi-language Tree-sitter AST parsers. |
| **L2** | **Conventions & Patterns** | Naming standards, error handling patterns, layering rules, idioms. | Synthesized from repeated codebase patterns and linters. |
| **L3** | **Decisions & Rationale (ADRs)** | Explicit design choices, trade-offs, consequences, and invariants. | Grounded in code references and git commit signatures. |
| **L4** | **Known Failures & Post-Mortems** | Bugs, flaky tests, edge cases, rejected fixes, and anti-patterns. | Grounded in git diffs, normalized error signatures, and test runs. |
| **L5** | **Durable Lessons** | Hierarchically consolidated rules synthesized from episodic failures. | Clustered and verified across multiple independent episodes. |
| **L6** | **Active Working State** | Current agent task, prospective diffs, and blast radius simulations. | Bound to active execution session and task context. |

---

## 🚀 Quickstart

### 1. Installation

```bash
# Clone repository
git clone https://github.com/Kannankaruppaiya/CortexForge.git
cd CortexForge

# Set up Python virtual environment (Python 3.12+)
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\activate

# Install with development dependencies
pip install -e ".[all]"
```

### 2. Basic Workflow

```bash
# Run diagnostics
cortex doctor

# Index local repository with incremental Tree-sitter AST scanner
cortex scan .

# Clean state recovery and full index rebuild (if index corrupted or out of sync)
cortex rebuild .

# Inspect synthesized architecture map
cortex architecture

# View and manage project memories
cortex memory list
cortex memory verify
cortex memory consolidate

# Generate structured, token-budget context for an agent task
cortex context "Fix refresh token rotation race condition" --profile medium

# Launch Developer Web Dashboard and REST API
cortex serve --port 8000
```

Open `http://localhost:8000` in your browser to view the interactive Developer Web Dashboard.

---

## 🔌 Model Context Protocol (MCP 2.x) Integration

CortexForge exposes a standards-compliant MCP 2.x server over `stdio`, ready to plug into Claude Desktop, Cursor, Gemini Antigravity, or any MCP-compatible agent.

### Claude Desktop Configuration (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cortexforge": {
      "command": "cortex",
      "args": ["mcp"]
    }
  }
}
```

### Available MCP Tools

- `project_get_context(task_prompt, profile, target_files)`: Returns budget-optimized structured context packet (L0–L6) with `why_selected` annotations.
- `project_get_architecture(depth)`: Returns synthesized module and dependency map.
- `project_get_component(symbol_name)`: Returns symbol AST signature, upstream callers, and downstream dependencies.
- `architecture_check_rules()`: Checks architecture boundaries and returns violations with evidence.
- `memory_search(query, memory_type, limit)`: Hybrid semantic + lexical retrieval with MMR reranking.
- `memory_create(memory_type, title, content, summary, importance, evidence)`: Stores durable, evidence-grounded memory.
- `memory_get_provenance(memory_id)`: Traces the causal chain: Memory → Evidence → Symbol → File → Commit → Tests.
- `memory_get_decisions()`: Retrieves active Architectural Decision Records (ADRs).
- `memory_get_failures()`: Retrieves known failure post-mortems and durable lessons.
- `change_get_impact(modified_files, mark_stale)`: Simulates blast radius and constraint violations before editing files.
- `project_take_snapshot(message)`: Creates a formal Cognitive Snapshot preserving state at the current commit.
- `task_find_similar(task_prompt, limit)`: Finds historically similar engineering tasks, approaches, and fixes.

---

## 🌐 Developer Web Dashboard

The built-in React 19 + TypeScript + Vite dashboard provides real-time visualization of the project cognitive model:

1. **Overview**: Live stats, cognitive loop health, indexed commits, and quick actions.
2. **Architecture Map**: Interactive module cards, symbol hierarchies, call graphs, and blast radius inspection.
3. **Architecture Invariants**: Real-time rule enforcement and boundary violation alerts with evidence.
4. **Memory Explorer**: Search, filter by layer and status, inspect version diffs, and launch **Provenance Tracing**.
5. **Provenance Modal ("Why does CortexForge believe this?")**: Interactive inspection of evidentiary files, AST symbols, commits, and version history.
6. **Decisions & Failures**: Split-view ADR tracker and normalized bug signature post-mortems with rejected approaches.
7. **Change Impact Simulator**: Pre-action blast radius calculator showing affected callers and stale memory warnings.
8. **Cognitive Snapshots & Replay**: Historical state inspection and deterministic replay at any commit SHA.
9. **Evaluation Harness**: 7-way comparative agent benchmark and repository mutation test suite.

To build production static assets (automatically served by `cortex serve`):

```bash
cd apps/web
npm install
npm run build
```

---

## 🔬 First-Class Domain Model & Schema

CortexForge promotes all cognitive concepts to first-class relational SQLAlchemy 2.0 entities managed via Alembic:

- `Project` & `RepositorySnapshot`
- `Commit`, `ChangeSet`, `FileChange`, `SymbolChange`
- `CodeEntity`, `Relationship`
- `Memory`, `MemoryEvidence`, `MemoryRelation`, `MemoryVersion`
- `AgentTask`, `AgentEvent`
- `TestRun`, `TestCaseResult`
- `FailureEpisode`, `FixAttempt`
- `ArchitectureRule`, `RuleViolation`
- `CognitiveSnapshot`

---

## 📊 Empirical Evaluation & Benchmark Suite

The evaluation harness executes reproducible, empirical comparison across 7 agent configurations:

- **Mode A (No Memory)**: Zero context; full blind exploratory code reading.
- **Mode B (Naive Vector RAG)**: Arbitrary 500-token chunk retrieval without AST grounding or verification.
- **Mode C (Flat Conversational Memory)**: Concatenated recent chat summaries without layers or structure.
- **Mode D (CortexForge Retrieval Only)**: BM25 + Vector hybrid retrieval without graph propagation.
- **Mode E (CortexForge + Provenance)**: Hybrid retrieval with evidence hashes, source commits, and symbol IDs.
- **Mode F (CortexForge + Change Propagation)**: Mode E + Tree-sitter semantic AST diffs and symbol-level invalidation.
- **Mode G (Full CortexForge)**: Mode F + conflict resolution (polarity arbitration) + safe consolidation + token-budgeted context.

### Repository Mutation Benchmarks
Deterministic mutation testing (`RENAME_SYMBOL`, `CHANGE_SIGNATURE`, `REMOVE_DEPENDENCY`, `CHANGE_BEHAVIOUR`) evaluates cognitive engine reaction accuracy:
```bash
cortex eval mutations
```

> **⚠️ ZERO FABRICATED METRICS POLICY**: CortexForge strictly forbids hardcoded or synthetic benchmark figures in documentation or dashboards. All reported metrics are generated via live test execution.

---

## 🔒 Security & Privacy Guarantees

- **Zero Cloud Leakage by Default**: Local SQLite + `FastDeterministicEmbeddingProvider` (zero external network egress required).
- **Strict Provider Safety**: Missing API keys in production mode raise explicit errors; no silent mock fallback.
- **Secret Redaction**: Automatic redaction of OpenAI/AWS keys, JWTs, and database credentials before storage or egress.
- **Prompt Injection Defense**: Untrusted repository comments and markdown are bounded to `UNTRUSTED_REPOSITORY_TEXT` and cannot override control policies.
- **Path Traversal Protection**: All filesystem accesses are strictly confined to project root boundaries.

---

## 🧪 Testing

```bash
# Unit, integration, contract, property and evaluation suites
pytest -v

# Only the property-based invariants (idempotency, isolation, authority ordering)
pytest tests/property -v

# Only the adversarial and temporal benchmarks
pytest tests/evaluation -v

# The schema must be buildable from migrations alone, as it is on a fresh deploy
pytest tests/contract -v

# Lint
ruff check src tests
```

The suite is deliberately not described by a fixed count here: a number in prose
goes stale the moment a test is added, and `cortex integrity` will report it when
it does.

---

## 📜 License

Apache-2.0, as declared in `pyproject.toml`.

> **Note:** the repository does not yet contain a `LICENSE` file. `cortex integrity`
> reports this, because a declared license that is not distributed with the code
> is a claim the repository cannot back up.

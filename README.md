# CortexForge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP: 2.x](https://img.shields.io/badge/MCP-2.x_Compliant-emerald.svg)](https://modelcontextprotocol.io)
[![FastAPI](https://img.shields.io/badge/FastAPI-Production-green.svg)](https://fastapi.tiangolo.com)
[![TypeScript: 5.7+](https://img.shields.io/badge/TypeScript-5.7+-blue.svg)](https://www.typescriptlang.org/)
[![Vite: 6.x](https://img.shields.io/badge/Vite-6.x-purple.svg)](https://vitejs.dev/)

> **A continuously evolving, verified project memory layer for AI coding agents.**

---

## ⚡ What is CortexForge?

AI coding agents generate code efficiently, but across long-running software projects they repeatedly rediscover repository structure, re-investigate architectural decisions, violate established conventions, and re-attempt solutions that previously failed.

**CortexForge is NOT a simple vector database.**  
**It is NOT a chat-history store.**  
**It is NOT ordinary text-chunk RAG.**

CortexForge maintains an evidence-grounded, provenance-aware, conflict-aware **Project Cognitive Model** that allows AI coding agents to recover accumulated understanding instantly without re-reading the entire repository.

```
                  ┌─────────────────────────────────────────────────────────┐
                  ▼                                                         │
            ┌───────────┐         ┌────────────┐         ┌───────────┐      │
            │  OBSERVE  │ ──────> │ UNDERSTAND │ ──────> │   STORE   │      │
            └───────────┘         └────────────┘         └───────────┘      │
                                                               │            │
                                                               ▼            │
            ┌───────────┐         ┌────────────┐         ┌───────────┐      │
            │    ACT    │ <────── │  RETRIEVE  │ <────── │  VERIFY   │      │
            └───────────┘         └────────────┘         └───────────┘      │
                  │                                            │            │
                  │                                            ▼            │
                  │                                      ┌───────────┐      │
                  │                                      │CONSOLIDATE│ ─────┘
                  │                                      └───────────┘
                  └─────────────────────────────────────────────────────────┘
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
| **L4** | **Known Failures & Post-Mortems** | Bugs, flaky tests, edge cases, rejected fixes, and anti-patterns. | Grounded in git diffs and issue/test failure records. |
| **L5** | **Durable Lessons** | Hierarchically consolidated rules synthesized from episodic failures. | Clustered and verified across multiple episodes. |
| **L6** | **Active Working State** | Current agent task, prospective diffs, and blast radius simulations. | Bound to active execution session and task context. |

---

## 🚀 Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/your-org/CortexForge.git
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

### Cursor Configuration (`.cursor/mcp.json`)

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

### Available MCP Tools & Resources

- `project_get_context(task_prompt, profile, target_files)`: Returns budget-optimized structured context packet (L0–L5).
- `project_get_architecture(depth)`: Returns synthesized module and dependency map.
- `project_get_component(symbol_name)`: Returns symbol AST signature, upstream callers, and downstream dependencies.
- `memory_search(query, memory_type, limit)`: Hybrid semantic + lexical retrieval with MMR reranking.
- `memory_create(memory_type, title, content, summary, importance, evidence)`: Stores durable, evidence-grounded memory.
- `memory_get_decisions()`: Retrieves active Architectural Decision Records (ADRs).
- `memory_get_failures()`: Retrieves known failure post-mortems and durable lessons.
- `change_get_impact(modified_files, mark_stale)`: Simulates blast radius and constraint violations before editing files.

---

## 🌐 Developer Web Dashboard

The built-in React + TypeScript dashboard provides real-time visualization of the project cognitive model:

1. **Overview**: Live stats, cognitive loop health, indexed commits, and quick actions.
2. **Architecture Map**: Interactive module cards, symbol hierarchies, call graphs, and blast radius inspection.
3. **Memory Explorer**: Search, filter by cognitive layer, verify evidence grounding against the live working tree, and audit version history.
4. **Decisions & Failures**: Split-view ADR tracker and episodic bug post-mortem immune system.
5. **Change Impact Simulator**: Pre-action blast radius calculator showing affected callers and stale memory warnings.
6. **Token Economics Scoreboard**: Context efficiency visualizer with budget breakdown simulator.
7. **Evaluation Harness**: 4-way comparative agent benchmark dashboard.

To run the web dashboard in standalone development mode:

```bash
cd apps/web
npm install
npm run dev
```

To build production static assets (automatically mounted by `cortex serve`):

```bash
cd apps/web
npm run build
```

---

## 🐳 Docker Stack Deployment

For team deployments with PostgreSQL (`pgvector`) and Redis:

```bash
# Start full stack (API, PostgreSQL with pgvector, Redis)
docker compose up -d

# Verify services
docker compose ps
curl http://localhost:8000/health
```

---

## 📊 Empirical Evaluation & Benchmark Suite

The research evaluation harness executes reproducible, empirical comparison across 7 agent configurations:

- **Mode A (No Memory)**: Zero context; full blind exploratory code reading.
- **Mode B (Naive Vector RAG)**: Arbitrary 500-token chunk retrieval without AST grounding or verification.
- **Mode C (Flat Conversational Memory)**: Concatenated recent chat summaries without layers or structure.
- **Mode D (CortexForge Retrieval Only)**: BM25 + Vector hybrid retrieval without graph propagation.
- **Mode E (CortexForge + Provenance)**: Hybrid retrieval with evidence hashes, source commits, and symbol IDs.
- **Mode F (CortexForge + Change Propagation)**: Mode E + Tree-sitter semantic AST diffs and symbol-level invalidation.
- **Mode G (Full CortexForge)**: Mode F + conflict resolution (polarity arbitration) + safe consolidation + token-budgeted context.

### Controlled Ablation Studies
Run controlled ablation runs to isolate the impact of individual cognitive components:
`cortex benchmark --ablation [without_graph | without_provenance | without_freshness | without_failures | without_change_propagation | without_consolidation | without_semantic_ast_diff]`

> **⚠️ NOTE: UNVERIFIED CLAIM NOTICE**  
> Any preliminary target metric that cannot be directly reproduced from a live benchmark run is classified as **UNVERIFIED**. CortexForge never reports hardcoded or synthetic results as established fact. Run `cortex benchmark` locally to output timestamped JSON logs in `benchmarks/results/`.

```bash
cortex benchmark .
```


---

## 🔒 Local-First Privacy Guarantees

- **Zero Cloud Leakage by Default**: CortexForge defaults to local SQLite + `FastDeterministicEmbeddingProvider` (zero external API calls required).
- **Deterministic Offline Clustering**: Summaries and lesson consolidation execute locally without requiring third-party LLM endpoints.
- **Opt-in Cloud Accelerators**: OpenAI (`text-embedding-3-small`, `gpt-4o-mini`) and PostgreSQL `pgvector` can be enabled optionally via `.env`.

---

## 🧪 Testing

```bash
# Run all unit and integration tests
pytest -v

# Run code style & type checks
ruff check src tests
```

---

## 📜 License

MIT License. Designed and built with production rigor for the open-source developer tooling ecosystem.

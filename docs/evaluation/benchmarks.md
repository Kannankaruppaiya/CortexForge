# CortexForge Evaluation Framework & Benchmark Methodology

## Evaluation Philosophy: Empirical Grounding

As mandated by Section 2 and Section 25, no claim of "memory improvement" or "token reduction" can be made without empirical measurement. The evaluation framework is built directly into CortexForge to continuously test our primary hypothesis:

> **Primary Hypothesis:** *"An AI coding agent can reduce redundant repository exploration and preserve coding-task accuracy when it has access to a continuously maintained, provenance-aware and verified project model."*

## Comparative Configurations

Every benchmark task is run across 4 distinct agent modes:
1. **Baseline Agent (Zero Memory / Fresh Session)**: Standard agent given repo root and bash/file search tools (e.g., `grep`, `find`, reading files from scratch).
2. **Naive Vector RAG Agent**: Agent equipped with standard vector-based semantic search over 500-token text chunks.
3. **Flat Memory Agent**: Agent with flat conversational key-value memory (no graph, no provenance, no verification).
4. **CortexForge Agent (Full Cognitive Model)**: Hybrid graph + semantic retrieval, layered memory (L0–L6), pre-action governance, and verified provenance.

## Tracked Metrics

| Metric | Target / Unit | Definition |
|---|---|---|
| **Task Success Rate** | % of passing unit tests | Did the agent produce code that passed all task verification tests without regressions? |
| **Exploration Reduction** | % fewer files read | $\frac{\text{Files}_{\text{baseline}} - \text{Files}_{\text{cortex}}}{\text{Files}_{\text{baseline}}} \times 100$ |
| **Token Reduction** | % fewer prompt tokens | Total input + output tokens consumed during task execution. |
| **Tool Call Reduction** | % fewer calls | Redundant directory listings and greps avoided. |
| **Stale Memory Error Rate** | Count per 100 tasks | Errors caused by the agent acting on outdated code patterns. |
| **Repeated Failure Rate** | Count per 100 tasks | Instances where the agent attempted a solution previously documented as failing. |
| **Retrieval Precision & Recall** | Mean Average Precision (MAP) | Overlap of retrieved entities with actual ground-truth modified files. |
| **Context Compression Ratio** | Ratio | $\frac{\text{Raw Episodic Trajectory Tokens}}{\text{Consolidated Memory Tokens}}$ |

## Benchmark Suites

1. **Bug Fix & Regression Suite**: 20 realistic multi-file bugs where incorrect solutions have known anti-patterns.
2. **Refactoring & Architectural Migration Suite**: Tasks touching upstream interfaces that have downstream callers and invariant constraints.
3. **Continuous Evolution Suite**: Synthetic git commit streams where APIs are changed/deprecated, measuring whether the agent detects stale patterns without re-reading the entire repository.

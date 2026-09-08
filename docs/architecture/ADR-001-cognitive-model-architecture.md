# ADR 001: Project Cognitive Model Architecture for AI Coding Agents

## Status
Accepted

## Context
AI coding agents generate large volumes of code and make architectural decisions, but across long-running projects they suffer from amnesia:
- They repeatedly rediscover repository structure, directory layouts, and dependency graphs.
- They repeat previously attempted and failed approaches.
- They violate established architectural invariants and implicit project constraints.
- They lack awareness of changes across git commits, leading to actions based on stale knowledge.

Existing solutions in industry fall into three inadequate buckets:
1. **Chat History Stores / Session Buffers**: Ephemeral, token-inefficient, unstructured, and noisy.
2. **Naive Vector RAG**: Treats code snippets as unstructured text chunks; lacks understanding of call hierarchies, AST scopes, dependency directions, or operational constraints. Suffers from chunk boundary errors and semantic drift.
3. **Static Code Graphs (Code Property Graphs)**: Good at syntax relations, but completely blind to decisions, rationale, historical failures, human intent, and runtime constraints.

## Decision
We establish **CortexForge**: an evolving, verified Project Cognitive Model.
The core loop is:
$$\text{OBSERVE} \to \text{UNDERSTAND} \to \text{STORE} \to \text{VERIFY} \to \text{CONSOLIDATE} \to \text{RETRIEVE} \to \text{ACT} \to \text{OBSERVE AGAIN}$$

### Key Architectural Pillars:
1. **Layered Memory (L0–L6)**: Strict separation of Working, Structural, Decision, Episodic, Knowledge, and Skill layers.
2. **Relational Graph System of Record**: PostgreSQL with relational graph tables, recursive CTEs, and pgvector embeddings.
3. **Deterministic AST Parsing**: Tree-sitter powered incremental symbol, dependency, and hierarchy extraction before any LLM involvement.
4. **Semantic Change Propagation**: Invalidation and verification driven by Git diffs mapped to affected graph nodes and linked memories.
5. **Provenance & Verification**: Every memory retains evidence links (source, commit SHA, file, line range, hash) and transitions through explicit verification states (`ACTIVE`, `STALE`, `CONFLICTED`, `DEPRECATED`, `UNVERIFIED`, `ARCHIVED`).
6. **Agent Governance**: Memory pre-action checks intercepting high-risk modifications with known failure histories and constraints.
7. **Model Context Protocol (MCP)**: Standards-compliant tool and resource exposure to Claude, Codex, Gemini, and open-source agents.

## Consequences
- **Positive**: Agents avoid re-exploring thousands of files, reduce token consumption by >40%, avoid repeating known bugs, and maintain architectural cohesion.
- **Negative**: Requires background workers for continuous verification and consolidation; requires disciplined git integration.

# CortexForge

**A continuously evolving, verified project memory layer for AI coding agents.**

CortexForge maintains a continuously updated, provenance-aware, conflict-aware Project Cognitive Model that allows AI coding agents to recover previously accumulated project understanding without rereading the entire repository.

## The Continuous Cognitive Loop

$$\text{OBSERVE} \longrightarrow \text{UNDERSTAND} \longrightarrow \text{STORE} \longrightarrow \text{VERIFY} \longrightarrow \text{CONSOLIDATE} \longrightarrow \text{RETRIEVE} \longrightarrow \text{ACT} \longrightarrow \text{OBSERVE AGAIN}$$

## Quickstart (Phase 1)

```bash
# Scan repository and extract AST code entities
cortex scan .

# View synthesized project architecture
cortex architecture

# Launch MCP Server for AI agents (Claude, Cursor, Gemini)
cortex mcp

# Launch REST API server
cortex serve --port 8000
```

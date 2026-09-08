# CortexForge Model Context Protocol (MCP) Tool Specification

## Overview
CortexForge exposes a standard MCP Server (over stdio and SSE) allowing agents such as Claude, Codex, Gemini, and Cursor to directly interact with the Project Cognitive Model without manual copy-pasting.

## Tools Exposed

### 1. `project_get_context`
- **Description**: Returns structured, token-budget-aware project context for a planned task. Includes architecture, relevant decisions, constraints, previous failures, and warnings.
- **Input Schema**:
  - `task_text` (string, required): The prompt or issue description the agent is solving.
  - `profile` (string, optional, enum: `small`, `medium`, `large`, default: `medium`): Context detail level.
  - `target_files` (array of strings, optional): Specific files the agent intends to touch.
- **Output**: Formatted markdown context block ready for agent prompt injection.

### 2. `project_get_architecture`
- **Description**: Returns the high-level structural map of the repository, including core modules, service boundaries, and dependency directions.
- **Input Schema**:
  - `module` (string, optional): Specific sub-module or directory to focus on.
  - `depth` (integer, optional, default: 2): Traversal depth.
- **Output**: Structured component topology and key public interfaces.

### 3. `project_get_component`
- **Description**: Retrieves detailed AST entity information for a specific class, function, or module, including its imports, callers, and associated memories.
- **Input Schema**:
  - `qualified_name` (string, required): e.g., `PaymentService.process_payment` or `auth/jwt.py`.
- **Output**: Signature, file location, dependencies, dependents, and linked constraints.

### 4. `memory_search`
- **Description**: Performs hybrid search across project memories (decisions, constraints, failures, conventions).
- **Input Schema**:
  - `query` (string, required): Search query.
  - `memory_type` (string, optional, enum: `FACT`, `DECISION`, `CONSTRAINT`, `FAILURE`, `CONVENTION`, etc.).
  - `limit` (integer, optional, default: 5).
- **Output**: Ranked list of memories with provenance, status, and confidence.

### 5. `memory_create`
- **Description**: Records a new durable project memory discovered during agent work.
- **Input Schema**:
  - `memory_type` (string, required): e.g., `DECISION`, `CONSTRAINT`, `FAILURE`, `LESSON`.
  - `title` (string, required): Short descriptive title.
  - `content` (string, required): Detailed rationale and facts.
  - `summary` (string, required): One-sentence summary.
  - `evidence_file` (string, optional): Relevant source code path.
  - `evidence_lines` (array of integers, optional): `[start_line, end_line]`.
- **Output**: Created memory ID, status, and verification assignment.

### 6. `memory_get_decisions`
- **Description**: Retrieves historical architectural and design decisions, their rationale, and superseded choices.
- **Input Schema**:
  - `component` (string, optional): Filter decisions relevant to a given component.
- **Output**: List of architectural decisions and their current validity state.

### 7. `memory_get_failures`
- **Description**: Retrieves previous failed attempts, anti-patterns, and bug post-mortems to avoid repeating mistakes.
- **Input Schema**:
  - `component` (string, optional): Filter by affected component.
- **Output**: List of past failure episodes, root causes, and verified fixes.

### 8. `memory_get_constraints`
- **Description**: Retrieves hard architectural, operational, and business constraints that must be preserved.
- **Input Schema**:
  - `component` (string, optional): Component or subsystem.
- **Output**: Invariant constraints and test requirements.

### 9. `graph_get_dependencies`
- **Description**: Returns all downstream dependencies of an entity up to depth $N$.
- **Input Schema**:
  - `entity_name` (string, required): Symbol or file path.
  - `depth` (integer, optional, default: 2).

### 10. `graph_get_dependents`
- **Description**: Returns all upstream callers and consumers of an entity to evaluate blast radius.
- **Input Schema**:
  - `entity_name` (string, required): Symbol or file path.
  - `depth` (integer, optional, default: 2).

### 11. `change_get_impact`
- **Description**: Pre-action check analyzing the blast radius of modifying a given set of files or symbols.
- **Input Schema**:
  - `modified_files` (array of strings, required): List of files intended to be changed.
- **Output**: List of affected symbols, dependent tests, linked constraints, and past failures.

### 12. `task_record_decision` / `task_record_failure`
- **Description**: Specialized shortcut tools for agents to register rationale or obstacles encountered during execution.

### 13. `memory_health`
- **Description**: Diagnostic tool reporting count of active vs stale memories and unverified assertions.

## Resources Exposed
- `cortex://project/architecture`: Latest structural architecture summary.
- `cortex://project/constraints`: Active project constraints.
- `cortex://project/decisions`: Active architectural decisions.

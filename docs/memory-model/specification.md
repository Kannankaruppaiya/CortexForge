# CortexForge Cognitive Memory Model Specification

## 1. Cognitive Architecture Layers (L0 – L6)

CortexForge separates cognitive **layer** from **memory_type**. A memory exists at a discrete layer in the project's mental model:

| Layer | Semantic Scope | Primary Memory Types | Typical Token Budget |
|---|---|---|---|
| **L0** | Project Identity, Runtime, Language, Branching | `FACT`, `CONSTRAINT` | ~250 tokens |
| **L1** | Structural Architecture (Files, Symbols, Routes, Schemas) | `ARCHITECTURE_PATTERN`, `FACT` | ~400–1200 tokens |
| **L2** | Coding Conventions, Style Standards, Idioms | `CONVENTION` | ~250 tokens |
| **L3** | Architectural Decisions, Tradeoffs, Rationale | `DECISION` | ~350 tokens |
| **L4** | Failure Episodes, Anti-Patterns, Normalized Signatures | `FAILURE`, `FIX` | ~300 tokens |
| **L5** | Durable Lessons, Consolidated Invariants | `LESSON`, `CONSTRAINT` | ~250 tokens |
| **L6** | Active Working State, Task Objectives, In-flight Changes | `EPISODE`, `TASK` | ~200 tokens |

## 2. Memory Lifecycle Finite State Machine

Transitions between memory states are strictly validated by `MemoryLifecycleManager`:

```
   [CANDIDATE / UNVERIFIED]
             │
             ▼
         [ACTIVE] ◄──────┐
         │   │   │       │ (re-verified)
         │   │   └─► [STALE]
         │   │
         │   └─────► [CONFLICTED]
         │
         ▼
    [SUPERSEDED]
         │
         ▼
    [ARCHIVED / INVALIDATED]
```

### Valid Transitions:
1. `UNVERIFIED -> ACTIVE` (Evidence verified or initial grounded ingestion)
2. `ACTIVE -> STALE` (Evidence file or symbol modified in Git diff)
3. `STALE -> ACTIVE` (Re-verification confirms snippet/AST integrity)
4. `ACTIVE -> CONFLICTED` (Contradiction detected with equal authority)
5. `ACTIVE -> SUPERSEDED` (Higher authority or newer verified code supersedes)
6. `SUPERSEDED -> ARCHIVED` (Archived after consolidation or audit)
7. `CANDIDATE -> INVALIDATED` (Contradicts verified code without evidence)

## 3. Grounded Evidence & Provenance Chain

Every durable memory carries structured evidence linking it to the physical code:
- `file_path`: Relative repository path
- `symbol_id`: Tree-sitter qualified symbol name (`AuthService.login`)
- `line_start` / `line_end`: Exact source code line boundaries
- `evidence_hash`: SHA-256 fingerprint of the source text slice
- `snippet_hash`: AST fingerprint of the surrounding syntax node
- `commit_sha`: Git commit hash where the evidence was established
- `last_verified_at`: Timestamp of the most recent working-tree verification

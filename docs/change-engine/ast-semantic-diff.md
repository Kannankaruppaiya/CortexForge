# Tree-sitter Semantic AST Diff & Symbol-Level Invalidation

## 1. The Fine-Grained Invalidation Invariant

A fundamental flaw of naive repository caches is file-level coarse invalidation: modifying one function in a 2,000-line file marks every memory about that file stale.

**CortexForge Invariant:**
> A memory grounded in function `A` remains `ACTIVE` when function `B` in the same file is modified, provided `A`'s AST fingerprint, signature, and dependency graph are unchanged.

## 2. Semantic Diff Pipeline

```
previous_commit ──► git diff (with hunks & renames)
                          │
                          ▼
                 Tree-sitter AST Parsing
                          │
                          ▼
                  AST Semantic Differ
   ┌──────────────────────┴──────────────────────┐
   ▼                                             ▼
Symbol Added / Removed / Renamed          Signature vs Body Changes
   │                                             │
   └──────────────────────┬──────────────────────┘
                          ▼
             Semantic Change Propagator
   ┌──────────────────────┴──────────────────────┐
   ▼                                             ▼
Direct Graph Dependents                  Affected Evidence Slices
   │                                             │
   ▼                                             ▼
Transitive Blast Radius               Symbol-Level Memory Re-evaluation
```

## 3. Structural Change Types Detected:
- `FILE_ADDED`, `FILE_REMOVED`, `FILE_RENAMED`
- `SYMBOL_ADDED`, `SYMBOL_REMOVED`, `SYMBOL_RENAMED`
- `SIGNATURE_CHANGED`: Triggers downstream dependent symbol re-analysis
- `BODY_CHANGED`: Localized impact; does not break external contracts
- `INHERITANCE_CHANGED`: Traverses class hierarchies and base implementations
- `IMPORT_CHANGED`: Updates module dependency edges
- `ROUTE_CHANGED`: Updates HTTP endpoint signatures and parameter models

# Queryable Provenance Chain & Conflict Resolution

## 1. Provenance Graph Topology

Every memory in CortexForge answers the fundamental engineering questions:
- **Origin**: Which event, commit, or task generated this knowledge?
- **Evidence**: Which file, line slice, and symbol supports it?
- **Authority**: Is this backed by verified code, test execution, or an agent observation?
- **Recency**: When was it last verified against the current working tree?
- **Lineage**: What prior knowledge does this memory supersede, and what has superseded it?

```
Memory (L3/L4/L5)
   ├── evidence ──► File (`services/auth.py`)
   │                  └── symbol (`AuthService.login`)
   │                        └── AST fingerprint (SHA-256)
   ├── source_commit ──► Git Commit (`a3f891b`)
   ├── source_task ──► Agent Task (`task_1049`)
   ├── conflict_group ──► Conflict Cluster (`conf-8f2a1b`)
   ├── supersedes ──► Older Memory (`mem_001`)
   └── superseded_by ──► Newer Memory (`mem_042`)
```

## 2. Contradiction & Supersession Arbitration

When a new memory candidate is introduced, the `ConflictResolver`:
1. Evaluates semantic cosine similarity and lexical token overlap.
2. Evaluates polarity contradiction heuristics (e.g., *required* vs *no longer required*, *enabled* vs *removed*).
3. Compares source authority:
   `VERIFIED_CODE (1.00)` > `VERIFIED_TEST (0.95)` > `GIT (0.80)` > `DOCUMENTATION (0.65)` > `AGENT_OBSERVATION (0.45)` > `UNTRUSTED (0.20)`.
4. Compares commit recency if authorities are equal.
5. If the new candidate dominates, the older memory is transitioned to `SUPERSEDED`, linked via `supersedes_id` / `superseded_by_id`, and grouped in `conflict_group`.
6. If authority is ambiguous, both memories are marked `CONFLICTED` for human review.

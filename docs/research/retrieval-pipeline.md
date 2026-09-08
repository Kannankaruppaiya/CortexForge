# Hybrid Retrieval & Context Composition Pipeline

## Multi-Signal Retrieval Architecture

CortexForge repudiates "pure vector RAG" for codebase understanding. Real coding tasks require structural awareness, dependency directions, recency, confidence, and provenance.

```mermaid
flowchart TD
    Q[User Task Query] --> TC[Task Classification & Entity Extraction]
    TC --> LS[Lexical Search (BM25 / PG Full-Text)]
    TC --> VS[Vector Search (HNSW Cosine)]
    TC --> GE[Graph Expansion (AST Dependents/Dependencies)]
    
    LS --> FUSE[Multi-Signal Fusion Engine]
    VS --> FUSE
    GE --> FUSE
    
    FUSE --> RERANK[Re-ranking & Penalty Scoring]
    RERANK --> BUDGET[Token-Budget Aware Context Composer]
    BUDGET --> CTX[Final Structured Context]
```

## Retrieval Scoring Formula

The ranking score $S(m, q)$ for memory or code entity $m$ given query $q$ is defined as:

$$S(m, q) = w_{sem} \cdot \text{sim}_{vec}(m, q) + w_{lex} \cdot \text{score}_{bm25}(m, q) + w_{graph} \cdot \text{rel}_{graph}(m, q) + w_{task} \cdot \text{match}_{task}(m, q) + w_{fresh} \cdot \text{recency}(m) + w_{conf} \cdot m.confidence + w_{prov} \cdot \text{quality}(m.evidence) - P_{contra} - P_{stale} - P_{redundancy}$$

### Configurable Weights:
All weights are benchmarked and exposed via configuration (`RetrievalWeights`):
- $w_{sem} = 0.28$: Semantic embedding cosine similarity
- $w_{lex} = 0.22$: PostgreSQL `ts_rank` lexical relevance
- $w_{graph} = 0.20$: Graph proximity (1-hop = 1.0, 2-hop = 0.5, 3-hop = 0.25)
- $w_{task} = 0.12$: Alignment with classified task intent (e.g. `DEBUG_FAILURE` prioritizes `FAILURE` and `FIX` memories)
- $w_{fresh} = 0.08$: Time-decay factor $\exp(-\lambda \Delta t)$
- $w_{conf} = 0.05$: Base verified confidence score
- $w_{prov} = 0.05$: Provenance depth score (commits + files verified)
- $P_{contra} = 0.50$: Heavy penalty for conflicted assertions
- $P_{stale} = 0.40$: Penalty for unverified or modified code references
- $P_{redundancy} = 0.30$: Penalty for near-duplicate retrieved items (Maximal Marginal Relevance)

## Context Composer & Budget Allocation

The context builder outputs structured, high-density markdown tailored to token budget profiles:

1. **Small (`~1000-1500 tokens`)**:
   - Targeted component summary & signature
   - Critical invariant constraints (L5)
   - 1-2 most recent failed attempts on this component
2. **Medium (`~3000-4500 tokens`)**:
   - Architecture module map
   - Active architectural decisions (L3)
   - Invariants and constraints (L5)
   - Relevant failure post-mortems and fixes (L4)
   - Direct dependencies and dependent tests
3. **Large (`~8000+ tokens`)**:
   - Comprehensive dependency sub-graph
   - Full decision rationales and historical context
   - Detailed trajectory of recent changes
   - Test suites and mock conventions

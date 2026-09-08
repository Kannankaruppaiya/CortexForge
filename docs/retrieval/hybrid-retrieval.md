# Real Hybrid Retrieval Engine & MMR Diversity Reranking

## 1. Multi-Modal Retrieval Architecture

CortexForge employs a grounded hybrid scoring function combining lexical, vector, and graph topologies:

$$\text{Combined Score} = w_v \cdot S_{\text{vector}} + w_l \cdot S_{\text{bm25}} + w_g \cdot S_{\text{graph}} + B_{\text{layer}} + B_{\text{task}} - P_{\text{stale}} - P_{\text{conflict}}$$

### Scoring Components:
1. **BM25 Lexical Scoring**:
   - Computes standard Robertson-Spärck Jones BM25 with term frequency, inverse document frequency, and document length normalization ($k_1 = 1.5, b = 0.75$).
2. **Dense Vector Similarity**:
   - pgvector inner product / cosine similarity with local FastDeterministic 384-d vector fallback.
3. **Graph Topology Relevance**:
   - Computes 1-hop and 2-hop entity relationships and target file proximity.
4. **Cognitive Layer Priors**:
   - Configurable layer boost based on query intent (e.g., boosting L4 for bugfix tasks, L3 for architecture tasks).
5. **Freshness & Provenance Quality**:
   - Penalties applied for `STALE` (-0.35) and `CONFLICTED` (-0.20) statuses. `SUPERSEDED` memories are filtered out entirely.

## 2. Maximal Marginal Relevance (MMR)

To eliminate redundancy and prevent near-duplicate memories from flooding the context window, retrieval applies greedy MMR reranking:

$$\text{MMR} = \operatorname{argmax}_{d \in R \setminus S} \left[ \lambda \cdot \text{Rel}(d, q) - (1 - \lambda) \max_{s \in S} \text{Sim}(d, s) \right]$$

- $\lambda = 0.65$: Calibrated balance between relevance to the task prompt and diversity among selected memories.
- Inter-document similarity is evaluated using embedding cosine similarity and shared entity overlaps.

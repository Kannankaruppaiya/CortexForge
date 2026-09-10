"""Okapi BM25 lexical ranking engine for CortexForge memories and documents."""

import math
import re
from collections import Counter


class BM25Scorer:
    """Okapi BM25 term frequency-inverse document frequency ranker."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avgdl = 0.0
        self.doc_freqs: Counter[str] = Counter()
        self.doc_lengths: list[int] = []
        self.tokenized_docs: list[list[str]] = []

    def _tokenize(self, text: str) -> list[str]:
        """Normalize and tokenize text into lowercase word tokens."""
        return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]

    def fit(self, documents: list[str]) -> "BM25Scorer":
        """Index corpus documents to compute IDF and document length statistics."""
        self.corpus_size = len(documents)
        if self.corpus_size == 0:
            self.avgdl = 0.0
            return self

        self.tokenized_docs = [self._tokenize(doc) for doc in documents]
        self.doc_lengths = [len(doc) for doc in self.tokenized_docs]
        total_tokens = sum(self.doc_lengths)
        self.avgdl = total_tokens / max(1, self.corpus_size)

        self.doc_freqs = Counter()
        for doc in self.tokenized_docs:
            unique_terms = set(doc)
            for term in unique_terms:
                self.doc_freqs[term] += 1

        return self

    def score_document(self, query: str, doc_idx: int) -> float:
        """Compute BM25 relevance score for a specific indexed document."""
        if doc_idx < 0 or doc_idx >= self.corpus_size or self.corpus_size == 0:
            return 0.0

        q_terms = self._tokenize(query)
        if not q_terms:
            return 0.0

        doc_tokens = self.tokenized_docs[doc_idx]
        doc_len = self.doc_lengths[doc_idx]
        term_counts = Counter(doc_tokens)

        score = 0.0
        for term in q_terms:
            df = self.doc_freqs.get(term, 0)
            if df == 0:
                continue

            # Standard BM25 IDF formulation
            idf = math.log(1.0 + (self.corpus_size - df + 0.5) / (df + 0.5))

            tf = term_counts.get(term, 0)
            # Length normalization denominator
            denom = tf + self.k1 * (
                1.0 - self.b + self.b * (doc_len / max(1e-6, self.avgdl))
            )
            term_score = idf * (tf * (self.k1 + 1.0)) / max(1e-6, denom)
            score += term_score

        # Normalize bounded score roughly to [0, 1] range for rank fusion
        max_possible = len(q_terms) * 4.0
        return max(0.0, min(1.0, score / max(1e-6, max_possible)))

    def score_all(self, query: str) -> list[float]:
        """Compute BM25 scores for all documents in the fitted corpus."""
        return [self.score_document(query, i) for i in range(self.corpus_size)]

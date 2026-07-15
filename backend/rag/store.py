"""Vector store abstraction + offline in-memory implementation.

``VectorStore`` is the swap point for production: OpenSearch (AWS), Azure AI
Search, or Vertex AI Vector Search implement the same three methods and the
rest of the pipeline is untouched (see deployment guides).

The in-memory store needs no model downloads: it embeds chunks as L2-normalised
TF-IDF vectors and scores queries with a BM25 + cosine hybrid, which is strong
for domain terminology ("GSTR-3B", "Section 87A") — exactly what tax Q&A hits.
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter, defaultdict

from rag.chunker import Chunk

_TOKEN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-']*")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class VectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        """Return (chunk, score) pairs, best first, score normalised to 0..1."""

    @abstractmethod
    def __len__(self) -> int: ...


class InMemoryVectorStore(VectorStore):
    _K1 = 1.5
    _B = 0.75

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._tf: list[Counter[str]] = []
        self._df: Counter[str] = Counter()
        self._doc_len: list[int] = []
        self._avg_len: float = 0.0
        self._inverted: dict[str, list[int]] = defaultdict(list)

    def add(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            tokens = tokenize(chunk.text + " " + chunk.section + " " + chunk.doc_title)
            idx = len(self._chunks)
            self._chunks.append(chunk)
            tf = Counter(tokens)
            self._tf.append(tf)
            self._doc_len.append(len(tokens))
            for term in tf:
                self._df[term] += 1
                self._inverted[term].append(idx)
        self._avg_len = sum(self._doc_len) / max(1, len(self._doc_len))

    def _idf(self, term: str) -> float:
        n, df = len(self._chunks), self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, idx: int, q_terms: list[str]) -> float:
        tf, dl = self._tf[idx], self._doc_len[idx]
        score = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            score += self._idf(term) * (f * (self._K1 + 1)) / (
                f + self._K1 * (1 - self._B + self._B * dl / self._avg_len)
            )
        return score

    def _cosine(self, idx: int, q_tf: Counter[str]) -> float:
        tf = self._tf[idx]
        dot = sum(
            (1 + math.log(f)) * self._idf(t) * (1 + math.log(q_tf[t])) * self._idf(t)
            for t, f in tf.items()
            if t in q_tf
        )
        if dot == 0:
            return 0.0
        norm_d = math.sqrt(sum(((1 + math.log(f)) * self._idf(t)) ** 2 for t, f in tf.items()))
        norm_q = math.sqrt(sum(((1 + math.log(f)) * self._idf(t)) ** 2 for t, f in q_tf.items()))
        return dot / (norm_d * norm_q) if norm_d and norm_q else 0.0

    def search(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        if not self._chunks:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []
        q_tf = Counter(q_terms)
        candidates: set[int] = set()
        for term in q_terms:
            candidates.update(self._inverted.get(term, ()))
        if not candidates:
            return []

        bm25_scores = {i: self._bm25(i, q_terms) for i in candidates}
        max_bm25 = max(bm25_scores.values()) or 1.0
        results = []
        for i in candidates:
            hybrid = 0.65 * (bm25_scores[i] / max_bm25) + 0.35 * self._cosine(i, q_tf)
            results.append((self._chunks[i], hybrid))
        results.sort(key=lambda r: -r[1])
        return results[:top_k]

    def __len__(self) -> int:
        return len(self._chunks)

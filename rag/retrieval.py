"""Retrievers: dense vectors, BM25 keywords, hybrid fusion, and MongoDB Atlas."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .chunking import Chunk
from .embeddings import Embedder, tokenize


@dataclass(frozen=True)
class Hit:
    doc_name: str
    chunk_id: str
    text: str
    score: float
    url: str = ""


class Retriever(Protocol):
    def search(self, query: str, k: int) -> list[Hit]: ...


class DenseRetriever:
    """Exact cosine-similarity search over an in-memory embedding matrix."""

    def __init__(self, chunks: list[Chunk], embedder: Embedder):
        self.chunks = chunks
        self.embedder = embedder
        self.matrix = embedder.encode([c.text for c in chunks]) if chunks else np.zeros((0, embedder.dim))

    def search(self, query: str, k: int) -> list[Hit]:
        if not self.chunks:
            return []
        q = self.embedder.encode([query])[0]
        scores = self.matrix @ q
        top = np.argsort(-scores)[:k]
        return [_hit(self.chunks[i], float(scores[i])) for i in top]


class BM25Retriever:
    """Okapi BM25 keyword retriever (k1=1.5, b=0.75). No external dependencies."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c.text)) for c in chunks]
        self.lengths = np.array([sum(d.values()) for d in self.docs], dtype=float)
        self.avg_len = float(self.lengths.mean()) if len(self.lengths) else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(len(self.docs))
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, d in enumerate(self.docs):
                tf = d.get(term, 0)
                if tf:
                    denom = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len)
                    out[i] += idf * tf * (self.k1 + 1) / denom
        return out

    def search(self, query: str, k: int) -> list[Hit]:
        s = self.scores(query)
        top = [i for i in np.argsort(-s)[:k] if s[i] > 0]
        return [_hit(self.chunks[i], float(s[i])) for i in top]


class HybridRetriever:
    """Reciprocal Rank Fusion of several retrievers: score = sum 1 / (rrf_k + rank).

    RRF needs no score calibration between BM25 and cosine similarity, which live on
    completely different scales.
    """

    def __init__(self, retrievers: list[Retriever], rrf_k: int = 60, depth: int = 20):
        self.retrievers = retrievers
        self.rrf_k = rrf_k
        self.depth = depth

    def search(self, query: str, k: int) -> list[Hit]:
        fused: dict[str, float] = {}
        by_id: dict[str, Hit] = {}
        for r in self.retrievers:
            for rank, h in enumerate(r.search(query, self.depth), start=1):
                fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
                by_id.setdefault(h.chunk_id, h)
        order = sorted(fused, key=fused.get, reverse=True)[:k]
        return [Hit(by_id[c].doc_name, c, by_id[c].text, fused[c], by_id[c].url) for c in order]


class AtlasVectorRetriever:
    """MongoDB Atlas $vectorSearch (production backend)."""

    def __init__(self, collection: Any, embedder: Embedder, index: str, num_candidates: int = 100):
        self.coll = collection
        self.embedder = embedder
        self.index = index
        self.num_candidates = num_candidates

    def search(self, query: str, k: int) -> list[Hit]:
        qvec = self.embedder.encode([query])[0].tolist()
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index,
                    "path": "embedding",
                    "queryVector": qvec,
                    "numCandidates": max(self.num_candidates, k),
                    "limit": k,
                }
            },
            {"$project": {"_id": 0, "docName": 1, "chunkId": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
        ]
        return [
            Hit(d.get("docName", ""), d.get("chunkId", ""), d.get("text", ""), float(d.get("score", 0.0)))
            for d in self.coll.aggregate(pipeline, maxTimeMS=8000)
        ]


def _hit(c: Chunk, score: float) -> Hit:
    return Hit(doc_name=c.doc_name, chunk_id=c.chunk_id, text=c.text, score=score, url=c.url)


def build_memory_retriever(chunks: list[Chunk], mode: str, embedder: Embedder | None) -> Retriever:
    if mode == "bm25":
        return BM25Retriever(chunks)
    if embedder is None:
        raise ValueError(f"retrieval mode {mode!r} needs an embedder")
    if mode == "dense":
        return DenseRetriever(chunks, embedder)
    if mode == "hybrid":
        return HybridRetriever([DenseRetriever(chunks, embedder), BM25Retriever(chunks)])
    raise ValueError(f"unknown retrieval mode {mode!r}")

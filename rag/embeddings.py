"""Embedding backends."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

import numpy as np
from cachetools import TTLCache

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class Embedder(Protocol):
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) array of L2-normalized embeddings."""
        ...


class SentenceTransformerEmbedder:
    """Wraps a sentence-transformers model (default all-MiniLM-L6-v2, 384-dim)."""

    def __init__(self, model_name: str, cache_dir: str | None = None):
        from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        self._model = SentenceTransformer(model_name, cache_folder=cache_dir)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, normalize_embeddings=True), dtype=np.float32)


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder.

    Not semantically smart, but needs no model download. Used for tests, CI smoke runs
    and fully offline demos.
    """

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _bucket(self, token: str) -> int:
        return int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "little") % self.dim

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for tok in tokenize(text):
                out[row, self._bucket(tok)] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class CachedEmbedder:
    """TTL cache in front of any embedder, for repeated queries."""

    def __init__(self, inner: Embedder, ttl_seconds: int = 600, maxsize: int = 2048):
        self.inner = inner
        self.dim = inner.dim
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self.hits = 0
        self.misses = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        rows: list[np.ndarray | None] = []
        missing = []
        for i, t in enumerate(texts):
            key = t.strip()
            if key in self._cache:
                self.hits += 1
                rows.append(self._cache[key])
            else:
                self.misses += 1
                rows.append(None)
                missing.append(i)
        if missing:
            fresh = self.inner.encode([texts[i].strip() for i in missing])
            for i, vec in zip(missing, fresh, strict=True):
                self._cache[texts[i].strip()] = vec
                rows[i] = vec
        return np.vstack(rows)


def build_embedder(kind: str, model_name: str = "", cache_dir: str | None = None) -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name, cache_dir)
    raise ValueError(f"unknown embedder {kind!r}")

"""Phase Quality-V2-A — content-addressed embedding cache.

Embeddings are deterministic for a (model, text) pair, so we cache by
``sha256(model + "\\n" + text)``. The default backend is a simple
in-process dict (enough for the evaluator + tests); a persistent
(pgvector / table) backend can implement the same ``EmbeddingCache``
protocol later without touching callers.

Text hashing also enables embedding versioning: when the model
changes, the key changes, so stale vectors are never served.
"""

from __future__ import annotations

import hashlib
from typing import Protocol


def embedding_key(model: str, text: str) -> str:
    """Stable content-addressed key for a (model, text) pair."""
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def embedding_cache_key(
    provider: str, model: str, input_type: str, text: str
) -> str:
    """Phase Quality-V2-C — cache key over (provider, model, input_type,
    text).

    ``input_type`` matters: the same text embedded as a ``document`` vs
    a ``query`` is a different vector for retrieval models, so they must
    be cached separately. Including ``provider`` + ``model`` means a
    provider/model change invalidates the cache (never serves stale
    vectors)."""
    h = hashlib.sha256()
    for part in (provider, model, input_type, text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class EmbeddingCache(Protocol):
    def get(self, key: str) -> list[float] | None: ...
    def set(self, key: str, vector: list[float]) -> None: ...


class InMemoryEmbeddingCache:
    """Trivial dict-backed cache. Process-local; cleared on restart."""

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}

    def get(self, key: str) -> list[float] | None:
        return self._store.get(key)

    def set(self, key: str, vector: list[float]) -> None:
        self._store[key] = vector

    def __len__(self) -> int:
        return len(self._store)

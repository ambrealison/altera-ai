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
import json
from pathlib import Path
from typing import Protocol


def embedding_key(model: str, text: str) -> str:
    """Stable content-addressed key for a (model, text) pair."""
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def embedding_cache_key(
    provider: str,
    model: str,
    input_type: str,
    text: str,
    dimensions: int | None = None,
) -> str:
    """Phase Quality-V2-C/E — cache key over (provider, model, input_type,
    dimensions, text).

    ``input_type`` matters: the same text embedded as a ``document`` vs
    a ``query`` is a different vector for retrieval models, so they are
    cached separately (this is the "documents and queries are separate
    caches" guarantee). Including ``provider`` + ``model`` +
    ``dimensions`` means any of those changing invalidates the cache
    (never serves stale vectors). ``dimensions`` defaults to ``None`` so
    older 4-argument call sites keep working unchanged."""
    h = hashlib.sha256()
    for part in (provider, model, input_type, str(dimensions), text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class EmbeddingCache(Protocol):
    def get(self, key: str) -> list[float] | None: ...
    def set(self, key: str, vector: list[float]) -> None: ...
    def flush(self) -> None: ...


class InMemoryEmbeddingCache:
    """Trivial dict-backed cache. Process-local; cleared on restart."""

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> list[float] | None:
        vec = self._store.get(key)
        if vec is None:
            self.misses += 1
            return None
        self.hits += 1
        return vec

    def set(self, key: str, vector: list[float]) -> None:
        self._store[key] = vector

    def flush(self) -> None:  # parity with FileEmbeddingCache; no-op
        return None

    def __len__(self) -> int:
        return len(self._store)


class FileEmbeddingCache:
    """Phase Quality-V2-E — persistent, resumable embedding cache.

    A content-addressed JSON file mapping ``embedding_cache_key`` →
    vector. Built so a long full-NEVO benchmark can be interrupted and
    resumed without re-embedding completed batches: vectors are flushed
    to disk every ``autosave_every`` writes (and on explicit ``flush``),
    via an atomic temp-file replace so a crash never corrupts the cache.

    Document vs query vectors never collide because ``input_type`` is part
    of the key (so this one file holds both, kept logically separate).
    ``hits`` / ``misses`` are tracked for progress reporting.
    """

    def __init__(self, path: str | Path, *, autosave_every: int = 128) -> None:
        self._path = Path(path)
        self._store: dict[str, list[float]] = {}
        self._pending = 0
        self._autosave_every = max(1, autosave_every)
        self.hits = 0
        self.misses = 0
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._store = {str(k): list(v) for k, v in loaded.items()}
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                # A corrupt/partial cache file is non-fatal — start empty
                # and let the run repopulate it.
                self._store = {}

    def get(self, key: str) -> list[float] | None:
        vec = self._store.get(key)
        if vec is None:
            self.misses += 1
            return None
        self.hits += 1
        return vec

    def set(self, key: str, vector: list[float]) -> None:
        self._store[key] = list(vector)
        self._pending += 1
        if self._pending >= self._autosave_every:
            self.flush()

    def flush(self) -> None:
        """Atomically persist the cache to disk (temp file + replace)."""
        if self._pending == 0 and self._path.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._store), encoding="utf-8")
        tmp.replace(self._path)
        self._pending = 0

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._store)

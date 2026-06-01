"""Phase Quality-V2-A — deterministic fake embedding provider.

No network. Produces a stable, normalised vector from a hash of the
text so tests are reproducible and similar texts that share tokens get
correlated vectors (enough to exercise a retriever's ranking logic).
``input_type`` is accepted and recorded but does not change the output
for the fake (real providers differentiate document vs query).
"""

from __future__ import annotations

import hashlib
import math


class FakeEmbeddingProvider:
    """Deterministic, offline embedding provider for tests + dev."""

    def __init__(self, dimensions: int = 64) -> None:
        self._dims = dimensions

    @property
    def model(self) -> str:
        return "fake-deterministic-v1"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _vector(self, text: str) -> list[float]:
        # Token-bag hashing: each token contributes to a few dimensions
        # so texts sharing tokens get correlated vectors. Deterministic
        # via sha256 — no randomness (which is also unavailable in some
        # sandboxes).
        vec = [0.0] * self._dims
        tokens = [t for t in text.lower().split() if t]
        if not tokens:
            tokens = ["__empty__"]
        for tok in tokens:
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            for i in range(0, len(h), 2):
                idx = (h[i] << 8 | h[i + 1]) % self._dims
                vec[idx] += 1.0
        # L2-normalise so cosine similarity is just a dot product.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Convenience for retriever prototypes/tests."""
    return sum(x * y for x, y in zip(a, b, strict=True))

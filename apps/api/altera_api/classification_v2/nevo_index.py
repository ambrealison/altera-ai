"""Phase Quality-V2-C — offline NEVO vector index (evaluator-only).

An in-memory cosine-similarity index over NEVO reference foods. It is a
*candidate generator*: ``search`` returns the most semantically similar
reference foods for a product query; the V2 NEVO rules then gate those
candidates (a candidate is never accepted on similarity alone). No
production route uses this.

Privacy: reference text and product query text are built by the
``embeddings.text_builder`` helpers, which raise on any commercial /
physical field — sales, units, weight, price, margin can never be
embedded.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altera_api.classification_v2.nevo_rules import NevoCandidate
from altera_api.embeddings.cache import (
    EmbeddingCache,
    InMemoryEmbeddingCache,
    embedding_cache_key,
)
from altera_api.embeddings.fake_provider import cosine_similarity
from altera_api.embeddings.provider import EmbeddingProvider
from altera_api.embeddings.text_builder import (
    build_nevo_reference_text,
    build_product_text,
)


@dataclass(frozen=True)
class BuildProgress:
    """Phase Quality-V2-E — a progress event emitted while building the
    document index, so the benchmark can print observable, flushed
    output during a long full-NEVO embedding run.

    ``stage`` is ``"start"`` (once, before any batch) or ``"batch"``
    (after each document batch completes)."""

    stage: str
    references: int          # total reference foods
    to_embed: int            # unique cache-miss texts to embed
    batches: int             # total document batches
    batch_size: int
    batch_index: int = 0     # 1-based; 0 for the "start" event
    embedded: int = 0        # unique texts embedded so far


#: A progress callback receives a :class:`BuildProgress` event.
BuildProgressFn = Callable[[BuildProgress], None]

# Default full NEVO reference export shipped in the repo.
_NEVO_CSV = (
    Path(__file__).resolve().parents[1] / "data" / "reference" / "nevo2025.csv"
)


def load_nevo_reference(
    source: str = "fixture",
    *,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load NEVO reference foods for the vector index (Phase V2-D).

    ``source="fixture"`` → the small curated reference JSON
    (``--reference path`` to override). ``source="nevo"`` → the full
    NEVO 2025 reference CSV shipped in the repo (2.3k foods), mapping the
    English/Dutch names, synonym and food group. Reads only the
    descriptor columns — no nutrition values are loaded into the index.
    """
    if source == "nevo":
        csv_path = Path(path) if path else _NEVO_CSV
        refs: list[dict[str, Any]] = []
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                en = (row.get("Engelse naam/Food name") or "").strip()
                nl = (row.get("Voedingsmiddelnaam/Dutch food name") or "").strip()
                if not (en or nl):
                    continue
                refs.append(
                    {
                        "nevo_code": (row.get("NEVO-code") or "").strip(),
                        "food_name_en": en or nl,
                        "food_name_nl": nl,
                        "synonym": (row.get("Synoniem") or "").strip() or None,
                        "food_group": (row.get("Food group") or "").strip() or None,
                    }
                )
        return refs

    # fixture (default)
    fixture = Path(path) if path else (
        Path(__file__).resolve().parents[1]
        / "data" / "eval" / "nevo" / "nevo_reference.json"
    )
    data = json.loads(Path(fixture).read_text(encoding="utf-8"))
    return data.get("references", data if isinstance(data, list) else [])


def build_nevo_query_text(product: dict[str, Any]) -> str:
    """Privacy-safe product query text for NEVO retrieval.

    Delegates to ``build_product_text``, which (a) raises
    ``ForbiddenEmbeddingField`` if ANY commercial/physical field is
    present in the input, and (b) emits only the allowed descriptor
    lines (name, retailer category, ingredients, labels)."""
    return build_product_text(product)


@dataclass
class ScoredCandidate:
    candidate: NevoCandidate
    similarity: float
    rank: int


@dataclass
class NevoVectorIndex:
    """Cosine index over NEVO reference foods (offline).

    Phase Quality-V2-E: ``build`` embeds references in *batches* (one
    provider call per batch instead of per food), de-duplicates repeated
    texts, looks up a (persistent) cache first so an interrupted run
    resumes without re-embedding, and emits progress events. ``search``
    embeds the query (cached) and ranks by cosine similarity.
    """

    provider: EmbeddingProvider
    provider_name: str = "fake"
    top_k: int = 20
    cache: EmbeddingCache | None = None
    batch_size: int = 64
    _refs: list[NevoCandidate] = field(default_factory=list)
    _vectors: list[list[float]] = field(default_factory=list)
    embedding_calls: int = 0  # unique texts embedded (cache misses)

    @property
    def _dimensions(self) -> int | None:
        return getattr(self.provider, "dimensions", None)

    def _key(self, text: str, input_type: str) -> str:
        return embedding_cache_key(
            self.provider_name, self.provider.model, input_type, text,
            self._dimensions,
        )

    def _embed(self, text: str, input_type: str) -> list[float]:
        """Single-text embed (used by ``search`` for the query)."""
        key = self._key(text, input_type)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        self.embedding_calls += 1
        vec = (
            self.provider.embed_documents([text])[0]
            if input_type == "document"
            else self.provider.embed_query(text)
        )
        if self.cache is not None:
            self.cache.set(key, vec)
        return vec

    def build(
        self,
        references: list[dict[str, Any]],
        *,
        progress: BuildProgressFn | None = None,
    ) -> None:
        """Index NEVO reference dicts (food_name_en, …) in batches.

        Cache hits are served first; only unique cache-miss texts are
        embedded, in ``batch_size`` chunks. ``progress`` (if given) is
        called once with ``stage="start"`` and once per completed batch.
        """
        texts = [build_nevo_reference_text(ref) for ref in references]
        cands = [
            NevoCandidate(
                nevo_code=str(ref.get("nevo_code", "")),
                food_name_en=str(ref.get("food_name_en", "")),
            )
            for ref in references
        ]
        vectors: list[list[float] | None] = [None] * len(texts)

        # Resolve cache hits; collect unique misses (dedup by key so an
        # in-build repeat embeds once — preserves embedding_calls semantics).
        miss_positions: dict[str, list[int]] = {}
        miss_text: dict[str, str] = {}
        for i, text in enumerate(texts):
            key = self._key(text, "document")
            hit = self.cache.get(key) if self.cache is not None else None
            if hit is not None:
                vectors[i] = hit
            else:
                miss_text.setdefault(key, text)
                miss_positions.setdefault(key, []).append(i)

        unique_keys = list(miss_positions.keys())
        bs = max(1, self.batch_size)
        total_batches = math.ceil(len(unique_keys) / bs) if unique_keys else 0
        if progress is not None:
            progress(
                BuildProgress(
                    stage="start", references=len(texts),
                    to_embed=len(unique_keys), batches=total_batches,
                    batch_size=bs,
                )
            )

        embedded = 0
        for b, start in enumerate(range(0, len(unique_keys), bs)):
            chunk_keys = unique_keys[start : start + bs]
            chunk_texts = [miss_text[k] for k in chunk_keys]
            embs = self.provider.embed_documents(chunk_texts)
            self.embedding_calls += len(chunk_texts)
            embedded += len(chunk_texts)
            for k, vec in zip(chunk_keys, embs, strict=True):
                if self.cache is not None:
                    self.cache.set(k, vec)
                for pos in miss_positions[k]:
                    vectors[pos] = vec
            if progress is not None:
                progress(
                    BuildProgress(
                        stage="batch", references=len(texts),
                        to_embed=len(unique_keys), batches=total_batches,
                        batch_size=bs, batch_index=b + 1, embedded=embedded,
                    )
                )

        if self.cache is not None:
            self.cache.flush()
        self._vectors = [v if v is not None else [] for v in vectors]
        self._refs = cands

    @classmethod
    def load_or_build(
        cls,
        references: list[dict[str, Any]],
        *,
        provider: EmbeddingProvider,
        provider_name: str = "fake",
        top_k: int = 20,
        cache: EmbeddingCache | None = None,
        batch_size: int = 64,
        progress: BuildProgressFn | None = None,
    ) -> NevoVectorIndex:
        """Build an index reusing a (persistent) cache.

        With a :class:`FileEmbeddingCache`, a second run over the same
        references/model embeds nothing (all hits); a model/provider/
        dimensions or reference-text change invalidates the affected
        entries (different cache key) and only those are re-embedded.
        """
        index = cls(
            provider=provider, provider_name=provider_name, top_k=top_k,
            cache=cache if cache is not None else InMemoryEmbeddingCache(),
            batch_size=batch_size,
        )
        index.build(references, progress=progress)
        return index

    def search(self, query_text: str, top_k: int | None = None) -> list[ScoredCandidate]:
        """Return the top-k most similar reference foods (desc similarity)."""
        if not self._refs:
            return []
        k = top_k or self.top_k
        qv = self._embed(query_text, "query")
        scored = [
            (cosine_similarity(qv, rv), cand)
            for cand, rv in zip(self._refs, self._vectors, strict=True)
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            ScoredCandidate(candidate=cand, similarity=sim, rank=i + 1)
            for i, (sim, cand) in enumerate(scored[:k])
        ]

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altera_api.classification_v2.nevo_rules import NevoCandidate
from altera_api.embeddings.cache import EmbeddingCache, embedding_cache_key
from altera_api.embeddings.fake_provider import cosine_similarity
from altera_api.embeddings.provider import EmbeddingProvider
from altera_api.embeddings.text_builder import (
    build_nevo_reference_text,
    build_product_text,
)

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
    """Cosine index over NEVO reference foods (offline)."""

    provider: EmbeddingProvider
    provider_name: str = "fake"
    top_k: int = 20
    cache: EmbeddingCache | None = None
    _refs: list[NevoCandidate] = field(default_factory=list)
    _vectors: list[list[float]] = field(default_factory=list)
    embedding_calls: int = 0

    def _embed(self, text: str, input_type: str) -> list[float]:
        key = embedding_cache_key(
            self.provider_name, self.provider.model, input_type, text
        )
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

    def build(self, references: list[dict[str, Any]]) -> None:
        """Index a list of NEVO reference dicts (food_name_en, …)."""
        self._refs = []
        self._vectors = []
        for ref in references:
            text = build_nevo_reference_text(ref)
            self._vectors.append(self._embed(text, "document"))
            self._refs.append(
                NevoCandidate(
                    nevo_code=str(ref.get("nevo_code", "")),
                    food_name_en=str(ref.get("food_name_en", "")),
                )
            )

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

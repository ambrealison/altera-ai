"""Phase Quality-V2-C — Voyage embeddings + NEVO vector candidate search.

Offline only. No test calls the real Voyage API. Confirms: fake is the
default; voyage needs a key when used; a mocked voyage client uses the
right input_type; the cache keys by provider/model/input_type/text; the
NEVO vector index is privacy-safe + deterministic; and the
rules+embeddings pipeline never lets an embedding override a hard
rejection. Production stays on V1 with embeddings disabled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altera_api.classification_v2.evaluation import NevoMetrics, nevo_gates
from altera_api.classification_v2.nevo_eval_embeddings import evaluate_nevo_embeddings
from altera_api.classification_v2.nevo_index import (
    NevoVectorIndex,
    build_nevo_query_text,
)
from altera_api.classification_v2.nevo_pipeline import decide_with_embeddings
from altera_api.embeddings import (
    FakeEmbeddingProvider,
    InMemoryEmbeddingCache,
    VoyageEmbeddingProvider,
    build_embedding_provider,
    embedding_cache_key,
    get_embedding_provider,
)
from altera_api.embeddings.provider import EmbeddingProviderError
from altera_api.embeddings.text_builder import (
    ForbiddenEmbeddingField,
    build_nevo_reference_text,
)

_EVAL = Path(__file__).resolve().parents[2] / "altera_api" / "data" / "eval" / "nevo"
_REFS = json.loads((_EVAL / "nevo_reference.json").read_text())["references"]
_EMBED_FIXTURE = json.loads((_EVAL / "nevo_dataset_embeddings.json").read_text())["cases"]


# ---------------------------------------------------------------------------
# B. Provider selection + Voyage
# ---------------------------------------------------------------------------
class FakeVoyageClient:
    """Records the input_type of each call; returns dummy vectors."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed(self, texts, *, model, input_type, **kwargs):
        self.calls.append({"texts": texts, "model": model, "input_type": input_type})

        class _Resp:
            embeddings = [[0.1, 0.2, 0.3] for _ in texts]

        return _Resp()


class TestProvider:
    def test_default_provider_is_fake(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.delenv("ALTERA_EMBEDDING_PROVIDER", raising=False)
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)

    def test_build_fake_offline(self) -> None:
        p = build_embedding_provider("fake")
        assert isinstance(p, FakeEmbeddingProvider)
        assert len(p.embed_query("tofu")) == p.dimensions

    def test_voyage_requires_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(EmbeddingProviderError):
            VoyageEmbeddingProvider(model="voyage-4-lite")

    def test_voyage_uses_document_and_query_input_types(self) -> None:
        client = FakeVoyageClient()
        p = VoyageEmbeddingProvider(model="voyage-4-lite", client=client)
        p.embed_documents(["reference food"])
        p.embed_query("product query")
        kinds = [c["input_type"] for c in client.calls]
        assert kinds == ["document", "query"]
        assert all(c["model"] == "voyage-4-lite" for c in client.calls)

    def test_voyage_no_network_with_injected_client(self) -> None:
        client = FakeVoyageClient()
        p = VoyageEmbeddingProvider(model="m", client=client)
        out = p.embed_documents(["a", "b"])
        assert len(out) == 2 and out[0] == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# C. Cache key
# ---------------------------------------------------------------------------
class TestCacheKey:
    def test_same_document_text_same_key(self) -> None:
        a = embedding_cache_key("voyage", "m", "document", "tofu")
        b = embedding_cache_key("voyage", "m", "document", "tofu")
        assert a == b

    def test_query_and_document_differ(self) -> None:
        d = embedding_cache_key("voyage", "m", "document", "tofu")
        q = embedding_cache_key("voyage", "m", "query", "tofu")
        assert d != q

    def test_model_change_invalidates(self) -> None:
        a = embedding_cache_key("voyage", "m1", "document", "tofu")
        b = embedding_cache_key("voyage", "m2", "document", "tofu")
        assert a != b

    def test_index_caches_repeated_text(self) -> None:
        cache = InMemoryEmbeddingCache()
        idx = NevoVectorIndex(
            provider=FakeEmbeddingProvider(), provider_name="fake", cache=cache
        )
        idx.build([{"food_name_en": "Tofu"}, {"food_name_en": "Tofu"}])
        # Two identical reference texts → embedded once (one cache entry).
        assert idx.embedding_calls == 1


# ---------------------------------------------------------------------------
# D. NEVO vector index
# ---------------------------------------------------------------------------
class TestIndex:
    def test_reference_text_excludes_commercial(self) -> None:
        with pytest.raises(ForbiddenEmbeddingField):
            build_nevo_reference_text({"food_name_en": "Tofu", "sales_value": 9})

    def test_query_text_excludes_commercial(self) -> None:
        with pytest.raises(ForbiddenEmbeddingField):
            build_nevo_query_text({"product_name": "Tofu", "items_sold": 5})

    def test_search_deterministic_and_top_k(self) -> None:
        idx = NevoVectorIndex(provider=FakeEmbeddingProvider(), provider_name="fake")
        idx.build(_REFS)
        r1 = idx.search("Name: Tofu nature", top_k=5)
        r2 = idx.search("Name: Tofu nature", top_k=5)
        assert [c.candidate.nevo_code for c in r1] == [c.candidate.nevo_code for c in r2]
        assert len(r1) == 5
        assert r1[0].rank == 1


# ---------------------------------------------------------------------------
# E. Rules + embeddings pipeline — embeddings never override hard rejections
# ---------------------------------------------------------------------------
class TestPipeline:
    @pytest.fixture
    def index(self) -> NevoVectorIndex:
        idx = NevoVectorIndex(
            provider=FakeEmbeddingProvider(), provider_name="fake", top_k=20
        )
        idx.build(_REFS)
        return idx

    @pytest.mark.parametrize(
        "product,forbidden_name",
        [
            ("Ratatouille a l'huile d'olive", "Oil olive"),
            ("Ratatouille ail et persil", "Garlic raw"),
            ("Lait demi-ecreme", "Potatoes mashed with milk"),
            ("Beurre doux", "Apple pie without butter"),
        ],
    )
    def test_trap_never_final_match(self, index, product, forbidden_name) -> None:
        d = decide_with_embeddings({"product_name": product}, index)
        assert d.food_name_en != forbidden_name
        # If the trap was retrieved, it must appear rejected, not accepted.
        for tr in d.top_candidates:
            if tr.candidate_name == forbidden_name:
                assert not tr.accepted

    def test_embedding_cannot_override_hard_rejection(self) -> None:
        # Index containing ONLY the trap reference — even as the sole/top
        # candidate it must not be accepted for a different-concept product.
        idx = NevoVectorIndex(provider=FakeEmbeddingProvider(), provider_name="fake")
        idx.build([{"nevo_code": "NEVO-OILOLIVE", "food_name_en": "Oil olive"}])
        d = decide_with_embeddings({"product_name": "Ratatouille a l'huile d'olive"}, idx)
        assert not d.matched
        assert d.match_type == "no_match"

    def test_safe_candidate_accepted(self, index) -> None:
        d = decide_with_embeddings({"product_name": "Tofu nature"}, index)
        assert d.matched and d.food_name_en == "Tofu"
        assert d.match_type == "embedding_plus_rule"
        assert d.confidence >= 0.90

    def test_ambiguous_abstains(self, index) -> None:
        d = decide_with_embeddings({"product_name": "Box repas surprise xyz"}, index)
        assert not d.matched and d.match_type == "no_match"

    def test_decision_carries_trace_and_provider(self, index) -> None:
        d = decide_with_embeddings({"product_name": "Pois chiches"}, index)
        assert d.provider == "fake" and d.model
        assert len(d.top_candidates) > 0


# ---------------------------------------------------------------------------
# H. Gates + evaluator
# ---------------------------------------------------------------------------
class TestGatesAndEval:
    def test_embeddings_eval_gates_pass_with_fake(self) -> None:
        m, rows = evaluate_nevo_embeddings(
            _EMBED_FIXTURE, _REFS, FakeEmbeddingProvider(), provider_name="fake"
        )
        assert m.false_positive_count == 0
        assert m.forbidden_rejected == m.forbidden_total
        assert nevo_gates(m)["passed"]
        assert rows  # candidate trace populated

    def test_gate_fails_on_false_positive(self) -> None:
        m = NevoMetrics(
            matcher_version="v2-embeddings", forbidden_total=2, forbidden_rejected=1,
            false_positive_count=1,
        )
        assert not nevo_gates(m)["passed"]


# ---------------------------------------------------------------------------
# Production untouched.
# ---------------------------------------------------------------------------
class TestProductionUntouched:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from altera_api.quality_config import (
            embedding_model,
            embedding_provider_name,
            embeddings_enabled,
        )

        for v in (
            "ALTERA_ENABLE_EMBEDDINGS",
            "ALTERA_EMBEDDING_PROVIDER",
            "ALTERA_EMBEDDING_MODEL",
        ):
            monkeypatch.delenv(v, raising=False)
        assert embeddings_enabled() is False
        assert embedding_provider_name() == "fake"
        assert embedding_model() == "voyage-4-lite"

    def test_routes_do_not_import_embeddings_or_voyage(self) -> None:
        src = (
            Path(__file__).resolve().parents[2] / "altera_api" / "api" / "routes.py"
        ).read_text(encoding="utf-8")
        assert "voyage" not in src.lower()
        assert "embeddings" not in src

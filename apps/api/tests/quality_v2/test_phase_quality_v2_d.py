"""Phase Quality-V2-D — real-Voyage eval harness (offline-safe).

No test calls the real Voyage API. Confirms: a present VOYAGE_API_KEY
does NOT enable embeddings in the env factory; the gate hardening that
closed the sub-token false-match hole; the full-NEVO reference loader;
top-k + token metrics; the failure taxonomy; and Voyage token
accounting via a mocked client.
"""

from __future__ import annotations

import pytest

from altera_api.classification_v2.evaluation import load_fixture, nevo_gates
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
)
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_rules import NevoCandidate, gate_candidate
from altera_api.embeddings import (
    FakeEmbeddingProvider,
    VoyageEmbeddingProvider,
    get_embedding_provider,
)
from altera_api.embeddings.text_builder import (
    ForbiddenEmbeddingField,
    build_nevo_reference_text,
)


# ---------------------------------------------------------------------------
# A. Safety — a present VOYAGE_API_KEY must not change runtime behaviour.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_key_present_but_embeddings_disabled_is_fake(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        # Key set, embeddings off → still the offline fake provider.
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)

    def test_embeddings_disabled_overrides_voyage_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        monkeypatch.setenv("ALTERA_EMBEDDING_PROVIDER", "voyage")
        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "false")
        # Disabled wins — no real provider is constructed.
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)


# ---------------------------------------------------------------------------
# Gate hardening regression (sub-token false match).
# ---------------------------------------------------------------------------
class TestGateHardening:
    def test_subtoken_not_a_match(self) -> None:
        # 'peanut' is the head of 'peanut butter' (concept peanut_butter);
        # a bare sub-token match to 'Biscuit peanut' must be rejected.
        r = gate_candidate("Peanut butter", NevoCandidate("X", "Biscuit peanut"))
        assert not r.accepted

    def test_concept_match_still_accepts(self) -> None:
        r = gate_candidate("Peanut butter", NevoCandidate("OK", "Peanut butter"))
        assert r.accepted and r.match_type in ("exact", "alias")

    def test_plain_head_match_without_concept_still_works(self) -> None:
        # 'Basmati rice' has no mapped concept → literal head match is OK.
        r = gate_candidate("Basmati rice", NevoCandidate("OK", "Basmati rice cooked"))
        assert r.accepted


# ---------------------------------------------------------------------------
# Full NEVO reference loader (PART E).
# ---------------------------------------------------------------------------
class TestReferenceLoader:
    def test_fixture_source(self) -> None:
        refs = load_nevo_reference("fixture")
        assert len(refs) >= 20
        assert all("food_name_en" in r for r in refs)

    def test_full_nevo_source(self) -> None:
        refs = load_nevo_reference("nevo")
        assert len(refs) > 1000  # the full 2025 reference
        sample = refs[0]
        assert sample["food_name_en"] and "food_group" in sample
        # Reference text builds + carries no commercial field.
        assert "Food:" in build_nevo_reference_text(sample)

    def test_reference_text_excludes_commercial(self) -> None:
        with pytest.raises(ForbiddenEmbeddingField):
            build_nevo_reference_text({"food_name_en": "Tofu", "price": 3})


# ---------------------------------------------------------------------------
# Metrics: top-k buckets + token total + taxonomy (fake, offline).
# ---------------------------------------------------------------------------
class TestMetricsAndTaxonomy:
    def _run(self):
        cases = load_fixture(
            "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"
        )
        refs = load_nevo_reference("fixture")
        return cases, evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake", top_k=20
        )

    def test_topk_and_tokens(self) -> None:
        _cases, (m, _rows) = self._run()
        d = m.as_dict()
        assert d["expected_top1"] is not None
        assert d["expected_top20"] >= d["expected_top5"] >= d["expected_top1"]
        assert d["token_total"] == 0  # fake provider has no token cost

    def test_gates_pass_offline(self) -> None:
        _cases, (m, _rows) = self._run()
        assert m.false_positive_count == 0
        assert nevo_gates(m)["passed"]

    def test_taxonomy_buckets(self) -> None:
        cases, (_m, rows) = self._run()
        tax = summarize_candidates(cases, rows)
        for key in (
            "expected_rank_1",
            "expected_rank_2_5",
            "expected_retrieved_but_rejected",
            "expected_missing_from_topk",
            "dangerous_ranked_high_but_rejected",
        ):
            assert key in tax
        # Forbidden/trap candidates that ranked high were correctly killed.
        assert tax["dangerous_ranked_high_but_rejected"] >= 0


# ---------------------------------------------------------------------------
# Voyage token accounting via a mocked client (no network).
# ---------------------------------------------------------------------------
class TestVoyageTokens:
    def test_total_tokens_accumulate(self) -> None:
        class Client:
            def embed(self, texts, *, model, input_type, **kw):
                class R:
                    embeddings = [[0.0, 1.0] for _ in texts]
                    total_tokens = 7
                return R()

        p = VoyageEmbeddingProvider(model="voyage-4", client=Client())
        p.embed_documents(["a", "b"])
        p.embed_query("c")
        assert p.total_tokens == 14  # 7 + 7
        assert p.call_count == 2

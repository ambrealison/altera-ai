"""Phase Quality-V2-A — V2 foundation tests.

Covers:
  A. Feature flags default to V1; env selects V2; embeddings off by
     default; production stays on V1.
  E. Rule engine returns a trace; rules are deterministic; minimal
     PT/WWF rules classify the known cases.
  F. Fake embedding provider works; text builder rejects commercial
     fields; cache key is content-addressed + model-versioned.
  NEVO gates reject the traps (zero false positives).
  Evaluator basics: loads fixtures, computes metrics, writes the
  mismatch CSV with the documented columns.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from altera_api.classification_v2.evaluation import (
    MISMATCH_CSV_COLUMNS,
    evaluate_classification,
    evaluate_nevo,
    load_fixture,
    write_mismatches_csv,
)
from altera_api.classification_v2.nevo_rules import NevoCandidate, gate_candidate
from altera_api.classification_v2.pt_rules import PT_RULES
from altera_api.classification_v2.rule_engine import ProductInput, RuleEngine
from altera_api.classification_v2.wwf_rules import WWF_RULES
from altera_api.embeddings import (
    FakeEmbeddingProvider,
    InMemoryEmbeddingCache,
    cosine_similarity,
    embedding_key,
    get_embedding_provider,
)
from altera_api.embeddings.text_builder import (
    ForbiddenEmbeddingField,
    build_product_text,
)
from altera_api.quality_config import (
    MatcherVersion,
    PipelineVersion,
    classification_pipeline_version,
    embeddings_enabled,
    nevo_matcher_version,
    v2_evaluation_enabled,
)

_EVAL = Path(__file__).resolve().parents[2] / "altera_api" / "data" / "eval"


# ---------------------------------------------------------------------------
# A. Feature flags
# ---------------------------------------------------------------------------
class TestFeatureFlags:
    def test_defaults_are_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k in (
            "ALTERA_CLASSIFICATION_PIPELINE_VERSION",
            "ALTERA_NEVO_MATCHER_VERSION",
            "ALTERA_ENABLE_EMBEDDINGS",
            "ALTERA_ENABLE_V2_EVALUATION",
        ):
            monkeypatch.delenv(k, raising=False)
        assert classification_pipeline_version() is PipelineVersion.V1
        assert nevo_matcher_version() is MatcherVersion.V1
        assert embeddings_enabled() is False
        assert v2_evaluation_enabled() is False

    def test_env_selects_v2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_CLASSIFICATION_PIPELINE_VERSION", "v2")
        monkeypatch.setenv("ALTERA_NEVO_MATCHER_VERSION", "v2")
        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "true")
        assert classification_pipeline_version() is PipelineVersion.V2
        assert nevo_matcher_version() is MatcherVersion.V2
        assert embeddings_enabled() is True

    def test_unknown_value_falls_back_to_v1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_CLASSIFICATION_PIPELINE_VERSION", "v9")
        assert classification_pipeline_version() is PipelineVersion.V1


# ---------------------------------------------------------------------------
# H. Production safety — no production module imports the V2 stack
# ---------------------------------------------------------------------------
class TestProductionUntouched:
    def test_routes_do_not_import_classification_v2(self) -> None:
        import inspect

        from altera_api.api import routes

        assert "classification_v2" not in inspect.getsource(routes)

    def test_routes_do_not_import_embeddings(self) -> None:
        import inspect

        from altera_api.api import routes

        assert "altera_api.embeddings" not in inspect.getsource(routes)

    def test_get_embedding_provider_is_fake_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        provider = get_embedding_provider()
        assert isinstance(provider, FakeEmbeddingProvider)


# ---------------------------------------------------------------------------
# E. Rule engine + minimal rules
# ---------------------------------------------------------------------------
class TestRuleEngine:
    def test_engine_returns_trace_and_first_match(self) -> None:
        engine = RuleEngine(PT_RULES, name="pt")
        out = engine.evaluate(ProductInput(product_name="Wrap falafel houmous"))
        assert out.result.matched
        assert out.result.classification["pt_group"] == "plant_based_core"
        # Trace records every rule tried + the winner.
        assert out.trace.winning_rule_id == out.result.rule_id
        assert len(out.trace.entries) >= 1

    def test_engine_abstains_when_no_rule_matches(self) -> None:
        engine = RuleEngine(PT_RULES, name="pt")
        out = engine.evaluate(ProductInput(product_name="zzzz gizmo"))
        assert not out.result.matched
        assert out.result.rule_id == "abstain"
        assert out.result.review_required

    def test_rules_are_deterministic(self) -> None:
        engine = RuleEngine(PT_RULES)
        p = ProductInput(product_name="Soupe lentilles coco")
        a = engine.evaluate(p).result
        b = engine.evaluate(p).result
        assert (a.rule_id, a.confidence, a.classification) == (
            b.rule_id,
            b.confidence,
            b.classification,
        )

    @pytest.mark.parametrize(
        "name,expected",
        [
            # Rule 2 — central plant protein, no animal → plant_core.
            ("Wrap falafel houmous", "plant_based_core"),
            ("Burger haricots noirs", "plant_based_core"),
            ("Bowl tofu riz legumes", "plant_based_core"),
            # Rule 1 — animal + plant protein → composite.
            ("Salade poulet pois chiches", "composite_products"),
            # Rule 3 — snack/cereal/vegan-cue, no animal → non_core.
            ("Muesli avoine fruits graines", "plant_based_non_core"),
        ],
    )
    def test_pt_known_cases(self, name: str, expected: str) -> None:
        # NOTE: the skeleton implements only the 3 documented minimal
        # rules. Cases like "Chili sin carne" (→core) or "Pizza jambon"
        # (animal-only →composite) are TARGET labels in the fixtures
        # but out of the skeleton's scope — they're the work of later
        # V2 phases. The eval harness measures that gap on purpose.
        out = RuleEngine(PT_RULES).evaluate(ProductInput(product_name=name))
        assert out.result.classification.get("pt_group") == expected

    @pytest.mark.parametrize(
        "name,fg,composite",
        [
            ("Carottes", "FG4", False),
            ("Sardines a l'huile", "FG1", False),
            ("Biscuits sables beurre", "FG7", False),
            ("Fromage vegetal noix de cajou", "FG2", False),
            ("Curry poulet riz", "FG1", True),
        ],
    )
    def test_wwf_known_cases(self, name: str, fg: str, composite: bool) -> None:
        out = RuleEngine(WWF_RULES).evaluate(ProductInput(product_name=name))
        assert out.result.classification.get("wwf_food_group") == fg
        assert bool(out.result.classification.get("wwf_is_composite")) == composite


# ---------------------------------------------------------------------------
# F. Embeddings abstraction
# ---------------------------------------------------------------------------
class TestEmbeddings:
    def test_fake_provider_deterministic_and_normalised(self) -> None:
        p = FakeEmbeddingProvider()
        v1 = p.embed_query("curry pois chiches epinards")
        v2 = p.embed_query("curry pois chiches epinards")
        assert v1 == v2
        assert len(v1) == p.dimensions
        # self-similarity ~ 1 (L2-normalised).
        assert abs(cosine_similarity(v1, v1) - 1.0) < 1e-6

    def test_similar_texts_correlate(self) -> None:
        p = FakeEmbeddingProvider()
        a = p.embed_query("lentilles vertes bio")
        b = p.embed_query("lentilles vertes")
        c = p.embed_query("steak boeuf")
        assert cosine_similarity(a, b) > cosine_similarity(a, c)

    def test_text_builder_excludes_commercial_fields(self) -> None:
        ok = build_product_text(
            {
                "product_name": "Curry pois chiches épinards",
                "retailer_category": "Ready meals",
                "ingredients_text": "chickpeas, spinach, coconut milk",
                "labels": "vegan",
            }
        )
        assert "Curry" in ok and "vegan" in ok
        for forbidden in (
            {"product_name": "X", "items_purchased": 10},
            {"product_name": "X", "items_sold": 10},
            {"product_name": "X", "weight_per_item_kg": 0.5},
            {"product_name": "X", "sales_value": 99},
            {"product_name": "X", "margin": 0.3},
        ):
            with pytest.raises(ForbiddenEmbeddingField):
                build_product_text(forbidden)

    def test_cache_key_is_model_versioned(self) -> None:
        k1 = embedding_key("model-a", "tofu")
        k2 = embedding_key("model-b", "tofu")
        k3 = embedding_key("model-a", "tofu")
        assert k1 != k2  # model change → new key
        assert k1 == k3  # same (model, text) → same key
        cache = InMemoryEmbeddingCache()
        assert cache.get(k1) is None
        cache.set(k1, [0.1, 0.2])
        assert cache.get(k1) == [0.1, 0.2]


# ---------------------------------------------------------------------------
# NEVO gates — precision-first
# ---------------------------------------------------------------------------
class TestNevoGates:
    def test_head_match_accepts_primary_head(self) -> None:
        r = gate_candidate(
            "Ratatouille a l'huile d'olive",
            NevoCandidate("NEVO-RATA", "Ratatouille"),
        )
        assert r.accepted and r.confidence >= 0.9

    def test_rejects_secondary_oil_trap(self) -> None:
        r = gate_candidate(
            "Ratatouille a l'huile d'olive",
            NevoCandidate("X", "Oil olive"),
        )
        assert not r.accepted

    def test_rejects_garlic_parsley_trap(self) -> None:
        for forbidden in ("Garlic raw", "Parsley"):
            r = gate_candidate(
                "Ratatouille ail & persil", NevoCandidate("X", forbidden)
            )
            assert not r.accepted

    def test_no_false_positives_on_trap_fixtures(self) -> None:
        for fname in (
            "nevo_composite_traps.json",
            "nevo_secondary_ingredient_traps.json",
        ):
            cases = load_fixture(_EVAL / "nevo" / fname)
            m = evaluate_nevo(cases, matcher_version=MatcherVersion.V2)
            assert m.false_positive_count == 0, fname
            assert m.forbidden_rejected == m.forbidden_total, fname


# ---------------------------------------------------------------------------
# Evaluator basics
# ---------------------------------------------------------------------------
class TestEvaluatorBasics:
    def test_loads_fixture(self) -> None:
        cases = load_fixture(
            _EVAL / "classification" / "pt" / "pt_dataset_100.json"
        )
        assert len(cases) > 0
        assert "product_name" in cases[0]

    def test_pt_v2_metrics_on_known_fixture(self) -> None:
        cases = load_fixture(
            _EVAL / "classification" / "pt" / "pt_dataset_100.json"
        )
        m = evaluate_classification(
            "pt", cases, pipeline_version=PipelineVersion.V2
        )
        assert m.total == len(cases)
        # The skeleton must classify the core legume dishes correctly +
        # never produce a wrong auto-accept.
        assert m.correct >= 10
        assert m.wrong_accepted == 0

    def test_wwf_v2_strong_on_known_fixture(self) -> None:
        cases = load_fixture(
            _EVAL / "classification" / "wwf" / "wwf_dataset_100.json"
        )
        m = evaluate_classification(
            "wwf", cases, pipeline_version=PipelineVersion.V2
        )
        assert m.accuracy >= 0.9
        assert m.wrong_accepted == 0

    def test_v1_baseline_runs_without_embeddings(self) -> None:
        cases = load_fixture(
            _EVAL / "classification" / "wwf" / "wwf_obvious.json"
        )
        m = evaluate_classification(
            "wwf", cases, pipeline_version=PipelineVersion.V1
        )
        assert m.total == len(cases)

    def test_mismatch_csv_has_documented_columns(self, tmp_path: Path) -> None:
        cases = load_fixture(
            _EVAL / "classification" / "pt" / "pt_dataset_100.json"
        )
        m = evaluate_classification(
            "pt", cases, pipeline_version=PipelineVersion.V2
        )
        out = tmp_path / "mm.csv"
        write_mismatches_csv(out, m.mismatches)
        with out.open(encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        assert header == MISMATCH_CSV_COLUMNS

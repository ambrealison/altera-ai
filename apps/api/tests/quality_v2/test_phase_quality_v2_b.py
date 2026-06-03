"""Phase Quality-V2-B — expanded PT/WWF/NEVO rules, fixtures + gates.

Offline only. Confirms the V2 rules classify the documented cases, the
NEVO gate rejects every trap with zero false positives, V2 beats V1 on
the fixtures (quality gates pass), and production stays on V1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altera_api.classification_v2.evaluation import (
    ClassificationComparison,
    ClassificationMetrics,
    compare_classification,
    evaluate_nevo,
    load_fixture,
    nevo_gates,
    pt_gates,
    wwf_gates,
)
from altera_api.classification_v2.nevo_rules import NevoCandidate, gate_candidate
from altera_api.classification_v2.pt_rules import PT_RULES
from altera_api.classification_v2.rule_engine import ProductInput, RuleEngine
from altera_api.classification_v2.wwf_rules import WWF_RULES
from altera_api.quality_config import MatcherVersion, PipelineVersion

_EVAL = Path(__file__).resolve().parents[2] / "altera_api" / "data" / "eval"
_PT_FIX = _EVAL / "classification" / "pt" / "pt_dataset_v2b.json"
_WWF_FIX = _EVAL / "classification" / "wwf" / "wwf_dataset_v2b.json"
_NEVO_FIX = _EVAL / "nevo" / "nevo_dataset_v2b.json"


def _pt(name: str) -> str | None:
    return RuleEngine(PT_RULES).evaluate(ProductInput(product_name=name)).result.classification.get("pt_group")


def _wwf(name: str) -> dict:
    return RuleEngine(WWF_RULES).evaluate(ProductInput(product_name=name)).result.classification


# ---------------------------------------------------------------------------
# B. PT rules
# ---------------------------------------------------------------------------
class TestPTRules:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Filet de poulet", "animal_core"),
            ("Saumon fume", "animal_core"),
            ("Yaourt nature", "animal_core"),
            ("Cheddar cheese", "animal_core"),
            ("Lentilles corail", "plant_based_core"),
            ("Tofu nature", "plant_based_core"),
            ("Steak vegetal", "plant_based_core"),
            ("Black bean burger", "plant_based_core"),
            ("Wrap falafel houmous", "plant_based_core"),
            ("Chili sin carne", "plant_based_core"),
            ("Curry pois chiches epinards", "plant_based_core"),
            ("Bowl tofu riz legumes", "plant_based_core"),
            ("Pizza jambon", "composite_products"),
            ("Quiche lorraine", "composite_products"),
            ("Sushi saumon avocat", "composite_products"),
            ("Tuna sandwich", "composite_products"),
            ("Croquettes chat poulet", "animal_core"),
            ("Croquettes chien tofu", "plant_based_core"),
            ("Litiere pour chat", "out_of_scope"),
            ("Jouet pour chien", "out_of_scope"),
        ],
    )
    def test_pt_cases(self, name: str, expected: str) -> None:
        assert _pt(name) == expected

    @pytest.mark.parametrize(
        "name,not_expected",
        [
            ("Muesli avoine fruits graines", "plant_based_core"),
            ("Granola", "plant_based_core"),
            ("Chips nature", "plant_based_core"),
            ("Pizza legumes vegan", "composite_products"),
            ("Curry pois chiches epinards", "composite_products"),
            ("Wrap falafel houmous", "composite_products"),
        ],
    )
    def test_pt_negative(self, name: str, not_expected: str) -> None:
        assert _pt(name) != not_expected


# ---------------------------------------------------------------------------
# C. WWF rules
# ---------------------------------------------------------------------------
class TestWWFRules:
    @pytest.mark.parametrize(
        "name,fg,composite,bucket",
        [
            ("Curry poulet riz", "FG1", True, "meat_based"),
            ("Curry pois chiches epinards", "FG1", True, "vegan"),
            ("Sushi saumon avocat", "FG1", True, "seafood_based"),
            ("Tarte epinards feta", "FG1", True, "vegetarian"),
            ("Sardines a l'huile", "FG1", False, None),
            ("Biscuits sables beurre", "FG7", False, None),
            ("Fromage vegetal noix de cajou", "FG2", False, None),
            ("Muesli avoine fruits graines", "FG5", False, None),
            ("Beurre de cacahuete", "FG3", False, None),
            ("Lait d'amande", "FG2", False, None),
            ("Boisson avoine", "FG2", False, None),
            ("Pommes de terre", "FG6", False, None),
            ("Ice cream", "FG7", False, None),
            ("Ratatouille", "FG4", False, None),
        ],
    )
    def test_wwf_cases(self, name, fg, composite, bucket) -> None:
        cls = _wwf(name)
        assert cls.get("wwf_food_group") == fg
        assert bool(cls.get("wwf_is_composite")) == composite
        if bucket is not None:
            assert cls.get("wwf_composite_step1_bucket") == bucket

    def test_wwf_seafood_in_oil_not_fg3(self) -> None:
        assert _wwf("Sardines a l'huile")["wwf_food_group"] != "FG3"

    def test_wwf_biscuit_butter_not_fg3(self) -> None:
        assert _wwf("Biscuits sables beurre")["wwf_food_group"] != "FG3"

    def test_wwf_plant_cheese_stays_fg2(self) -> None:
        assert _wwf("Fromage vegetal noix de cajou")["wwf_food_group"] == "FG2"

    def test_wwf_composite_wins_over_dominant_ingredient(self) -> None:
        # 'Curry poulet riz' → composite (not plain FG1 meat / FG5 rice).
        cls = _wwf("Curry poulet riz")
        assert cls["wwf_is_composite"] is True


# ---------------------------------------------------------------------------
# D. NEVO precision-first matcher
# ---------------------------------------------------------------------------
class TestNevo:
    @pytest.mark.parametrize(
        "product,forbidden",
        [
            ("Ratatouille a l'huile d'olive", "Oil olive"),
            ("Ratatouille ail et persil", "Garlic raw"),
            ("Lait demi-ecreme", "Potatoes mashed with milk"),
            ("Beurre doux", "Apple pie without butter"),
            ("Beurre de cacahuete", "Butter"),
            ("Lasagnes bolognaise", "Pasta white boiled"),
            ("Pois chiches", "Hummus with chickpeas"),
            ("Huile de tournesol", "Salad with oil"),
        ],
    )
    def test_forbidden_rejected(self, product: str, forbidden: str) -> None:
        r = gate_candidate(product, NevoCandidate("X", forbidden))
        assert not r.accepted, f"{forbidden!r} wrongly accepted for {product!r}"

    @pytest.mark.parametrize(
        "product,match",
        [
            ("Tofu nature", "Tofu"),
            ("Pois chiches", "Chickpeas"),
            ("Lentilles corail", "Lentils"),
            ("Beurre de cacahuete", "Peanut butter"),
            ("Lait demi-ecreme", "Milk semi-skimmed"),
            ("Yaourt nature", "Yoghurt"),
        ],
    )
    def test_safe_matches_accepted(self, product: str, match: str) -> None:
        r = gate_candidate(product, NevoCandidate("OK", match))
        assert r.accepted and r.confidence >= 0.90
        assert r.match_type in ("exact", "alias")

    def test_zero_false_positives_on_v2b_traps(self) -> None:
        m = evaluate_nevo(load_fixture(_NEVO_FIX), matcher_version=MatcherVersion.V2)
        assert m.false_positive_count == 0
        assert m.forbidden_rejected == m.forbidden_total


# ---------------------------------------------------------------------------
# G. Quality gates — V2 beats V1; gates pass on the fixtures.
# ---------------------------------------------------------------------------
class TestGates:
    def test_pt_gates_pass(self) -> None:
        cmp = compare_classification("pt", load_fixture(_PT_FIX))
        assert cmp.v2.accuracy >= cmp.v1.accuracy
        assert pt_gates(cmp)["passed"]
        assert len(cmp.improvements) > 0
        assert len(cmp.regressions) == 0

    def test_wwf_gates_pass(self) -> None:
        cmp = compare_classification("wwf", load_fixture(_WWF_FIX))
        assert wwf_gates(cmp)["passed"]
        assert cmp.v2.composite_bucket_correct == cmp.v2.composite_bucket_total

    def test_nevo_gates_pass(self) -> None:
        m = evaluate_nevo(load_fixture(_NEVO_FIX), matcher_version=MatcherVersion.V2)
        assert nevo_gates(m)["passed"]

    def test_gate_fails_when_v2_regresses(self) -> None:
        # A gate must FAIL (so V2 is not activated) when V2 is worse.
        v1 = ClassificationMetrics(task="pt", pipeline_version="v1", total=10, correct=9)
        v2 = ClassificationMetrics(
            task="pt", pipeline_version="v2", total=10, correct=7, wrong_accepted=2
        )
        cmp = ClassificationComparison(task="pt", v1=v1, v2=v2)
        g = pt_gates(cmp)
        assert not g["passed"]
        assert not g["v2_accuracy_ge_v1"]

    def test_gate_fails_on_unknown_readable(self) -> None:
        v1 = ClassificationMetrics(task="pt", pipeline_version="v1", total=10, correct=8)
        v2 = ClassificationMetrics(
            task="pt", pipeline_version="v2", total=10, correct=9, unknown_readable=3
        )
        cmp = ClassificationComparison(task="pt", v1=v1, v2=v2)
        assert not pt_gates(cmp)["passed"]


# ---------------------------------------------------------------------------
# H. V1 remains default / production untouched.
# ---------------------------------------------------------------------------
class TestProductionUntouched:
    def test_defaults_are_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from altera_api.quality_config import (
            classification_pipeline_version,
            embeddings_enabled,
            nevo_matcher_version,
        )

        for var in (
            "ALTERA_CLASSIFICATION_PIPELINE_VERSION",
            "ALTERA_NEVO_MATCHER_VERSION",
            "ALTERA_ENABLE_EMBEDDINGS",
        ):
            monkeypatch.delenv(var, raising=False)
        assert classification_pipeline_version() is PipelineVersion.V1
        assert nevo_matcher_version() is MatcherVersion.V1
        assert embeddings_enabled() is False

    def test_routes_do_not_import_classification_v2(self) -> None:
        src = (
            Path(__file__).resolve().parents[2]
            / "altera_api" / "api" / "routes.py"
        ).read_text(encoding="utf-8")
        assert "classification_v2" not in src

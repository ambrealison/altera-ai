"""Phase Quality-V2-K — expanded NEVO V2 concepts for real FR retailer
products + sharper shadow risk buckets.

All offline (fake provider / seeded reference / stub store). V1 stays
default; embeddings disabled by default; no route imports V2/embeddings.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import compare_nevo_v1_v2 as cli
from altera_api.classification_v2.compare_nevo_v1_v2 import risk_bucket
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    concept_of,
    gate_candidate,
)


def _g(product: str, candidate: str) -> bool:
    return gate_candidate(product, NevoCandidate("X", candidate)).accepted


# ---------------------------------------------------------------------------
# New concepts resolve for the real FR products.
# ---------------------------------------------------------------------------
class TestConcepts:
    @pytest.mark.parametrize(
        "product,concept",
        [
            ("Chips Vinaigre de Cidre", "crisps"),
            ("Quinoa Blanc", "quinoa"),
            ("Margarine Oméga 3", "margarine"),
            ("Crème Fraîche Épaisse", "creme_fraiche"),
            ("Sorbet Framboise", "sorbet"),
            ("Houmous Nature", "hummus"),
            ("Mozzarella di Bufala", "mozzarella"),
            ("Feta AOP", "feta"),
            ("Jambon Supérieur", "ham"),
            ("Blanc de Poulet", "chicken"),
            ("Oeufs Frais", "egg"),
            ("Saumon Fumé", "salmon"),
            ("Miel de Fleurs", "honey"),
            ("Confiture de Fraise", "jam"),
            ("Sucre de Canne", "sugar"),
            ("Pain de Mie Complet", "bread"),
            ("Farine de Blé", "wheat_flour"),
            ("Couscous Moyen", "couscous"),
            ("Boisson Amande", "almond_drink"),
            ("Moutarde de Dijon", "mustard"),
            ("Vinaigre de Cidre", "vinegar"),
        ],
    )
    def test_product_concept(self, product, concept) -> None:
        assert concept_of(product) == concept


# ---------------------------------------------------------------------------
# Safe matches to real NEVO references.
# ---------------------------------------------------------------------------
class TestSafeMatches:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Chips Vinaigre de Cidre", "Chips prepared"),
            ("Quinoa Blanc", "Quinoa cooked"),
            ("Margarine Oméga 3", "Margarine product 60% fat <17 g sat fa unsalted"),
            ("Crème Fraîche Épaisse", "Creme fraiche"),
            ("Sorbet Framboise", "Sorbet"),
            ("Houmous Nature", "Hummus natural"),
            ("Mozzarella di Bufala", "Cheese Mozzarella made from cow's milk"),
            ("Feta AOP", "Cheese Feta"),
            ("Jambon Supérieur", "Ham lean boiled"),
            ("Blanc de Poulet", "Chicken fillet prepared"),
            ("Oeufs Frais", "Egg whole chicken av raw"),
            ("Saumon Fumé", "Salmon smoked"),
            ("Miel de Fleurs", "Honey"),
            ("Confiture de Fraise", "Jam"),
            ("Sucre de Canne", "Sugar granulated"),
            ("Pain de Mie Complet", "Wheat bread white"),
            ("Farine de Blé", "Flour wheat white"),
            ("Couscous Moyen", "Couscous boiled"),
            ("Boisson Amande", "Drink almond unsweetened"),
        ],
    )
    def test_accepts_real_reference(self, product, candidate) -> None:
        assert _g(product, candidate)


# ---------------------------------------------------------------------------
# Traps must stay rejected (incl. the moved-from-dish-noun hummus trap).
# ---------------------------------------------------------------------------
class TestTraps:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Pois Chiches", "Hummus with chickpeas"),       # chickpea ≠ hummus
            ("Corn Flakes", "Corn starch"),                  # head-token trap
            ("Vinaigre de Cidre", "Salad dressing olive oil-vinegar"),  # salad dish
            ("Margarine Oméga 3", "Egg whole chicken fried in margarine"),  # egg head
            ("Sauce Tomate Basilic", "Beans white baked in tomato sauce canned"),
            ("Farine de Maïs", "Flour corn"),                # corn flour ≠ wheat flour
        ],
    )
    def test_rejects_trap(self, product, candidate) -> None:
        assert not _g(product, candidate)

    def test_hummus_trap_still_rejected_after_dishnoun_move(self) -> None:
        # "hummus" is now a concept (not a dish noun); the trap is rejected
        # via the JOINER ("with") head logic.
        from altera_api.classification_v2.nevo_rules import _head_concept

        assert _head_concept("Hummus with chickpeas") == "hummus"
        assert not _g("Pois chiches", "Hummus with chickpeas")
        # …but a hummus PRODUCT matches a plain hummus reference.
        assert _g("Houmous Nature", "Hummus natural")


# ---------------------------------------------------------------------------
# Risk bucket — exact head agreement (no concept) reads as a win.
# ---------------------------------------------------------------------------
class TestRiskBucket:
    def test_head_agreement_no_concept_is_v2_better(self) -> None:
        # "Biscuits Apéritif" / "Biscuits assorted" share the head 'biscuits'
        # but neither maps to a concept → still a safe V2 win, not a
        # potential false positive.
        assert risk_bucket(
            agreement="v2_only", product_name="Biscuits Apéritif Romarin",
            v1_name="", v2_name="Biscuits assorted", v2_matched=True,
            v2_review_required=False,
        ) == "v2_better_than_v1"

    def test_concept_match_is_v2_better(self) -> None:
        assert risk_bucket(
            agreement="v2_only", product_name="Sorbet Framboise", v1_name="",
            v2_name="Sorbet", v2_matched=True, v2_review_required=False,
        ) == "v2_better_than_v1"

    def test_unverifiable_autoaccept_still_flagged(self) -> None:
        # No concept and heads disagree → genuinely unverifiable → inspect.
        assert risk_bucket(
            agreement="v2_only", product_name="Zzz box", v1_name="",
            v2_name="Tofu unprepared", v2_matched=True, v2_review_required=False,
        ) == "v2_potential_false_positive"


# ---------------------------------------------------------------------------
# End-to-end shadow comparison — the 6 high-risk rows reclassify to wins.
# ---------------------------------------------------------------------------
_REFERENCE_FOODS = [
    {"food_name_en": "Chips prepared", "nevo_code": "C1"},
    {"food_name_en": "Quinoa cooked", "nevo_code": "C2"},
    {"food_name_en": "Margarine product 60% fat <17 g sat fa unsalted", "nevo_code": "C3"},
    {"food_name_en": "Creme fraiche", "nevo_code": "C4"},
    {"food_name_en": "Sorbet", "nevo_code": "C5"},
    {"food_name_en": "Biscuits assorted", "nevo_code": "C6"},
    {"food_name_en": "Corn starch", "nevo_code": "C7"},
]

_HIGH_RISK_PRODUCTS = [
    "Chips Vinaigre de Cidre", "Quinoa Blanc", "Margarine Oméga 3",
    "Crème Fraîche Épaisse", "Sorbet Framboise", "Biscuits Apéritif Romarin",
]


class _FakeStore:
    def __init__(self, products):
        self._products = products

    def get_project(self, project_id):
        return object()

    def list_products_for_project(self, project_id):
        return self._products

    def list_nevo_entries(self):
        return []


def _product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        retailer_subcategory=None, ingredients_text=None, labels=(),
        pt_fields=object(),
    )


class TestShadowReclassification:
    def test_six_high_risk_rows_become_wins(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        ref = tmp_path / "ref.json"
        ref.write_text(json.dumps({"references": _REFERENCE_FOODS}))
        store = _FakeStore([_product(n) for n in _HIGH_RISK_PRODUCTS])
        rc = cli.main(
            ["--project-id", str(uuid4()), "--reference-source", "fixture",
             "--reference", str(ref), "--cache-dir", "",
             "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 0
        import csv as _csv

        csv_path = next(tmp_path.glob("nevo_v1_v2_comparison_*.csv"))
        with csv_path.open() as fh:
            rows = list(_csv.DictReader(fh))
        # None of the six are labelled v2_potential_false_positive anymore.
        for r in rows:
            assert r["v2_outcome"] == "match", (r["product_name"], r["v2_outcome"])
            assert r["risk_bucket"] == "v2_better_than_v1", (
                r["product_name"], r["risk_bucket"]
            )
            assert "V2 own-concept match" in r["notes"]
        # Sweet corn product never accepted corn starch.
        quinoa = next(r for r in rows if r["product_name"] == "Quinoa Blanc")
        assert quinoa["v2_reference_name"] == "Quinoa cooked"

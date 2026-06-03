"""Phase Quality-V2-J — NEVO V2 concepts for real FR retailer products.

Adds chocolate / tuna / sweet corn / corn flakes / orange juice / coffee /
tea / soup / tomato sauce concepts so French products resolve to their
English NEVO entries, while ingredient-token traps (beans in tomato sauce,
chicken schnitzel w corn flakes, biscuit cafe, corn starch) stay rejected.
All offline (fake provider / seeded reference / stub store). V1 stays
default; embeddings disabled by default; no route imports V2.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import compare_nevo_v1_v2 as cli
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    concept_of,
    gate_candidate,
)


def _g(product: str, candidate: str) -> bool:
    return gate_candidate(product, NevoCandidate("X", candidate)).accepted


# ---------------------------------------------------------------------------
# Concept extraction for the real FR products.
# ---------------------------------------------------------------------------
class TestConcepts:
    @pytest.mark.parametrize(
        "product,concept",
        [
            ("Chocolat Noir", "chocolate"),
            ("Thon Entier au Naturel", "tuna"),
            ("Maïs Doux Extra Croquant", "sweet_corn"),
            ("Corn Flakes", "corn_flakes"),
            ("Jus d'Orange Pulpe", "orange_juice"),
            ("Café Capsules", "coffee"),
            ("Café Grains", "coffee"),
            ("Thé Noir Earl Grey", "tea"),
            ("Sauce Tomate Basilic", "tomato_sauce"),
            ("Soupe Potiron Châtaigne", "soup"),
            ("Velouté Poireaux Pommes de Terre", "soup"),
        ],
    )
    def test_product_concept(self, product, concept) -> None:
        assert concept_of(product) == concept


# ---------------------------------------------------------------------------
# Safe matches where a real NEVO reference exists.
# ---------------------------------------------------------------------------
class TestSafeMatches:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Chocolat Noir", "Chocolate dark"),
            ("Thon Entier au Naturel", "Tuna in water tinned"),
            ("Maïs Doux Extra Croquant", "Sweetcorn tinned"),
            ("Corn Flakes", "Breakfast cereal Cornflakes"),
            ("Jus d'Orange Pulpe", "Juice orange w pulp"),
            ("Café Capsules", "Coffee prepared"),
            ("Thé Noir Earl Grey", "Tea prepared"),
        ],
    )
    def test_accepts_real_reference(self, product, candidate) -> None:
        assert _g(product, candidate)


# ---------------------------------------------------------------------------
# Ingredient-token traps must stay rejected.
# ---------------------------------------------------------------------------
class TestTraps:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            # tomato sauce is a trailing ingredient of a bean dish
            ("Sauce Tomate Basilic", "Beans white baked in tomato sauce canned"),
            # corn flakes is a coating of a chicken dish
            ("Corn Flakes", "Chicken schnitzel breaded w corn flakes raw"),
            # corn starch is not sweet corn
            ("Maïs Doux Extra Croquant", "Corn starch"),
            # café is inside a biscuit dish name
            ("Café Capsules", "Biscuit Cafe noir"),
            # dark chocolate is not a milk chocolate drink
            ("Chocolat Noir", "Milk chocolate-flavoured full fat"),
            # a soup dish is not auto-accepted
            ("Soupe Potiron Châtaigne", "Soup clear w vegetables"),
            # a real tomato-sauce dish is not auto-accepted either (review/abstain)
            ("Sauce Tomate Basilic", "Sauce tomato ready-to-eat jar"),
        ],
    )
    def test_rejects_trap(self, product, candidate) -> None:
        assert not _g(product, candidate)


# ---------------------------------------------------------------------------
# End-to-end shadow comparison on the smoke products (seeded reference).
# ---------------------------------------------------------------------------
_REFERENCE_FOODS = [
    {"food_name_en": "Chocolate dark", "nevo_code": "432"},
    {"food_name_en": "Milk chocolate-flavoured full fat", "nevo_code": "272"},
    {"food_name_en": "Tuna in water tinned", "nevo_code": "1590"},
    {"food_name_en": "Sweetcorn tinned", "nevo_code": "2900"},
    {"food_name_en": "Corn starch", "nevo_code": "215"},
    {"food_name_en": "Breakfast cereal Cornflakes", "nevo_code": "2081"},
    {"food_name_en": "Chicken schnitzel breaded w corn flakes raw", "nevo_code": "5475"},
    {"food_name_en": "Juice orange w pulp", "nevo_code": "1932"},
    {"food_name_en": "Coffee prepared", "nevo_code": "644"},
    {"food_name_en": "Biscuit Cafe noir", "nevo_code": "9001"},
    {"food_name_en": "Tea prepared", "nevo_code": "645"},
    {"food_name_en": "Beans white baked in tomato sauce canned", "nevo_code": "197"},
    {"food_name_en": "Sauce tomato ready-to-eat jar", "nevo_code": "1524"},
    {"food_name_en": "Soup clear w vegetables", "nevo_code": "759"},
    {"food_name_en": "Peas chick canned", "nevo_code": "3185"},
]

_SMOKE_PRODUCTS = [
    "Chocolat Noir", "Thon Entier au Naturel", "Maïs Doux Extra Croquant",
    "Corn Flakes", "Jus d'Orange Pulpe", "Café Capsules", "Thé Noir Earl Grey",
    "Sauce Tomate Basilic", "Soupe Potiron Châtaigne", "Pois Chiches",
]


class _FakeStore:
    """Read-only stub. V1 has no NEVO entries → V1 abstains everywhere, so
    every V2 win shows up as v2_only / v2_better_than_v1."""

    def __init__(self, products) -> None:
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


class TestShadowComparison:
    def _run(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        ref_path = tmp_path / "ref.json"
        ref_path.write_text(json.dumps({"references": _REFERENCE_FOODS}))
        store = _FakeStore([_product(n) for n in _SMOKE_PRODUCTS])
        rc = cli.main(
            [
                "--project-id", str(uuid4()),
                "--reference-source", "fixture", "--reference", str(ref_path),
                "--cache-dir", "", "--output-dir", str(tmp_path),
            ],
            store=store,
        )
        assert rc == 0
        csv_path = next(tmp_path.glob("nevo_v1_v2_comparison_*.csv"))
        import csv as _csv

        with csv_path.open() as fh:
            rows = {r["product_name"]: r for r in _csv.DictReader(fh)}
        return rows

    def test_v2_matches_obvious_foods(self, tmp_path, monkeypatch) -> None:
        rows = self._run(tmp_path, monkeypatch)
        expected = {
            "Chocolat Noir": "Chocolate dark",
            "Thon Entier au Naturel": "Tuna in water tinned",
            "Maïs Doux Extra Croquant": "Sweetcorn tinned",
            "Corn Flakes": "Breakfast cereal Cornflakes",
            "Jus d'Orange Pulpe": "Juice orange w pulp",
            "Café Capsules": "Coffee prepared",
            "Thé Noir Earl Grey": "Tea prepared",
            "Pois Chiches": "Peas chick canned",
        }
        for product, ref in expected.items():
            r = rows[product]
            assert r["v2_outcome"] == "match", (product, r["v2_outcome"])
            assert r["v2_reference_name"] == ref, (product, r["v2_reference_name"])
            # V1 abstained → V2 produced the right concept → V2 better.
            assert r["risk_bucket"] == "v2_better_than_v1", (product, r["risk_bucket"])

    def test_v2_does_not_accept_ingredient_traps(self, tmp_path, monkeypatch) -> None:
        rows = self._run(tmp_path, monkeypatch)
        # Tomato sauce + soup have only trap/dish references → V2 abstains.
        assert rows["Sauce Tomate Basilic"]["v2_outcome"] == "no_match"
        assert rows["Soupe Potiron Châtaigne"]["v2_outcome"] == "no_match"
        # Sweet corn must not be corn starch; corn flakes must not be chicken.
        assert rows["Maïs Doux Extra Croquant"]["v2_reference_name"] != "Corn starch"
        assert "Chicken" not in rows["Corn Flakes"]["v2_reference_name"]
        # Dark chocolate must not be the milk-chocolate drink.
        assert "Milk" not in rows["Chocolat Noir"]["v2_reference_name"]

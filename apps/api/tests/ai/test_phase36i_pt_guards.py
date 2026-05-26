"""Phase 36I — deterministic PT taxonomy guards.

A 150-product audit (commit 34f0efc) measured ~76–80% precision on
the Protein Tracker classifier with four dominant systematic error
classes. Phase 36I introduces post-classification guards in
``altera_api.ai.pt_guards`` that detect these classes and reroute
the verdict (always to ``needs_review`` — never silently re-accept).

This module encodes the audit cases as regression tests:

  1. Plant_core demoted on preparations (coulis, compote, soupe,
     velouté, gaspacho, mouliné, sauce, huile, jardinière, etc.).
  2. Beverages routed correctly:
       * tea / coffee / soda / water / alcohol → out_of_scope
       * fruit juice / smoothie / nectar / boisson fruitée →
         plant_based_non_core
  3. Sweet bakery / chocolate routed to composite_products.
  4. Animal-source prepared meals routed to composite_products
     instead of animal_core.

Plus non-regression tests asserting good behaviour is preserved:
lentilles / pois chiches / tofu / steak végétal stay
plant_based_core; sardines / saumon brut / yaourt nature stay
animal_core; bare riz / pâtes / tomates / pommes are left untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.ai.pt_guards import GuardOverride, apply_pt_guards
from altera_api.domain.common import ClassificationSource
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)


def _cls(group: ProteinTrackerGroup) -> ProteinTrackerProductClassification:
    """Build a synthetic AI-source classification at confidence 0.9
    so the guard's confidence-clamp (≤ 0.69) is observable."""
    return ProteinTrackerProductClassification(
        product_id=uuid4(),
        pt_group=group,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase36i-test",
        ai_model="phase36i-fake",
        updated_at=datetime.now(UTC),
    )


def _apply(
    name: str, group: ProteinTrackerGroup
) -> tuple[ProteinTrackerGroup, str | None]:
    override = apply_pt_guards(name, _cls(group))
    if override is None:
        return group, None
    return override.new_classification.pt_group, override.rule


# ---------------------------------------------------------------------------
# Guard 1 — plant_core demotion
# ---------------------------------------------------------------------------


class TestPlantCoreDemotion:
    @pytest.mark.parametrize(
        "name",
        [
            "Coulis Mangue",
            "Coulis Figue Bio",
            "Confiture Mangue Sucrée",
            "Compote Pomme Sans Sucre",
            "Épinards Jardinière",
            "Maïs Doux en Conserve",
            "Maïs Doux Huile d'Olive",
            "Velouté de Tomate",
            "Velouté Légumes Anciens",
            "Gaspacho Potiron",
            "Mouliné Poireaux Pommes de Terre",
            "Potage 5 Légumes",
            "Purée de Pommes de Terre",
            "Sauce Tomate Basilic",
            "Huile d'Olive Vierge Extra",
            "Vinaigrette Balsamique",
            "Ketchup Bio",
            "Mayonnaise Allégée",
            "Pesto Vert",
            "Courgettes Râpées",
            "Brocolis en Bouquets",
            "Tomates Cerises",
        ],
    )
    def test_demoted_to_non_core(self, name: str) -> None:
        group, rule = _apply(name, ProteinTrackerGroup.PLANT_BASED_CORE)
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE, (
            f"{name!r}: {group} (rule={rule})"
        )
        assert rule == "plant_core_demoted_preparation_or_simple_veg"

    def test_protein_anchor_overrides_demotion(self) -> None:
        # "Soupe aux pois chiches" matches "soupe" (NOT plant core)
        # AND "pois chiches" (protein anchor) — anchor wins.
        group, rule = _apply(
            "Soupe aux Pois Chiches",
            ProteinTrackerGroup.PLANT_BASED_CORE,
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert rule is None

    def test_clamped_confidence_below_threshold(self) -> None:
        override = apply_pt_guards(
            "Coulis Mangue",
            _cls(ProteinTrackerGroup.PLANT_BASED_CORE),
        )
        assert override is not None
        # Confidence must be ≤ 0.69 so the orchestrator routes to
        # needs_review (auto-accept threshold is 0.70).
        assert override.new_classification.confidence <= Decimal("0.69")


# ---------------------------------------------------------------------------
# Guard 2 — beverages
# ---------------------------------------------------------------------------


class TestBeverageGuards:
    @pytest.mark.parametrize(
        "name",
        [
            "Thé Corsé",
            "Thé Verveine Capsules",
            "Thé Glacé Citron Vert",
            "Café Noisette",
            "Café Arabica Moulu",
            "Tisane Bio Sommeil",
            "Infusion Camomille",
            "Limonade Artisanale Orange",
            "Limonade Artisanale Multifruits",
            "Soda Cola Zéro",
            "Eau Minérale Plate",
            "Eau Gazeuse Citron",
            "Bière Blonde 33cl",
            "Vin Rouge Bordeaux",
            "Whisky Single Malt",
        ],
    )
    def test_routed_to_out_of_scope(self, name: str) -> None:
        # Model returned plant_based_non_core (a common error on
        # tea/coffee/soda); the beverage_out_of_scope guard must fire.
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule == "beverage_out_of_scope"

    @pytest.mark.parametrize(
        "name",
        [
            "Smoothie Pêche",
            "Smoothie Fruits Rouges",
            "Pur Jus d'Orange Pressée",
            "Boisson Fruitée Fruits Rouges Pur Jus",
            "Nectar de Pomme",
            "Jus de Tomate Bio",
            "Jus Multifruits Sans Sucre",
        ],
    )
    def test_fruit_drink_promoted_from_unknown(self, name: str) -> None:
        # Model returned ``unknown`` on a fruit-drink name; the
        # fruit_drink_non_core guard must promote to non_core.
        group, rule = _apply(name, ProteinTrackerGroup.UNKNOWN)
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert rule == "fruit_drink_non_core"

    def test_already_out_of_scope_stays_out_of_scope(self) -> None:
        # Eau already labelled out_of_scope by the model — no fire.
        group, rule = _apply(
            "Eau Minérale Naturelle",
            ProteinTrackerGroup.OUT_OF_SCOPE,
        )
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule is None


# ---------------------------------------------------------------------------
# Guard 3 — sweet bakery / chocolate composites
# ---------------------------------------------------------------------------


class TestBakeryComposite:
    @pytest.mark.parametrize(
        "name",
        [
            "Sablés Pépites Chocolat",
            "Sablés Noisette",
            "Croissants Maïs",
            "Croissants au Beurre",
            "Pain au Chocolat",
            "Pain aux Raisins",
            "Biscuits Petit Beurre",
            "Cookies Pépites",
            "Madeleines Bio",
            "Financiers Amandes",
            "Brownie Cacao",
            "Tablette Lait",
            "Tablette de Lait",
            "Chocolat au Lait Praliné",
            "Macarons Vanille",
            "Spéculoos Original",
        ],
    )
    def test_routed_to_composite(self, name: str) -> None:
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        assert rule == "bakery_composite"


# ---------------------------------------------------------------------------
# Guard 4 — animal prepared meals
# ---------------------------------------------------------------------------


class TestAnimalPreparedMeal:
    @pytest.mark.parametrize(
        "name",
        [
            "Curry Saumon Riz Basmati",
            "Parmentier Saumon Épinards",
            "Poêlée Saumon Légumes",
            "Soupe Poulet Légumes",
            "Salade Poulet César",
            "Cassoulet Citron Provençale",
            "Cassoulet Provençale",
            "Lasagnes Bolognaise",
            "Ratatouille Poulet",
            "Risotto Crevettes",
            "Pizza Jambon Champignons",
            "Wrap Poulet Crudités",
            "Gratin Saumon Brocolis",
            "Quiche Lorraine Lardons",
        ],
    )
    def test_routed_to_composite(self, name: str) -> None:
        group, rule = _apply(name, ProteinTrackerGroup.ANIMAL_CORE)
        assert group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        assert rule == "animal_prepared_meal_composite"

    def test_simple_animal_food_unchanged(self) -> None:
        # Plain animal-source products must stay animal_core — they
        # carry no prepared-dish marker.
        for name in [
            "Filets de Saumon Atlantique",
            "Yaourt Nature 0% MG",
            "Œufs Bio x6",
            "Blanc de Poulet Tranché",
            "Beurre Doux 250g",
            "Sardines à l'Huile",  # NOTE: "huile" is a not-plant-core
            # pattern but the input is animal_core not plant_core; the
            # plant_core guard doesn't fire on animal_core inputs.
            "Camembert Affiné",
        ]:
            group, rule = _apply(name, ProteinTrackerGroup.ANIMAL_CORE)
            assert group is ProteinTrackerGroup.ANIMAL_CORE, (
                f"{name!r} was reclassified by rule={rule}"
            )


# ---------------------------------------------------------------------------
# Non-regression: good cases stay unchanged
# ---------------------------------------------------------------------------


class TestNonRegressionGoodCases:
    @pytest.mark.parametrize(
        ("name", "group"),
        [
            # plant_based_core — protein-rich plants
            ("Lentilles Vertes du Puy", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Pois Chiches Cuits", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Haricots Rouges en Conserve", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Haricots Blancs Bio", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Tofu Nature Bio", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Tempeh Original", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Steak Végétal Soja & Blé", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Burger Végétal Pois Chiches", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Noix de Cajou Grillées", ProteinTrackerGroup.PLANT_BASED_CORE),
            ("Amandes Émondées", ProteinTrackerGroup.PLANT_BASED_CORE),
            # animal_core — simple animal foods (no prepared-dish marker)
            ("Sardines à la Tomate", ProteinTrackerGroup.ANIMAL_CORE),
            ("Saumon Filet Nature", ProteinTrackerGroup.ANIMAL_CORE),
            ("Yaourt Grec Nature", ProteinTrackerGroup.ANIMAL_CORE),
            ("Fromage Blanc 0%", ProteinTrackerGroup.ANIMAL_CORE),
            # plant_based_non_core — bare staples
            ("Pommes Golden 1.5kg", ProteinTrackerGroup.PLANT_BASED_NON_CORE),
            ("Riz Basmati Long Grain", ProteinTrackerGroup.PLANT_BASED_NON_CORE),
            ("Pâtes Spaghetti Bio", ProteinTrackerGroup.PLANT_BASED_NON_CORE),
        ],
    )
    def test_unchanged(
        self, name: str, group: ProteinTrackerGroup
    ) -> None:
        result_group, rule = _apply(name, group)
        assert result_group is group, (
            f"{name!r} ({group}) was reclassified to {result_group} "
            f"by rule={rule}"
        )
        assert rule is None


# ---------------------------------------------------------------------------
# Audit-batch summary: precision target on the brief's listed cases
# ---------------------------------------------------------------------------


# Each entry: (product_name, model_verdict, expected_group_after_guard).
# When ``expected_group_after_guard`` differs from ``model_verdict``,
# a guard MUST have fired. When equal, the guard MUST NOT fire.
_AUDIT: tuple[tuple[str, ProteinTrackerGroup, ProteinTrackerGroup], ...] = (
    # Plant-core demotions
    ("Coulis Mangue", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Coulis Figue", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Confiture Mangue", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Velouté Tomate", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Gaspacho Potiron", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Mouliné Poireaux Pommes de Terre",
     ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Épinards Jardinière", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Maïs Doux Huile d'Olive", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    # Beverages
    ("Thé Corsé", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Thé Verveine Capsules", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Limonade Artisanale Orange", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Limonade Artisanale Multifruits",
     ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Café Noisette", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Thé Glacé Citron Vert", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.OUT_OF_SCOPE),
    ("Smoothie Pêche", ProteinTrackerGroup.UNKNOWN,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    ("Boisson Fruitée Fruits Rouges Pur Jus", ProteinTrackerGroup.UNKNOWN,
     ProteinTrackerGroup.PLANT_BASED_NON_CORE),
    # Bakery / chocolate composites
    ("Sablés Pépites Chocolat", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    ("Croissants Maïs", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    ("Sablés Noisette", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    ("Tablette Lait", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    # Animal prepared meals
    ("Poêlée Saumon Légumes", ProteinTrackerGroup.ANIMAL_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    ("Cassoulet Provençale", ProteinTrackerGroup.ANIMAL_CORE,
     ProteinTrackerGroup.COMPOSITE_PRODUCTS),
    # Non-regression — model was already correct
    ("Lentilles Vertes du Puy", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_CORE),
    ("Tofu Nature Bio", ProteinTrackerGroup.PLANT_BASED_CORE,
     ProteinTrackerGroup.PLANT_BASED_CORE),
    ("Yaourt Nature 0% MG", ProteinTrackerGroup.ANIMAL_CORE,
     ProteinTrackerGroup.ANIMAL_CORE),
)


class TestAuditPrecision:
    def test_every_audit_case_lands_on_expected_group(self) -> None:
        mismatches: list[str] = []
        for name, given, expected in _AUDIT:
            result, _rule = _apply(name, given)
            if result is not expected:
                mismatches.append(
                    f"{name!r} (given {given}): got {result}, "
                    f"expected {expected}"
                )
        assert not mismatches, "\n".join(mismatches)

    def test_override_returns_typed_descriptor(self) -> None:
        # Sanity check on the public return type.
        override = apply_pt_guards(
            "Coulis Mangue",
            _cls(ProteinTrackerGroup.PLANT_BASED_CORE),
        )
        assert isinstance(override, GuardOverride)
        assert (
            override.new_classification.pt_group
            is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )

"""Phase WWF-D — deterministic WWF guards.

Mirrors ``tests/ai/test_phase36i_pt_guards.py`` for the WWF
methodology. The audit fixture
(``altera_api/data/audit/wwf_obvious_fixture.json``) drives a 110-case
strict-equality regression in
``scripts/evaluate_wwf_classification.py``; this test file pins the
top-level public behaviours of ``altera_api.ai.wwf_guards``:

  1. Scope exclusions (beverages, condiments, herbs/spices,
     bouillon, baby food, novel proteins, household, hygiene,
     pet accessories) → ``out_of_scope``.
  2. Dairy / plant-milk beverages stay in FG2 (NOT excluded as
     beverages).
  3. Composite dishes set ``wwf_is_composite=true`` + the right
     Step 1 bucket (meat → seafood → vegetarian → vegan).
  4. FG7 snacks beat FG2 dairy and FG3 fats (Chocolat au Lait /
     Croissants au Beurre / Sorbet Framboise / Confiture).
  5. FG1 priority beats FG3 plant fat (Sardines à l'Huile →
     seafood, not plant_fat).
  6. FG3 animal fat vs plant fat (butter is FG3, NOT FG2).
  7. FG6 tuber vs FG7 fries (Pommes de Terre → FG6; Frites →
     FG7 plant_snack).
  8. FG5 whole vs refined grains.
  9. Pet food in-scope (Croquettes Chien Bœuf → composite
     meat_based; Croquettes Tofu → FG1 alt_protein); pet
     accessories OOS.
 10. Confidence is clamped to ≤ 0.69 whenever a guard fires.
 11. Unicode ligatures (œ, æ) and French accents are normalised.
 12. Sans-sucre exclusion (Compote Pomme Sans Sucre stays FG4).
 13. Self-evident animal/seafood/vegetarian composites
     (Cassoulet, Lasagnes Bolognaise, Pizza Margherita,
     Paella Fruits de Mer).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.ai.wwf_guards import (
    WWFGuardOverride,
    apply_wwf_guards,
    classify_wwf_readable_fallback,
)
from altera_api.domain.common import ClassificationSource
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)


def _seed(
    food_group: WWFFoodGroup = WWFFoodGroup.UNKNOWN,
    *,
    is_composite: bool = False,
    fg1: WWFFG1Subgroup | None = None,
    fg2: WWFFG2Subgroup | None = None,
    fg3: WWFFG3Subgroup | None = None,
    fg5: WWFFG5GrainKind | None = None,
    fg7: WWFFG7SnackKind | None = None,
    bucket: WWFCompositeStep1Bucket | None = None,
) -> WWFProductClassification:
    """Build a synthetic AI-source WWF classification at confidence
    0.9 so the guard's confidence ceiling (≤ 0.69) is observable."""
    return WWFProductClassification(
        product_id=uuid4(),
        wwf_food_group=food_group,
        wwf_is_composite=is_composite,
        fg1_subgroup=fg1,
        fg2_subgroup=fg2,
        fg3_subgroup=fg3,
        fg5_grain_kind=fg5,
        fg7_snack_kind=fg7,
        composite_step1_bucket=bucket,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase-wwf-d-test",
        ai_model="phase-wwf-d-fake",
        updated_at=datetime.now(UTC),
    )


def _apply(
    name: str,
    *,
    food_group: WWFFoodGroup = WWFFoodGroup.UNKNOWN,
    **kw: object,
) -> WWFGuardOverride | None:
    return apply_wwf_guards(name, _seed(food_group, **kw))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Guard 1 — methodology exclusions
# ---------------------------------------------------------------------------


class TestMethodologyExclusions:
    @pytest.mark.parametrize(
        "name",
        [
            "Coca-Cola Zero",
            "Eau Minérale Plate",
            "Bière Blonde 33cl",
            "Vin Rouge 75cl",
            "Whisky 1L",
            "Jus d'Orange Pur Jus",
            "Smoothie Mangue",
            "Café Moulu Arabica",
            "Thé Vert Bio",
            "Ketchup Bio",
            "Vinaigrette Balsamique",
            "Mayonnaise Allégée",
            "Moutarde de Dijon",
            "Sel de Guérande",
            "Poivre Noir Moulu",
            "Herbes de Provence",
            "Levure Chimique",
            "Bouillon Cube Volaille",
            "Lait Infantile 1er Âge",
            "Petit Pot Bébé Carotte",
            "Insectes Comestibles",
            "Lessive Liquide",
            "Litière Chat",
            "Jouet Chien",
        ],
    )
    def test_routes_to_out_of_scope(self, name: str) -> None:
        override = _apply(name)
        assert override is not None, f"expected guard for {name!r}"
        assert (
            override.new_classification.wwf_food_group
            is WWFFoodGroup.OUT_OF_SCOPE
        )
        assert override.new_classification.confidence <= Decimal("0.69")


# ---------------------------------------------------------------------------
# Guard 2 — dairy / plant-milk beverages stay FG2
# ---------------------------------------------------------------------------


class TestDairyBeveragesStayFG2:
    @pytest.mark.parametrize(
        "name,expected_subgroup",
        [
            ("Lait Demi-Écrémé UHT", WWFFG2Subgroup.OTHER_DAIRY_ANIMAL),
            ("Lait Entier Bio", WWFFG2Subgroup.OTHER_DAIRY_ANIMAL),
            ("Yaourt à Boire Vanille", WWFFG2Subgroup.OTHER_DAIRY_ANIMAL),
            ("Boisson Avoine Bio", WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT),
            ("Lait d'Amande Sans Sucre", WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT),
            ("Soy Milk Original", WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT),
            ("Boisson Soja Calcium", WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT),
        ],
    )
    def test_dairy_beverage_to_fg2(
        self, name: str, expected_subgroup: WWFFG2Subgroup
    ) -> None:
        override = _apply(name)
        assert override is not None, f"expected guard for {name!r}"
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG2
        assert cls.fg2_subgroup is expected_subgroup


# ---------------------------------------------------------------------------
# Guard 3 — composite dishes
# ---------------------------------------------------------------------------


class TestCompositeDishes:
    @pytest.mark.parametrize(
        "name,bucket",
        [
            # Meat-based.
            ("Pizza Jambon", WWFCompositeStep1Bucket.MEAT_BASED),
            ("Lasagnes Bolognaise", WWFCompositeStep1Bucket.MEAT_BASED),
            ("Cassoulet Provençale", WWFCompositeStep1Bucket.MEAT_BASED),
            ("Hachis Parmentier", WWFCompositeStep1Bucket.MEAT_BASED),
            ("Tartiflette Savoyarde", WWFCompositeStep1Bucket.MEAT_BASED),
            ("Choucroute Garnie", WWFCompositeStep1Bucket.MEAT_BASED),
            # Seafood.
            ("Paella Fruits de Mer", WWFCompositeStep1Bucket.SEAFOOD_BASED),
            ("Curry Saumon Riz", WWFCompositeStep1Bucket.SEAFOOD_BASED),
            ("Risotto aux Crevettes", WWFCompositeStep1Bucket.SEAFOOD_BASED),
            # Vegetarian (cheese / cream / egg).
            ("Pizza Margherita", WWFCompositeStep1Bucket.VEGETARIAN),
            ("Quiche Lorraine Fromage", WWFCompositeStep1Bucket.VEGETARIAN),
            ("Épinards à la Crème", WWFCompositeStep1Bucket.VEGETARIAN),
            # Vegan.
            ("Curry Légumes Coco", WWFCompositeStep1Bucket.VEGAN),
            ("Buddha Bowl Quinoa", WWFCompositeStep1Bucket.VEGAN),
        ],
    )
    def test_composite_bucket(
        self, name: str, bucket: WWFCompositeStep1Bucket
    ) -> None:
        override = _apply(name)
        assert override is not None, f"expected composite guard for {name!r}"
        cls = override.new_classification
        assert cls.wwf_is_composite is True
        assert cls.composite_step1_bucket is bucket


# ---------------------------------------------------------------------------
# Guard 4 — FG7 snacks beat FG2 / FG3 / FG4
# ---------------------------------------------------------------------------


class TestFG7SnacksWin:
    @pytest.mark.parametrize(
        "name,expected_kind",
        [
            ("Chocolat au Lait Bio", WWFFG7SnackKind.ANIMAL_BASED_SNACK),
            ("Tablette Lait Noisettes", WWFFG7SnackKind.ANIMAL_BASED_SNACK),
            ("Croissants au Beurre", WWFFG7SnackKind.ANIMAL_BASED_SNACK),
            ("Pain au Chocolat", WWFFG7SnackKind.ANIMAL_BASED_SNACK),
            ("Crème Glacée Vanille", WWFFG7SnackKind.ANIMAL_BASED_SNACK),
            ("Sorbet Framboise", WWFFG7SnackKind.PLANT_BASED_SNACK),
            ("Confiture Abricot", WWFFG7SnackKind.PLANT_BASED_SNACK),
            ("Miel de Lavande", WWFFG7SnackKind.PLANT_BASED_SNACK),
            ("Tortilla Chips Sel", WWFFG7SnackKind.PLANT_BASED_SNACK),
        ],
    )
    def test_fg7_beats_alternatives(
        self, name: str, expected_kind: WWFFG7SnackKind
    ) -> None:
        override = _apply(name)
        assert override is not None, f"expected FG7 guard for {name!r}"
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG7
        assert cls.fg7_snack_kind is expected_kind


# ---------------------------------------------------------------------------
# Guard 5 — FG1 priority beats FG3 plant fat
# ---------------------------------------------------------------------------


class TestFG1PriorityOverFG3:
    @pytest.mark.parametrize(
        "name,expected_subgroup",
        [
            ("Sardines à l'Huile d'Olive", WWFFG1Subgroup.SEAFOOD),
            ("Thon à l'Huile Bio", WWFFG1Subgroup.SEAFOOD),
            ("Anchois à l'Huile", WWFFG1Subgroup.SEAFOOD),
        ],
    )
    def test_fg1_beats_huile(
        self, name: str, expected_subgroup: WWFFG1Subgroup
    ) -> None:
        override = _apply(name)
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert cls.fg1_subgroup is expected_subgroup


# ---------------------------------------------------------------------------
# Guard 6 — FG3 fat split (butter is FG3, not FG2)
# ---------------------------------------------------------------------------


class TestFG3FatSplit:
    @pytest.mark.parametrize(
        "name,expected_subgroup",
        [
            ("Beurre Doux Bio", WWFFG3Subgroup.ANIMAL_BASED_FAT),
            ("Ghee Indien", WWFFG3Subgroup.ANIMAL_BASED_FAT),
            ("Saindoux", WWFFG3Subgroup.ANIMAL_BASED_FAT),
            ("Huile d'Olive Vierge Extra", WWFFG3Subgroup.PLANT_BASED_FAT),
            ("Huile de Tournesol", WWFFG3Subgroup.PLANT_BASED_FAT),
            ("Margarine Végétale", WWFFG3Subgroup.PLANT_BASED_FAT),
        ],
    )
    def test_fg3_split(
        self, name: str, expected_subgroup: WWFFG3Subgroup
    ) -> None:
        override = _apply(name)
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG3
        assert cls.fg3_subgroup is expected_subgroup


# ---------------------------------------------------------------------------
# Guard 7 — FG6 tuber vs FG7 fries
# ---------------------------------------------------------------------------


class TestFG6vsFG7Fries:
    def test_pomme_de_terre_is_fg6(self) -> None:
        override = _apply("Pommes de Terre Charlotte")
        assert override is not None
        assert override.new_classification.wwf_food_group is WWFFoodGroup.FG6

    def test_frites_is_fg7_plant_snack(self) -> None:
        override = _apply("Frites Surgelées")
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG7
        assert cls.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK

    def test_potato_chips_is_fg7(self) -> None:
        override = _apply("Chips Sel & Vinaigre")
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG7
        assert cls.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK


# ---------------------------------------------------------------------------
# Guard 8 — FG5 grain split
# ---------------------------------------------------------------------------


class TestFG5GrainSplit:
    @pytest.mark.parametrize(
        "name,expected_kind",
        [
            ("Riz Complet Bio", WWFFG5GrainKind.WHOLE_GRAIN),
            ("Pain Complet", WWFFG5GrainKind.WHOLE_GRAIN),
            ("Pâtes Complètes", WWFFG5GrainKind.WHOLE_GRAIN),
            ("Quinoa Bio", WWFFG5GrainKind.WHOLE_GRAIN),
            ("Avoine Flocons", WWFFG5GrainKind.WHOLE_GRAIN),
            ("Riz Basmati", WWFFG5GrainKind.REFINED_GRAIN),
            ("Spaghetti", WWFFG5GrainKind.REFINED_GRAIN),
            ("Baguette Tradition", WWFFG5GrainKind.REFINED_GRAIN),
            ("Cornflakes", WWFFG5GrainKind.REFINED_GRAIN),
        ],
    )
    def test_fg5_split(
        self, name: str, expected_kind: WWFFG5GrainKind
    ) -> None:
        override = _apply(name)
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG5
        assert cls.fg5_grain_kind is expected_kind


# ---------------------------------------------------------------------------
# Guard 9 — pet food in-scope, pet accessories OOS
# ---------------------------------------------------------------------------


class TestPetFoodInScope:
    def test_petfood_animal_to_composite_meat_based(self) -> None:
        override = _apply("Croquettes Chien Bœuf")
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert cls.fg1_subgroup is WWFFG1Subgroup.RED_MEAT
        assert cls.wwf_is_composite is True
        assert cls.composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_petfood_seafood_to_composite_seafood_based(self) -> None:
        override = _apply("Pâtée Chat Saumon")
        assert override is not None
        cls = override.new_classification
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert cls.fg1_subgroup is WWFFG1Subgroup.SEAFOOD
        assert cls.wwf_is_composite is True
        assert (
            cls.composite_step1_bucket
            is WWFCompositeStep1Bucket.SEAFOOD_BASED
        )

    def test_pet_accessory_to_out_of_scope(self) -> None:
        override = _apply("Litière Chat Agglomérante")
        assert override is not None
        assert (
            override.new_classification.wwf_food_group
            is WWFFoodGroup.OUT_OF_SCOPE
        )


# ---------------------------------------------------------------------------
# Guard 10 — confidence ceiling
# ---------------------------------------------------------------------------


class TestConfidenceCeiling:
    def test_confidence_clamped(self) -> None:
        override = _apply("Coca-Cola Zero")
        assert override is not None
        assert override.new_classification.confidence <= Decimal("0.69")

    def test_confidence_preserves_low_values(self) -> None:
        seed = _seed(WWFFoodGroup.UNKNOWN)
        # Force confidence below ceiling — guard must NOT raise it.
        low_seed = seed.model_copy(update={"confidence": Decimal("0.30")})
        override = apply_wwf_guards("Coca-Cola Zero", low_seed)
        assert override is not None
        assert override.new_classification.confidence == Decimal("0.30")


# ---------------------------------------------------------------------------
# Guard 11 — ligature / accent normalisation
# ---------------------------------------------------------------------------


class TestNormalisation:
    @pytest.mark.parametrize(
        "name,expected_group",
        [
            ("Œufs Frais Bio", WWFFoodGroup.FG1),  # œ → oe
            ("Bœuf Haché 5% MG", WWFFoodGroup.FG1),  # œ → oe
            ("Pâtée Chien Bœuf", WWFFoodGroup.FG1),  # œ + composite
        ],
    )
    def test_ligature_normalised(
        self, name: str, expected_group: WWFFoodGroup
    ) -> None:
        override = _apply(name)
        assert override is not None
        assert override.new_classification.wwf_food_group is expected_group


# ---------------------------------------------------------------------------
# Guard 12 — sans sucre / unsweetened exclusion (FG4, not FG7)
# ---------------------------------------------------------------------------


class TestSansSucreException:
    @pytest.mark.parametrize(
        "name",
        [
            "Compote Pomme Sans Sucre",
            "Compote Poire Sans Sucres Ajoutés",
            "Applesauce Unsweetened",
        ],
    )
    def test_unsweetened_compote_is_fg4(self, name: str) -> None:
        override = _apply(name)
        assert override is not None, f"expected FG4 guard for {name!r}"
        assert override.new_classification.wwf_food_group is WWFFoodGroup.FG4


# ---------------------------------------------------------------------------
# Guard 13 — readable-name fallback
# ---------------------------------------------------------------------------


class TestReadableFallback:
    def test_readable_fallback_returns_fg1_for_protein_name(self) -> None:
        result = classify_wwf_readable_fallback("Lentilles Vertes du Puy")
        assert result is not None
        food_group, is_composite, fg1, _fg2, _fg3, _fg5, _fg7, _bucket, rule = (
            result
        )
        assert food_group is WWFFoodGroup.FG1
        assert fg1 is WWFFG1Subgroup.LEGUMES
        assert is_composite is False
        assert rule.startswith("wwf_readable_fallback_")

    def test_readable_fallback_returns_composite_for_pizza(self) -> None:
        result = classify_wwf_readable_fallback("Pizza Jambon Fromage")
        assert result is not None
        _fg, is_composite, _fg1, _fg2, _fg3, _fg5, _fg7, bucket, _rule = result
        assert is_composite is True
        assert bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_readable_fallback_returns_none_for_blank(self) -> None:
        assert classify_wwf_readable_fallback("   ") is None
        assert classify_wwf_readable_fallback("") is None


# ---------------------------------------------------------------------------
# Non-regression — known-good cases must NOT trigger a guard
# ---------------------------------------------------------------------------


class TestNoRegression:
    def test_already_correct_lentils_no_override(self) -> None:
        override = apply_wwf_guards(
            "Lentilles Vertes du Puy",
            _seed(
                WWFFoodGroup.FG1,
                fg1=WWFFG1Subgroup.LEGUMES,
            ),
        )
        assert override is None

    def test_already_correct_butter_no_override(self) -> None:
        override = apply_wwf_guards(
            "Beurre Doux",
            _seed(
                WWFFoodGroup.FG3,
                fg3=WWFFG3Subgroup.ANIMAL_BASED_FAT,
            ),
        )
        assert override is None

    def test_already_correct_cheese_no_override(self) -> None:
        override = apply_wwf_guards(
            "Camembert AOP",
            _seed(
                WWFFoodGroup.FG2,
                fg2=WWFFG2Subgroup.CHEESE,
            ),
        )
        assert override is None

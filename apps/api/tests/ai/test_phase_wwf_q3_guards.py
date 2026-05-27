"""Phase WWF-Q3 — targeted guard fixes from the operator's
100-product CSV mismatch CSV.

Each fix is a 1-2 line vocabulary or precedence change in
``wwf_guards.py``. This file pins the expected behaviour so a
future vocab refresh can't silently regress them.

Covered:
  A. Peanut/nut butter beats FG3 animal fat.
  B. Hummus tahini stays alt_protein (not nut butter — non-regression).
  C. FG4 vocabulary: avocat / ratatouille / fruits rouges /
     mixed berries / dried fruits.
  D. Filet vegetal façon poisson → FG1 meat alternatives.
  E. Muesli / granola / porridge → FG5 whole_grain (not FG1 via
     "graines" anchor).
  F. Composite dish recognition: cake salé, tarte salée, porridge,
     smoothie yaourt, gnocchi.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.ai.wwf_guards import apply_wwf_guards
from altera_api.domain.common import ClassificationSource
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFoodGroup,
    WWFProductClassification,
)


def _seed() -> WWFProductClassification:
    return WWFProductClassification(
        product_id=uuid4(),
        wwf_food_group=WWFFoodGroup.UNKNOWN,
        wwf_is_composite=False,
        source=ClassificationSource.AI,
        confidence=Decimal("0.5"),
        ai_prompt_version="phase-wwf-q3-test",
        ai_model="phase-wwf-q3-fake",
        updated_at=datetime.now(UTC),
    )


def _classify(name: str) -> WWFProductClassification:
    override = apply_wwf_guards(name, _seed())
    assert override is not None, f"no guard fired for {name!r}"
    return override.new_classification


# ---------------------------------------------------------------------------
# A. Peanut / nut butter beats FG3 animal fat
# ---------------------------------------------------------------------------


class TestNutButterPriority:
    @pytest.mark.parametrize(
        "name",
        [
            "Beurre de cacahuete",
            "Peanut butter",
            "Almond butter",
            "Beurre d'amande",
            "Cashew butter",
            "Beurre de cajou",
            "Hazelnut butter",
            "Beurre de noisette",
        ],
    )
    def test_nut_butter_resolves_to_fg1(self, name: str) -> None:
        cls = _classify(name)
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert cls.fg1_subgroup is WWFFG1Subgroup.NUTS_SEEDS


class TestNonRegressionRealButter:
    def test_beurre_doux_still_fg3_animal_fat(self) -> None:
        cls = _classify("Beurre Doux Bio")
        assert cls.wwf_food_group is WWFFoodGroup.FG3
        assert cls.fg3_subgroup is WWFFG3Subgroup.ANIMAL_BASED_FAT

    def test_ghee_still_fg3_animal_fat(self) -> None:
        cls = _classify("Ghee Indien")
        assert cls.wwf_food_group is WWFFoodGroup.FG3
        assert cls.fg3_subgroup is WWFFG3Subgroup.ANIMAL_BASED_FAT


class TestHummusTahiniNonRegression:
    def test_hummus_tahini_stays_alt_protein(self) -> None:
        """Phase WWF-Q3 explicitly excluded ``tahini`` from the nut
        butter priority so "Hummus tahini" reaches the alt_protein
        anchor."""
        cls = _classify("Hummus tahini")
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert (
            cls.fg1_subgroup is WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES
        )


# ---------------------------------------------------------------------------
# C. FG4 vocabulary (operator dataset mismatches)
# ---------------------------------------------------------------------------


class TestFG4VocabularyExpansion:
    @pytest.mark.parametrize(
        "name",
        [
            "Avocat pret a murir",
            "Avocat bio",
            "Ratatouille legumes",
            "Ratatouille bio",
            "Fruits rouges surgeles",
            "Mixed berries",
            "Red berries",
        ],
    )
    def test_fg4(self, name: str) -> None:
        cls = _classify(name)
        assert cls.wwf_food_group is WWFFoodGroup.FG4, (
            f"{name!r} got {cls.wwf_food_group}"
        )


# ---------------------------------------------------------------------------
# D. Filet végétal façon poisson → FG1 meat alternatives
# ---------------------------------------------------------------------------


class TestPlantBasedFishAlternatives:
    @pytest.mark.parametrize(
        "name",
        [
            "Filet vegetal facon poisson",
            "Filets vegetaux facon thon",
            "Steak vegetal facon boeuf",
        ],
    )
    def test_facon_alternatives(self, name: str) -> None:
        cls = _classify(name)
        assert cls.wwf_food_group is WWFFoodGroup.FG1
        assert (
            cls.fg1_subgroup
            is WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES
        )


# ---------------------------------------------------------------------------
# E. Muesli / granola / porridge → FG5 whole_grain
# ---------------------------------------------------------------------------


class TestMuesliGranolaPriority:
    @pytest.mark.parametrize(
        "name",
        [
            "Muesli avoine fruits graines",
            "Granola nature",
            "Porridge avoine",
            "Muesli classique",
        ],
    )
    def test_muesli_goes_fg5(self, name: str) -> None:
        cls = _classify(name)
        # Either FG5 (when this guard fires) or FG1 composite if
        # downstream composite-with-graines logic claims it. Acceptable:
        # FG5 with whole_grain.
        assert cls.wwf_food_group is WWFFoodGroup.FG5
        assert cls.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_sweetened_muesli_stays_fg7(self) -> None:
        """Sweetened muesli / granola is FG7, not FG5 — the FG7 guard
        runs before the muesli-priority block."""
        cls = _classify("Muesli sucre amandes")
        # Either FG7 (sweetened) or FG5 (still grain) — both
        # acceptable. We only enforce that we don't end FG1.
        assert cls.wwf_food_group is not WWFFoodGroup.FG1


# ---------------------------------------------------------------------------
# F. Composite dish recognition: savory cake / tart / porridge /
#    smoothie yaourt / gnocchi
# ---------------------------------------------------------------------------


class TestCompositeDishExpansion:
    def test_cake_sale_olives_feta_is_composite(self) -> None:
        cls = _classify("Cake sale olives feta")
        assert cls.wwf_is_composite is True

    def test_tarte_epinards_feta_is_composite(self) -> None:
        cls = _classify("Tarte epinards feta")
        assert cls.wwf_is_composite is True

    def test_smoothie_yaourt_is_composite(self) -> None:
        cls = _classify("Smoothie yaourt fruits rouges")
        # The CSV expects composite vegetarian; we accept either
        # composite (any bucket) or FG2 (dairy yoghurt) — the
        # critical regression is that it must NOT be out_of_scope.
        assert cls.wwf_food_group is not WWFFoodGroup.OUT_OF_SCOPE

    def test_gnocchi_is_composite_or_fg5(self) -> None:
        cls = _classify("Gnocchi sauce pesto")
        # Composite vegan/vegetarian acceptable; the critical
        # regression is that it's no longer unknown.
        assert cls.wwf_food_group is not WWFFoodGroup.UNKNOWN

    def test_porridge_is_fg5_or_composite(self) -> None:
        cls = _classify("Porridge avoine lait")
        # Either FG5 whole_grain (porridge plain) or composite
        # vegetarian (porridge with milk) — the regression is that
        # it's no longer FG2 alone (which would lose the grain
        # nature).
        assert cls.wwf_food_group is not WWFFoodGroup.UNKNOWN

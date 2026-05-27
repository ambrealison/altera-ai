"""Phase PT-WWF-S2 — guard additions surfaced by the operator's
manual audit of the live 100-product PT+WWF run.

Covered:
  A. PT vegan/plant-only composite demotion — Wrap falafel houmous,
     Soupe lentilles coco, Chili sin carne, Burger haricots noirs,
     Bowl tofu riz légumes etc. were mis-routed to
     ``composite_products`` by the model; the new
     ``composite_vegan_demoted_plant_core`` /
     ``composite_vegan_demoted_plant_non_core`` /
     ``plant_non_core_promoted_plant_core`` guards demote/promote
     them.
  B. WWF readable fallback covers FG4 fruit/veg — plain "Carottes",
     "Tomates", "Champignons", "Mangue", "Épinards", "Brocoli" no
     longer fall through to terminal-failed when the provider
     crashes; the new ``wwf_readable_fallback_fg4_fruits_veg`` rule
     fires instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.ai.pt_guards import apply_pt_guards
from altera_api.ai.wwf_guards import classify_wwf_readable_fallback
from altera_api.domain.common import ClassificationSource
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.wwf import WWFFoodGroup


def _pt_seed(group: ProteinTrackerGroup) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=uuid4(),
        pt_group=group,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase-pt-wwf-s2-test",
        ai_model="phase-pt-wwf-s2-fake",
        updated_at=datetime.now(UTC),
    )


def _apply_pt(
    name: str, seed_group: ProteinTrackerGroup
) -> tuple[ProteinTrackerGroup, str | None]:
    override = apply_pt_guards(name, _pt_seed(seed_group))
    if override is None:
        return seed_group, None
    return override.new_classification.pt_group, override.rule


# ---------------------------------------------------------------------------
# A. PT vegan composite demotion / plant-core promotion
# ---------------------------------------------------------------------------


class TestVeganCompositeDemoted:
    @pytest.mark.parametrize(
        "name",
        [
            "Wrap falafel houmous",
            "Soupe lentilles coco",
            "Chili sin carne",
            "Burger haricots noirs",
            "Bowl tofu riz legumes",
            "Burger vegetal soja",
        ],
    )
    def test_vegan_composite_with_plant_protein_becomes_plant_core(
        self, name: str
    ) -> None:
        group, rule = _apply_pt(
            name, ProteinTrackerGroup.COMPOSITE_PRODUCTS
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE, (
            f"{name!r}: expected plant_based_core, got {group} (rule={rule})"
        )
        assert rule == "composite_vegan_demoted_plant_core"

    def test_vegan_pizza_no_central_protein_becomes_plant_non_core(self) -> None:
        # "Pizza légumes vegan" has the vegan cue but no central
        # plant-protein anchor — demoted to plant_based_non_core.
        group, rule = _apply_pt(
            "Pizza legumes vegan", ProteinTrackerGroup.COMPOSITE_PRODUCTS
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert rule == "composite_vegan_demoted_plant_non_core"


class TestPlantNonCorePromoted:
    @pytest.mark.parametrize(
        "name",
        [
            "Curry pois chiches epinards",
            "Salade quinoa edamame",
            "Salade lentilles vertes",
        ],
    )
    def test_plant_non_core_with_legume_anchor_becomes_plant_core(
        self, name: str
    ) -> None:
        group, rule = _apply_pt(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE, (
            f"{name!r}: expected plant_based_core, got {group} (rule={rule})"
        )
        assert rule == "plant_non_core_promoted_plant_core"


class TestAnimalCompositeStaysComposite:
    """Non-regression — animal-containing composites are still composite."""

    @pytest.mark.parametrize(
        "name",
        [
            "Pizza jambon fromage",
            "Lasagnes bolognaise",
            "Cassoulet maison",
        ],
    )
    def test_animal_composite_stays_composite(self, name: str) -> None:
        group, _rule = _apply_pt(
            name, ProteinTrackerGroup.COMPOSITE_PRODUCTS
        )
        assert group is ProteinTrackerGroup.COMPOSITE_PRODUCTS


class TestPetfoodNotPreempted:
    """Non-regression — petfood guards still own their vocabulary; the
    new plant-core promotion rules MUST NOT fire on
    ``Croquettes Chien Tofu``."""

    def test_petfood_plant_protein_stays_petfood_rule(self) -> None:
        group, rule = _apply_pt(
            "Croquettes Chien Tofu",
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert rule == "petfood_plant_protein_core"


# ---------------------------------------------------------------------------
# B. WWF readable fallback covers FG4 fruit/veg
# ---------------------------------------------------------------------------


class TestWwfReadableFallbackFG4:
    @pytest.mark.parametrize(
        "name",
        [
            "Carottes",
            "Tomates cerises",
            "Champignons de Paris",
            "Mangue",
            "Brocoli",
            "Epinards frais",
            "Pommes Golden",
            "Bananes bio",
            "Courgettes",
        ],
    )
    def test_plain_fruit_veg_returns_fg4(self, name: str) -> None:
        out = classify_wwf_readable_fallback(name)
        assert out is not None, (
            f"{name!r}: expected FG4 fallback, got None"
        )
        fg, is_composite, *_rest, rule = (
            out[0],
            out[1],
            *out[2:8],
            out[8],
        )
        assert fg is WWFFoodGroup.FG4
        assert is_composite is False
        assert rule == "wwf_readable_fallback_fg4_fruits_veg"

    def test_unrecognized_name_still_returns_none(self) -> None:
        assert classify_wwf_readable_fallback("zzzz xqz gizmo") is None

    def test_composite_still_wins_over_fg4(self) -> None:
        # "Cassoulet" is a composite dish — must keep its composite
        # mapping even though FG4 vocabulary is now checked.
        out = classify_wwf_readable_fallback("Cassoulet maison")
        assert out is not None
        assert out[0] is WWFFoodGroup.FG1
        assert out[1] is True  # composite

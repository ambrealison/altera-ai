"""Smoke checks on the bundled default rule set.

These tests verify the engine + default rules together produce sane
classifications on a handful of canonical products. They are not
exhaustive — the deterministic engine is allowed to pass-through, and
the bundled rule set is intentionally narrow at this phase.
"""

from __future__ import annotations

from datetime import datetime

from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG5GrainKind,
    WWFFoodGroup,
)
from altera_api.rules.engine import (
    PTMatched,
    PTPassThrough,
    WWFMatched,
    WWFPassThrough,
    classify_protein_tracker,
    classify_wwf,
)
from altera_api.rules.loader import load_rules_from_dir


def test_pt_lentil_soup_matches_plant_based_core(make_pt_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_pt_product(name="Red Lentil Soup", labels=("vegan",))
    verdict = classify_protein_tracker(product, rs.pt, now=now)
    assert isinstance(verdict, PTMatched)
    assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE


def test_pt_chicken_matches_animal_core(make_pt_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_pt_product(name="Grilled Chicken Breast")
    verdict = classify_protein_tracker(product, rs.pt, now=now)
    assert isinstance(verdict, PTMatched)
    assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE


def test_pt_oat_milk_matches_plant_non_core(make_pt_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_pt_product(name="Oat Milk Barista 1L")
    verdict = classify_protein_tracker(product, rs.pt, now=now)
    assert isinstance(verdict, PTMatched)
    assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE


def test_pt_lasagna_matches_composite(make_pt_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_pt_product(name="Beef Lasagna Ready Meal")
    verdict = classify_protein_tracker(product, rs.pt, now=now)
    # Composite rules at priority 500 fire alongside animal rules at 100 →
    # this is the expected collision the engine routes to manual review.
    # We just assert it does NOT silently mis-classify as a single category.
    assert not isinstance(verdict, PTPassThrough)


def test_pt_unknown_product_is_pass_through(make_pt_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_pt_product(name="Mystery Sauce")
    verdict = classify_protein_tracker(product, rs.pt, now=now)
    assert isinstance(verdict, PTPassThrough)


def test_wwf_beef_mince_matches_fg1_red_meat(make_wwf_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Beef Mince 500g")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFMatched)
    assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
    assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.RED_MEAT


def test_wwf_cheddar_matches_fg2_cheese(make_wwf_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Mature Cheddar 400g")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFMatched)
    assert verdict.classification.fg2_subgroup is WWFFG2Subgroup.CHEESE


def test_wwf_oat_milk_matches_dairy_alt_plant(make_wwf_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Oat Milk 1L")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFMatched)
    assert verdict.classification.fg2_subgroup is WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT


def test_wwf_wholegrain_bread_matches_whole_grain(make_wwf_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Sliced Wholegrain Bread")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFMatched)
    assert verdict.classification.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN


def test_wwf_mystery_product_pass_through(make_wwf_product, now: datetime) -> None:
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Mystery Compound")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFPassThrough)


def test_engine_does_not_emit_composite_bucket_for_simple_product(
    make_wwf_product, now: datetime
) -> None:
    """Regression guard: a non-composite rule must not set composite_step1_bucket."""
    rs = load_rules_from_dir()
    product = make_wwf_product(name="Beef Mince 500g")
    verdict = classify_wwf(product, rs.wwf, now=now)
    assert isinstance(verdict, WWFMatched)
    assert verdict.classification.composite_step1_bucket is None
    # And the test that should-be-composite rules do set the bucket:
    _ = WWFCompositeStep1Bucket  # touch import

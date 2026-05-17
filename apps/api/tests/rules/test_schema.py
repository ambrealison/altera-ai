from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology
from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG5GrainKind,
    WWFFoodGroup,
)
from altera_api.rules.schema import (
    ConditionNode,
    PTRule,
    WWFRule,
    WWFRuleCategory,
)


class TestConditionNode:
    def test_leaf_form_ok(self) -> None:
        node = ConditionNode(product_name_contains=("lentil", "lentils"))
        assert node.product_name_contains == ("lentil", "lentils")

    def test_group_form_ok(self) -> None:
        node = ConditionNode(
            any_of=(
                ConditionNode(product_name_contains=("lentil",)),
                ConditionNode(taxonomy_node="food.pulses.lentils"),
            )
        )
        assert node.any_of is not None and len(node.any_of) == 2

    def test_empty_node_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            ConditionNode()

    def test_mixed_form_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            ConditionNode(
                product_name_contains=("x",),
                any_of=(ConditionNode(product_name_contains=("y",)),),
            )

    def test_two_leaves_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            ConditionNode(
                product_name_contains=("x",),
                brand_in=("y",),
            )


class TestWWFRuleCategory:
    def test_fg1_requires_subgroup(self) -> None:
        with pytest.raises(PydanticValidationError):
            WWFRuleCategory(wwf_food_group=WWFFoodGroup.FG1)

    def test_fg1_with_subgroup_ok(self) -> None:
        c = WWFRuleCategory(
            wwf_food_group=WWFFoodGroup.FG1,
            wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        )
        assert c.wwf_fg1_subgroup is WWFFG1Subgroup.RED_MEAT

    def test_fg2_with_subgroup_ok(self) -> None:
        c = WWFRuleCategory(
            wwf_food_group=WWFFoodGroup.FG2,
            wwf_fg2_subgroup=WWFFG2Subgroup.CHEESE,
        )
        assert c.wwf_fg2_subgroup is WWFFG2Subgroup.CHEESE

    def test_subgroup_for_wrong_food_group_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            WWFRuleCategory(
                wwf_food_group=WWFFoodGroup.FG4,
                wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
            )

    def test_composite_requires_bucket(self) -> None:
        with pytest.raises(PydanticValidationError):
            WWFRuleCategory(
                wwf_food_group=WWFFoodGroup.FG1,
                wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                wwf_is_composite=True,
            )

    def test_composite_with_bucket_ok(self) -> None:
        c = WWFRuleCategory(
            wwf_food_group=WWFFoodGroup.FG1,
            wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
            wwf_is_composite=True,
            wwf_composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
        )
        assert c.wwf_composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_bucket_without_composite_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            WWFRuleCategory(
                wwf_food_group=WWFFoodGroup.FG1,
                wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                wwf_composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
            )


class TestPTRule:
    def test_creates(self) -> None:
        r = PTRule(
            id="pt.test.x",
            methodology=Methodology.PROTEIN_TRACKER,
            category=ProteinTrackerGroup.PLANT_BASED_CORE,
            match=ConditionNode(product_name_contains=("lentil",)),
        )
        assert r.priority == 1000  # default
        assert r.category is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_system_state_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            PTRule(
                id="pt.test.x",
                methodology=Methodology.PROTEIN_TRACKER,
                category=ProteinTrackerGroup.UNKNOWN,
                match=ConditionNode(product_name_contains=("lentil",)),
            )


class TestWWFRule:
    def test_creates(self) -> None:
        r = WWFRule(
            id="wwf.test.x",
            methodology=Methodology.WWF,
            category=WWFRuleCategory(
                wwf_food_group=WWFFoodGroup.FG5,
                wwf_fg5_grain_kind=WWFFG5GrainKind.WHOLE_GRAIN,
            ),
            match=ConditionNode(product_name_contains=("wholegrain",)),
        )
        assert r.category.wwf_food_group is WWFFoodGroup.FG5

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import ClassificationSource
from altera_api.domain.wwf import (
    WWFCalculationRow,
    WWFCalculationSummary,
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
    WWFProductClassification,
)

_VER = dict(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


class TestWWFEnums:
    def test_fg1_animal_split(self) -> None:
        assert WWFFG1Subgroup.RED_MEAT.is_animal
        assert WWFFG1Subgroup.LEGUMES.is_plant
        assert WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES.is_plant
        assert WWFFG1Subgroup.SEAFOOD.is_animal

    def test_fg2_dairy_equivalent_factors(self) -> None:
        assert WWFFG2Subgroup.CHEESE.dairy_equivalent_factor == Decimal("10")
        assert WWFFG2Subgroup.OTHER_DAIRY_ANIMAL.dairy_equivalent_factor == Decimal("1")
        assert WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT.dairy_equivalent_factor == Decimal("1")
        assert WWFFG2Subgroup.CHEESE.is_animal
        assert WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT.is_animal is False


class TestWWFProductClassificationCrossFieldRules:
    def _base(self, product_id: UUID, now: datetime) -> dict:
        return dict(
            product_id=product_id,
            wwf_food_group=WWFFoodGroup.FG1,
            wwf_is_composite=False,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="wwf.meat.beef",
            updated_at=now,
        )

    def test_fg1_requires_fg1_subgroup(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)

    def test_fg1_with_subgroup_ok(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["fg1_subgroup"] = WWFFG1Subgroup.RED_MEAT
        c = WWFProductClassification(**base)
        assert c.fg1_subgroup is WWFFG1Subgroup.RED_MEAT

    def test_fg2_requires_fg2_subgroup(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG2
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)

    def test_fg2_with_subgroup_ok(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG2
        base["fg2_subgroup"] = WWFFG2Subgroup.CHEESE
        c = WWFProductClassification(**base)
        assert c.fg2_subgroup is WWFFG2Subgroup.CHEESE

    def test_fg5_requires_grain_kind(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG5
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)
        base["fg5_grain_kind"] = WWFFG5GrainKind.WHOLE_GRAIN
        c = WWFProductClassification(**base)
        assert c.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_subgroup_for_wrong_food_group_rejected(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG4
        base["fg1_subgroup"] = WWFFG1Subgroup.RED_MEAT
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)

    def test_composite_requires_step1_bucket(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["fg1_subgroup"] = WWFFG1Subgroup.RED_MEAT
        base["wwf_is_composite"] = True
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)
        base["composite_step1_bucket"] = WWFCompositeStep1Bucket.MEAT_BASED
        c = WWFProductClassification(**base)
        assert c.composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_step1_bucket_must_be_null_when_not_composite(
        self, product_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, now)
        base["fg1_subgroup"] = WWFFG1Subgroup.RED_MEAT
        base["composite_step1_bucket"] = WWFCompositeStep1Bucket.MEAT_BASED
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)

    def test_system_state_has_no_subgroups(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.OUT_OF_SCOPE
        base["fg1_subgroup"] = WWFFG1Subgroup.RED_MEAT
        with pytest.raises(PydanticValidationError):
            WWFProductClassification(**base)

    def test_fg7_kind_ok(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG7
        base["fg7_snack_kind"] = WWFFG7SnackKind.PLANT_BASED_SNACK
        c = WWFProductClassification(**base)
        assert c.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK

    def test_fg3_kind_ok(self, product_id: UUID, now: datetime) -> None:
        base = self._base(product_id, now)
        base["wwf_food_group"] = WWFFoodGroup.FG3
        base["fg3_subgroup"] = WWFFG3Subgroup.PLANT_BASED_FAT
        c = WWFProductClassification(**base)
        assert c.fg3_subgroup is WWFFG3Subgroup.PLANT_BASED_FAT


class TestWWFCompositeIngredient:
    def test_creates_fg1_ingredient(self, product_id: UUID) -> None:
        ing = WWFCompositeIngredient(
            id=UUID("00000000-0000-0000-0000-000000001111"),
            parent_product_id=product_id,
            food_group=WWFFoodGroup.FG1,
            fg1_subgroup=WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
            ingredient_weight_kg_per_item=Decimal("0.070"),
        )
        assert ing.fg1_subgroup is WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES

    def test_rejects_fg7(self, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            WWFCompositeIngredient(
                id=UUID("00000000-0000-0000-0000-000000001112"),
                parent_product_id=product_id,
                food_group=WWFFoodGroup.FG7,
                ingredient_weight_kg_per_item=Decimal("0.05"),
            )

    def test_fg2_requires_fg2_subgroup(self, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            WWFCompositeIngredient(
                id=UUID("00000000-0000-0000-0000-000000001113"),
                parent_product_id=product_id,
                food_group=WWFFoodGroup.FG2,
                ingredient_weight_kg_per_item=Decimal("0.02"),
            )

    def test_fg4_must_have_no_subgroup(self, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            WWFCompositeIngredient(
                id=UUID("00000000-0000-0000-0000-000000001114"),
                parent_product_id=product_id,
                food_group=WWFFoodGroup.FG4,
                fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                ingredient_weight_kg_per_item=Decimal("0.15"),
            )


class TestWWFCalculationRow:
    def _base(self, run_id: UUID, product_id: UUID) -> dict:
        return dict(
            run_id=run_id,
            product_id=product_id,
            in_scope=True,
            wwf_food_group=WWFFoodGroup.FG1,
            wwf_subgroup_label="red_meat",
            weight_kg=Decimal("2100"),
            wwf_is_composite=False,
            **_VER,
        )

    def test_fg2_must_have_dairy_equiv(self, run_id: UUID, product_id: UUID) -> None:
        base = self._base(run_id, product_id)
        base["wwf_food_group"] = WWFFoodGroup.FG2
        with pytest.raises(PydanticValidationError):
            WWFCalculationRow(**base)
        base["weight_kg_dairy_equiv"] = Decimal("21000")
        r = WWFCalculationRow(**base)
        assert r.weight_kg_dairy_equiv == Decimal("21000")

    def test_non_fg2_rejects_dairy_equiv(self, run_id: UUID, product_id: UUID) -> None:
        base = self._base(run_id, product_id)
        base["weight_kg_dairy_equiv"] = Decimal("100")
        with pytest.raises(PydanticValidationError):
            WWFCalculationRow(**base)

    def test_composite_row_with_bucket(self, run_id: UUID, product_id: UUID) -> None:
        base = self._base(run_id, product_id)
        base["wwf_is_composite"] = True
        base["wwf_composite_step1_bucket"] = WWFCompositeStep1Bucket.MEAT_BASED
        r = WWFCalculationRow(**base)
        assert r.wwf_composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_composite_without_bucket_rejected(self, run_id: UUID, product_id: UUID) -> None:
        base = self._base(run_id, product_id)
        base["wwf_is_composite"] = True
        with pytest.raises(PydanticValidationError):
            WWFCalculationRow(**base)

    def test_in_scope_must_match_food_group(self, run_id: UUID, product_id: UUID) -> None:
        base = self._base(run_id, product_id)
        base["wwf_food_group"] = WWFFoodGroup.OUT_OF_SCOPE
        base["in_scope"] = True
        with pytest.raises(PydanticValidationError):
            WWFCalculationRow(**base)


class TestWWFCalculationSummary:
    def test_buckets_must_sum_to_total(self, run_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            WWFCalculationSummary(
                run_id=run_id,
                reporting_period_label="FY 2024",
                per_food_group=(),
                total_sales_weight_in_scope_kg=Decimal("0"),
                composites_total_weight_kg=Decimal("100"),
                composites_meat_based_kg=Decimal("40"),
                composites_seafood_based_kg=Decimal("20"),
                composites_vegetarian_kg=Decimal("20"),
                composites_vegan_kg=Decimal("10"),  # sum=90, total=100
                whole_diet_plant_weight_kg=Decimal("0"),
                whole_diet_animal_weight_kg=Decimal("0"),
                out_of_scope_count=0,
                unknown_count=0,
                **_VER,
            )

    def test_unique_food_groups_in_per_group(self, run_id: UUID) -> None:
        dup = (
            WWFFoodGroupAggregate(
                food_group=WWFFoodGroup.FG1,
                weight_kg=Decimal("100"),
                share_pct=Decimal("50"),
            ),
            WWFFoodGroupAggregate(
                food_group=WWFFoodGroup.FG1,
                weight_kg=Decimal("100"),
                share_pct=Decimal("50"),
            ),
        )
        with pytest.raises(PydanticValidationError):
            WWFCalculationSummary(
                run_id=run_id,
                reporting_period_label="FY 2024",
                per_food_group=dup,
                total_sales_weight_in_scope_kg=Decimal("200"),
                composites_total_weight_kg=Decimal("0"),
                composites_meat_based_kg=Decimal("0"),
                composites_seafood_based_kg=Decimal("0"),
                composites_vegetarian_kg=Decimal("0"),
                composites_vegan_kg=Decimal("0"),
                whole_diet_plant_weight_kg=Decimal("0"),
                whole_diet_animal_weight_kg=Decimal("0"),
                out_of_scope_count=0,
                unknown_count=0,
                **_VER,
            )

    def test_creates_valid_summary(self, run_id: UUID) -> None:
        s = WWFCalculationSummary(
            run_id=run_id,
            reporting_period_label="FY 2024",
            per_food_group=(
                WWFFoodGroupAggregate(
                    food_group=WWFFoodGroup.FG1,
                    weight_kg=Decimal("15918"),
                    share_pct=Decimal("24.55"),
                    phd_reference_share_pct=Decimal("16"),
                ),
            ),
            total_sales_weight_in_scope_kg=Decimal("64826"),
            composites_total_weight_kg=Decimal("1280"),
            composites_meat_based_kg=Decimal("1280"),
            composites_seafood_based_kg=Decimal("0"),
            composites_vegetarian_kg=Decimal("0"),
            composites_vegan_kg=Decimal("0"),
            whole_diet_plant_weight_kg=Decimal("0"),
            whole_diet_animal_weight_kg=Decimal("0"),
            out_of_scope_count=0,
            unknown_count=0,
            **_VER,
        )
        assert s.composites_total_weight_kg == Decimal("1280")

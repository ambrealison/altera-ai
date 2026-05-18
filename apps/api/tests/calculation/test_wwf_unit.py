"""Hand-verifiable WWF calculation tests.

Each scenario uses tiny inputs whose expected weights / shares can be
re-derived on paper. These tests pin the formulas in
docs/calculation/wwf-calculation.md.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from altera_api.calculation import (
    PHD_REFERENCE_SHARES,
    WWFRunVersions,
    calculate_wwf_run,
)
from altera_api.domain.common import (
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import (
    NormalizedProduct,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.wwf import (
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)


def _ids(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


def _product(
    *,
    product_uid: int,
    weight_per_item_kg: str,
    items_sold: str,
    is_own_brand: bool = False,
    retail_channel: RetailChannel = RetailChannel.GROCERY_AMBIENT,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> NormalizedProduct:
    return NormalizedProduct(
        id=_ids(product_uid),
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        row_number=product_uid,
        external_product_id=f"W-{product_uid:03d}",
        product_name=f"Product {product_uid}",
        is_own_brand=is_own_brand,
        weight_per_item_kg=Decimal(weight_per_item_kg),
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=Decimal(items_sold),
            retail_channel=retail_channel,
            is_own_brand=is_own_brand,
        ),
        created_at=now,
    )


def _classification(
    product_id: UUID,
    *,
    food_group: WWFFoodGroup,
    is_composite: bool = False,
    fg1_subgroup: WWFFG1Subgroup | None = None,
    fg2_subgroup: WWFFG2Subgroup | None = None,
    fg3_subgroup: WWFFG3Subgroup | None = None,
    fg5_grain_kind: WWFFG5GrainKind | None = None,
    fg7_snack_kind: WWFFG7SnackKind | None = None,
    composite_step1_bucket: WWFCompositeStep1Bucket | None = None,
    now: datetime,
) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=food_group,
        wwf_is_composite=is_composite,
        fg1_subgroup=fg1_subgroup,
        fg2_subgroup=fg2_subgroup,
        fg3_subgroup=fg3_subgroup,
        fg5_grain_kind=fg5_grain_kind,
        fg7_snack_kind=fg7_snack_kind,
        composite_step1_bucket=composite_step1_bucket,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="wwf.test.rule",
        updated_at=now,
    )


@pytest.fixture
def wwf_versions() -> WWFRunVersions:
    return WWFRunVersions(
        methodology_version="1.0.0",
        methodology_source_edition="WWF Food Practice 2024",
        taxonomy_version="1.0.0",
        rules_version="1.0.0",
    )


class TestWeightAndShare:
    def test_single_fg1_red_meat(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        product = _product(
            product_uid=1,
            weight_per_item_kg="0.5",
            items_sold="100",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            product.id: _classification(
                product.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        # weight = 0.5 × 100 = 50 kg
        assert result.rows[0].weight_kg == Decimal("50.00000000")
        s = result.summary
        assert s.total_sales_weight_in_scope_kg == Decimal("50.00000000")
        fg1 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG1)
        assert fg1.weight_kg == Decimal("50.00000000")
        assert fg1.share_pct == Decimal("100.00000000")
        assert fg1.phd_reference_share_pct == Decimal("16")
        # FG2 is empty but the dairy_equiv slot is still set (zero).
        fg2 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG2)
        assert fg2.weight_kg_dairy_equiv == Decimal("0.00000000")


class TestDairyEquivalents:
    def test_cheese_multiplied_by_ten(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        cheddar = _product(
            product_uid=10,
            weight_per_item_kg="0.4",
            items_sold="100",  # raw = 40 kg
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            cheddar.id: _classification(
                cheddar.id,
                food_group=WWFFoodGroup.FG2,
                fg2_subgroup=WWFFG2Subgroup.CHEESE,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [cheddar],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        row = result.rows[0]
        assert row.weight_kg == Decimal("40.00000000")
        # Cheese ×10 → equivalent 400 kg
        assert row.weight_kg_dairy_equiv == Decimal("400.00000000")
        fg2 = next(a for a in result.summary.per_food_group if a.food_group is WWFFoodGroup.FG2)
        assert fg2.weight_kg == Decimal("40.00000000")
        assert fg2.weight_kg_dairy_equiv == Decimal("400.00000000")
        # Whole-diet animal uses the equivalents.
        assert result.summary.whole_diet_animal_weight_kg == Decimal("400.00000000")
        assert result.summary.whole_diet_plant_weight_kg == Decimal("0.00000000")

    def test_other_dairy_factor_one(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        milk = _product(
            product_uid=11,
            weight_per_item_kg="1.0",
            items_sold="50",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            milk.id: _classification(
                milk.id,
                food_group=WWFFoodGroup.FG2,
                fg2_subgroup=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [milk], cls, run_id=run_id, reporting_period_label="FY 2024", versions=wwf_versions
        )
        # Raw 50 kg, factor 1 → equiv 50 kg
        assert result.rows[0].weight_kg_dairy_equiv == Decimal("50.00000000")
        assert result.summary.whole_diet_animal_weight_kg == Decimal("50.00000000")

    def test_plant_alternative_no_conversion(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        oat_milk = _product(
            product_uid=12,
            weight_per_item_kg="1.0",
            items_sold="30",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            oat_milk.id: _classification(
                oat_milk.id,
                food_group=WWFFoodGroup.FG2,
                fg2_subgroup=WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [oat_milk],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        assert result.rows[0].weight_kg_dairy_equiv == Decimal("30.00000000")
        # Plant alternative → counted as plant in whole-diet split.
        assert result.summary.whole_diet_plant_weight_kg == Decimal("30.00000000")
        assert result.summary.whole_diet_animal_weight_kg == Decimal("0.00000000")


class TestStep1Composites:
    def test_composite_contributes_only_to_bucket_not_to_fg(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        lasagna = _product(
            product_uid=20,
            weight_per_item_kg="0.4",
            items_sold="100",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            lasagna.id: _classification(
                lasagna.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                is_composite=True,
                composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [lasagna],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        s = result.summary
        # Whole 40 kg goes to meat_based bucket, not to FG1.
        fg1 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG1)
        assert fg1.weight_kg == Decimal("0.00000000")
        assert s.composites_meat_based_kg == Decimal("40.00000000")
        assert s.composites_total_weight_kg == Decimal("40.00000000")
        assert s.total_sales_weight_in_scope_kg == Decimal("40.00000000")
        # Step 1 only — no whole-diet contribution.
        assert s.whole_diet_plant_weight_kg == Decimal("0.00000000")
        assert s.whole_diet_animal_weight_kg == Decimal("0.00000000")

    def test_buckets_sum_to_total(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        meat = _product(
            product_uid=30,
            weight_per_item_kg="1",
            items_sold="10",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        vegan = _product(
            product_uid=31,
            weight_per_item_kg="1",
            items_sold="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            meat.id: _classification(
                meat.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                is_composite=True,
                composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                now=now,
            ),
            vegan.id: _classification(
                vegan.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                is_composite=True,
                composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
                now=now,
            ),
        }
        result = calculate_wwf_run(
            [meat, vegan],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        s = result.summary
        assert s.composites_meat_based_kg == Decimal("10.00000000")
        assert s.composites_vegan_kg == Decimal("20.00000000")
        # Domain validator already pins the sum; this is the user-facing check.
        total = (
            s.composites_meat_based_kg
            + s.composites_seafood_based_kg
            + s.composites_vegetarian_kg
            + s.composites_vegan_kg
        )
        assert total == s.composites_total_weight_kg


class TestStep2Ingredients:
    def test_own_brand_ingredients_distributed_to_fgs(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        # 400g own-brand vegan lasagna, 100 sold → 40 kg whole composite.
        # Ingredients: 70g tofu (FG1 alt_protein), 20g soy "cheese" (FG2 plant),
        # 100g lasagne sheets (FG5 refined), 150g vegetables (FG4) per item.
        lasagna = _product(
            product_uid=40,
            weight_per_item_kg="0.4",
            items_sold="100",
            is_own_brand=True,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            lasagna.id: _classification(
                lasagna.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
                is_composite=True,
                composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
                now=now,
            )
        }
        ingredients = {
            lasagna.id: (
                WWFCompositeIngredient(
                    id=_ids(401),
                    parent_product_id=lasagna.id,
                    food_group=WWFFoodGroup.FG1,
                    fg1_subgroup=WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
                    ingredient_weight_kg_per_item=Decimal("0.070"),
                ),
                WWFCompositeIngredient(
                    id=_ids(402),
                    parent_product_id=lasagna.id,
                    food_group=WWFFoodGroup.FG2,
                    fg2_subgroup=WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT,
                    ingredient_weight_kg_per_item=Decimal("0.020"),
                ),
                WWFCompositeIngredient(
                    id=_ids(403),
                    parent_product_id=lasagna.id,
                    food_group=WWFFoodGroup.FG5,
                    ingredient_weight_kg_per_item=Decimal("0.100"),
                ),
                WWFCompositeIngredient(
                    id=_ids(404),
                    parent_product_id=lasagna.id,
                    food_group=WWFFoodGroup.FG4,
                    ingredient_weight_kg_per_item=Decimal("0.150"),
                ),
            ),
        }
        result = calculate_wwf_run(
            [lasagna],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
            ingredients_by_product=ingredients,
        )
        s = result.summary
        # Ingredient distributions (per item × 100 items sold):
        fg1 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG1)
        fg2 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG2)
        fg4 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG4)
        fg5 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG5)
        assert fg1.weight_kg == Decimal("7.00000000")  # 0.07 × 100
        assert fg2.weight_kg == Decimal("2.00000000")
        assert fg5.weight_kg == Decimal("10.00000000")
        assert fg4.weight_kg == Decimal("15.00000000")
        # Step 1 bucket also still reported.
        assert s.composites_vegan_kg == Decimal("40.00000000")
        assert s.composites_total_weight_kg == Decimal("40.00000000")
        # Whole-diet split: all ingredients here are plant.
        assert s.whole_diet_plant_weight_kg == Decimal("34.00000000")
        assert s.whole_diet_animal_weight_kg == Decimal("0.00000000")

    def test_branded_composite_step2_data_ignored(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        # Branded composite — even if ingredients are supplied they are
        # silently ignored per the methodology.
        bolognese = _product(
            product_uid=50,
            weight_per_item_kg="0.4",
            items_sold="100",
            is_own_brand=False,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            bolognese.id: _classification(
                bolognese.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                is_composite=True,
                composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                now=now,
            )
        }
        ingredients = {
            bolognese.id: (
                WWFCompositeIngredient(
                    id=_ids(501),
                    parent_product_id=bolognese.id,
                    food_group=WWFFoodGroup.FG1,
                    fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                    ingredient_weight_kg_per_item=Decimal("0.080"),
                ),
            )
        }
        result = calculate_wwf_run(
            [bolognese],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
            ingredients_by_product=ingredients,
        )
        # FG1 must remain 0 — branded composites stay at Step 1.
        fg1 = next(a for a in result.summary.per_food_group if a.food_group is WWFFoodGroup.FG1)
        assert fg1.weight_kg == Decimal("0.00000000")
        assert result.summary.composites_meat_based_kg == Decimal("40.00000000")


class TestOutOfScopeAndUnknown:
    def test_out_of_scope_excluded_from_total(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        in_scope = _product(
            product_uid=60,
            weight_per_item_kg="1",
            items_sold="10",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        water = _product(
            product_uid=61,
            weight_per_item_kg="1.5",
            items_sold="100",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            in_scope.id: _classification(
                in_scope.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                now=now,
            ),
            water.id: _classification(water.id, food_group=WWFFoodGroup.OUT_OF_SCOPE, now=now),
        }
        result = calculate_wwf_run(
            [in_scope, water],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=wwf_versions,
        )
        # 150 kg of water contributes nothing.
        assert result.summary.total_sales_weight_in_scope_kg == Decimal("10.00000000")
        assert result.summary.out_of_scope_count == 1


class TestVersionsAndMissingClassification:
    def test_versions_stamped(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        p = _product(
            product_uid=70,
            weight_per_item_kg="1",
            items_sold="1",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            p.id: _classification(
                p.id,
                food_group=WWFFoodGroup.FG5,
                fg5_grain_kind=WWFFG5GrainKind.WHOLE_GRAIN,
                now=now,
            )
        }
        result = calculate_wwf_run(
            [p], cls, run_id=run_id, reporting_period_label="FY 2024", versions=wwf_versions
        )
        assert result.summary.methodology is Methodology.WWF
        assert result.summary.methodology_source_edition == "WWF Food Practice 2024"
        assert result.rows[0].methodology_version == "1.0.0"

    def test_missing_classification_raises(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        wwf_versions: WWFRunVersions,
    ) -> None:
        p = _product(
            product_uid=80,
            weight_per_item_kg="1",
            items_sold="1",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        with pytest.raises(ValueError, match="no WWF classification"):
            calculate_wwf_run(
                [p], {}, run_id=run_id, reporting_period_label="FY 2024", versions=wwf_versions
            )


class TestPHDConstants:
    def test_phd_reference_share_matches_doc(self) -> None:
        assert PHD_REFERENCE_SHARES == {
            WWFFoodGroup.FG1: Decimal("16"),
            WWFFoodGroup.FG2: Decimal("19"),
            WWFFoodGroup.FG3: Decimal("4"),
            WWFFoodGroup.FG4: Decimal("39"),
            WWFFoodGroup.FG5: Decimal("18"),
            WWFFoodGroup.FG6: Decimal("4"),
        }
        # FG7 has no PHD reference.
        assert WWFFoodGroup.FG7 not in PHD_REFERENCE_SHARES

"""WWF Planet-Based Diets calculation.

Implements docs/calculation/wwf-calculation.md verbatim:

* per-product ``weight_kg = weight_per_item_kg * items_sold``
* dairy equivalents on FG2 rows (cheese ×10, other ×1, plant alt ×1)
* per-food-group aggregation across FG1..FG7, with PHD reference shares
  on FG1..FG6
* composite handling — Step 1 (whole composite weight → one of 4
  buckets, **always** reported) and Step 2 (own-brand ingredient
  weights distributed into FG totals, when supplied)
* whole-diet plant-vs-animal context split (FG2 animal uses
  dairy-equivalent weights)

WWF and Protein Tracker share no arithmetic. The unit here is
kilogrammes of product weight as sold, never protein.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.wwf import (
    WWFCalculationRow,
    WWFCalculationSummary,
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
    WWFProductClassification,
)

_EIGHT_DP = Decimal("0.00000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_ONE_HUNDRED = Decimal("100")

#: PHD reference shares per docs/methodologies/wwf.md (Planetary Health Diet
#: percentages from the EAT-Lancet model). FG7 has no PHD reference.
PHD_REFERENCE_SHARES: Final[Mapping[WWFFoodGroup, Decimal]] = {
    WWFFoodGroup.FG1: Decimal("16"),
    WWFFoodGroup.FG2: Decimal("19"),
    WWFFoodGroup.FG3: Decimal("4"),
    WWFFoodGroup.FG4: Decimal("39"),
    WWFFoodGroup.FG5: Decimal("18"),
    WWFFoodGroup.FG6: Decimal("4"),
}

_REAL_FOOD_GROUPS: Final[tuple[WWFFoodGroup, ...]] = (
    WWFFoodGroup.FG1,
    WWFFoodGroup.FG2,
    WWFFoodGroup.FG3,
    WWFFoodGroup.FG4,
    WWFFoodGroup.FG5,
    WWFFoodGroup.FG6,
    WWFFoodGroup.FG7,
)


def _q8(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_DP)


@dataclass(frozen=True)
class WWFRunVersions:
    """Version stamps placed on every WWF calculation row and on the summary."""

    methodology_version: str
    methodology_source_edition: str
    taxonomy_version: str
    rules_version: str


@dataclass(frozen=True)
class WWFRunResult:
    rows: tuple[WWFCalculationRow, ...]
    summary: WWFCalculationSummary


def _subgroup_label(classification: WWFProductClassification) -> str | None:
    """Free-text subgroup label for the row, derived from the classification."""
    if classification.fg1_subgroup is not None:
        return classification.fg1_subgroup.value
    if classification.fg2_subgroup is not None:
        return classification.fg2_subgroup.value
    if classification.fg3_subgroup is not None:
        return classification.fg3_subgroup.value
    if classification.fg5_grain_kind is not None:
        return classification.fg5_grain_kind.value
    if classification.fg7_snack_kind is not None:
        return classification.fg7_snack_kind.value
    return None


def _fg2_dairy_equiv(weight_kg: Decimal, subgroup: WWFFG2Subgroup | None) -> Decimal:
    """Apply the FG2 dairy-equivalent factor to a raw weight."""
    if subgroup is None:
        return weight_kg
    return _q8(weight_kg * subgroup.dairy_equivalent_factor)


def _whole_diet_contribution_for_whole(
    classification: WWFProductClassification,
    weight_kg: Decimal,
    dairy_equiv: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Whole-diet plant/animal contribution for one whole (non-composite) row.

    Returns ``(plant_kg, animal_kg)`` in *equivalent* weight terms (the
    FG2 animal contribution uses the dairy-equivalent weight, per the
    methodology).
    """
    fg = classification.wwf_food_group
    if fg is WWFFoodGroup.FG1:
        if classification.fg1_subgroup is not None and classification.fg1_subgroup.is_animal:
            return _ZERO, weight_kg
        return weight_kg, _ZERO
    if fg is WWFFoodGroup.FG2:
        if classification.fg2_subgroup is WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT:
            return weight_kg, _ZERO
        # dairy_animal — uses equivalents for the whole-diet split
        return _ZERO, dairy_equiv if dairy_equiv is not None else weight_kg
    if fg is WWFFoodGroup.FG3:
        if classification.fg3_subgroup is WWFFG3Subgroup.PLANT_BASED_FAT:
            return weight_kg, _ZERO
        return _ZERO, weight_kg
    if fg in {WWFFoodGroup.FG4, WWFFoodGroup.FG5, WWFFoodGroup.FG6}:
        return weight_kg, _ZERO
    if fg is WWFFoodGroup.FG7:
        if classification.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK:
            return weight_kg, _ZERO
        return _ZERO, weight_kg
    return _ZERO, _ZERO  # system states (out_of_scope / unknown)


def _whole_diet_contribution_for_ingredient(
    ingredient: WWFCompositeIngredient,
    ingredient_weight_kg: Decimal,
) -> tuple[Decimal, Decimal]:
    """Whole-diet plant/animal contribution for one Step 2 ingredient."""
    fg = ingredient.food_group
    if fg is WWFFoodGroup.FG1:
        if ingredient.fg1_subgroup is not None and ingredient.fg1_subgroup.is_animal:
            return _ZERO, ingredient_weight_kg
        return ingredient_weight_kg, _ZERO
    if fg is WWFFoodGroup.FG2:
        if ingredient.fg2_subgroup is WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT:
            return ingredient_weight_kg, _ZERO
        # dairy_animal — use equiv
        if ingredient.fg2_subgroup is not None:
            return _ZERO, _q8(
                ingredient_weight_kg * ingredient.fg2_subgroup.dairy_equivalent_factor
            )
        return _ZERO, ingredient_weight_kg
    if fg in {WWFFoodGroup.FG4, WWFFoodGroup.FG5, WWFFoodGroup.FG6}:
        return ingredient_weight_kg, _ZERO
    # FG3 / FG7 are not in the Step 2 target set per WWFCompositeIngredient
    # validator (FG7 is rejected; FG3 ingredients carry no subgroup, so we
    # leave them out of the whole-diet split).
    return _ZERO, _ZERO


def calculate_wwf_run(
    products: Sequence[NormalizedProduct],
    classifications: Mapping[UUID, WWFProductClassification],
    *,
    run_id: UUID,
    reporting_period_label: str,
    versions: WWFRunVersions,
    ingredients_by_product: Mapping[UUID, Sequence[WWFCompositeIngredient]] | None = None,
) -> WWFRunResult:
    """Compute one WWF run. See module docstring for the formulas.

    ``ingredients_by_product`` provides Step 2 ingredient data per
    own-brand composite. When supplied, the ingredient weights are
    distributed across the food-group aggregates; the Step 1 whole-
    weight bucket is still reported (the methodology calls for both).
    Branded composites are reported at Step 1 only — they are silently
    excluded from Step 2 even if ingredient data were supplied.
    """
    rows: list[WWFCalculationRow] = []

    # Aggregators
    fg_weight_raw: dict[WWFFoodGroup, Decimal] = {}
    fg_weight_dairy_equiv_fg2: Decimal = _ZERO
    bucket_weights: dict[WWFCompositeStep1Bucket, Decimal] = {
        b: _ZERO for b in WWFCompositeStep1Bucket
    }
    composite_total = _ZERO
    plant_weight_total = _ZERO
    animal_weight_total = _ZERO
    total_in_scope_weight = _ZERO
    out_of_scope_count = 0
    unknown_count = 0

    for product in products:
        if product.wwf_fields is None:
            continue
        if Methodology.WWF not in product.methodologies_enabled:
            continue

        classification = classifications.get(product.id)
        if classification is None:
            raise ValueError(
                f"product {product.id} has no WWF classification; classification "
                "must precede calculation."
            )

        wwf_fields = product.wwf_fields
        weight_kg = _q8(product.weight_per_item_kg * wwf_fields.items_sold)

        fg = classification.wwf_food_group
        in_scope = fg.is_methodology_group
        is_composite = classification.wwf_is_composite

        if fg is WWFFoodGroup.OUT_OF_SCOPE:
            out_of_scope_count += 1
        elif fg is WWFFoodGroup.UNKNOWN:
            unknown_count += 1

        # FG2 dairy-equivalent on the row (domain validator requires this).
        row_dairy_equiv: Decimal | None
        if fg is WWFFoodGroup.FG2:
            row_dairy_equiv = _fg2_dairy_equiv(weight_kg, classification.fg2_subgroup)
        else:
            row_dairy_equiv = None

        rows.append(
            WWFCalculationRow(
                run_id=run_id,
                product_id=product.id,
                in_scope=in_scope,
                wwf_food_group=fg,
                wwf_subgroup_label=_subgroup_label(classification),
                weight_kg=weight_kg,
                weight_kg_dairy_equiv=row_dairy_equiv,
                wwf_is_composite=is_composite,
                wwf_composite_step1_bucket=classification.composite_step1_bucket,
                methodology_version=versions.methodology_version,
                methodology_source_edition=versions.methodology_source_edition,
                taxonomy_version=versions.taxonomy_version,
                rules_version=versions.rules_version,
            )
        )

        if not in_scope:
            continue

        total_in_scope_weight += weight_kg

        if is_composite:
            # Step 1: whole-weight bucket assignment (always).
            bucket = classification.composite_step1_bucket
            assert bucket is not None  # domain validator guarantees this
            bucket_weights[bucket] += weight_kg
            composite_total += weight_kg

            # Step 2: own-brand only. Branded composites stay at Step 1.
            if wwf_fields.is_own_brand and ingredients_by_product is not None:
                for ingredient in ingredients_by_product.get(product.id, ()):
                    ing_weight = _q8(
                        ingredient.ingredient_weight_kg_per_item * wwf_fields.items_sold
                    )
                    fg_weight_raw[ingredient.food_group] = (
                        fg_weight_raw.get(ingredient.food_group, _ZERO) + ing_weight
                    )
                    if (
                        ingredient.food_group is WWFFoodGroup.FG2
                        and ingredient.fg2_subgroup is not None
                    ):
                        fg_weight_dairy_equiv_fg2 += _q8(
                            ing_weight * ingredient.fg2_subgroup.dairy_equivalent_factor
                        )
                    plant_inc, animal_inc = _whole_diet_contribution_for_ingredient(
                        ingredient, ing_weight
                    )
                    plant_weight_total += plant_inc
                    animal_weight_total += animal_inc
        else:
            # Whole (non-composite) product → contributes to its food group.
            fg_weight_raw[fg] = fg_weight_raw.get(fg, _ZERO) + weight_kg
            if fg is WWFFoodGroup.FG2:
                fg_weight_dairy_equiv_fg2 += (
                    row_dairy_equiv if row_dairy_equiv is not None else weight_kg
                )
            plant_inc, animal_inc = _whole_diet_contribution_for_whole(
                classification, weight_kg, row_dairy_equiv
            )
            plant_weight_total += plant_inc
            animal_weight_total += animal_inc

    # Per-food-group aggregates (FG1..FG7, even when empty).
    per_food_group_list: list[WWFFoodGroupAggregate] = []
    for fg in _REAL_FOOD_GROUPS:
        weight = fg_weight_raw.get(fg, _ZERO)
        share = (
            _q8(weight * _ONE_HUNDRED / total_in_scope_weight)
            if total_in_scope_weight > _ZERO
            else _ZERO
        )
        per_food_group_list.append(
            WWFFoodGroupAggregate(
                food_group=fg,
                weight_kg=_q8(weight),
                weight_kg_dairy_equiv=(
                    _q8(fg_weight_dairy_equiv_fg2) if fg is WWFFoodGroup.FG2 else None
                ),
                share_pct=share,
                phd_reference_share_pct=PHD_REFERENCE_SHARES.get(fg),
            )
        )

    summary = WWFCalculationSummary(
        run_id=run_id,
        reporting_period_label=reporting_period_label,
        per_food_group=tuple(per_food_group_list),
        total_sales_weight_in_scope_kg=_q8(total_in_scope_weight),
        composites_total_weight_kg=_q8(composite_total),
        composites_meat_based_kg=_q8(bucket_weights[WWFCompositeStep1Bucket.MEAT_BASED]),
        composites_seafood_based_kg=_q8(bucket_weights[WWFCompositeStep1Bucket.SEAFOOD_BASED]),
        composites_vegetarian_kg=_q8(bucket_weights[WWFCompositeStep1Bucket.VEGETARIAN]),
        composites_vegan_kg=_q8(bucket_weights[WWFCompositeStep1Bucket.VEGAN]),
        whole_diet_plant_weight_kg=_q8(plant_weight_total),
        whole_diet_animal_weight_kg=_q8(animal_weight_total),
        out_of_scope_count=out_of_scope_count,
        unknown_count=unknown_count,
        methodology_version=versions.methodology_version,
        methodology_source_edition=versions.methodology_source_edition,
        taxonomy_version=versions.taxonomy_version,
        rules_version=versions.rules_version,
    )

    return WWFRunResult(rows=tuple(rows), summary=summary)

"""WWF Step 2 companion ingredient file parser and validator.

Accepts the JSON companion format documented in docs/data/input-formats.md::

    {
      "<external_product_id>": {
        "ingredients": [
          {
            "food_group": "FG1",
            "subgroup": "poultry",
            "ingredient_weight_kg_per_item": 0.070
          }
        ]
      }
    }

Validation rules
----------------
- Parent product must exist in the project (error if not found).
- Parent product must be a WWF composite per its classification (error if
  classification missing or is not composite).
- Step 2 applies to **own-brand** composites only; branded composites
  receive a WARNING and their ingredients are not stored.
- ``ingredient_weight_kg_per_item`` must be strictly positive (error).
- Sum of ingredient weights must not exceed ``weight_per_item_kg`` of the
  parent product (warning — residual mass is normal and expected).
- ``food_group`` must be FG1..FG6; FG7, out_of_scope, and unknown are
  rejected (error).
- When ``food_group=FG1``, ``subgroup`` is required and must be a valid
  :class:`WWFFG1Subgroup` value (error).
- When ``food_group=FG2``, ``subgroup`` is required and must be a valid
  :class:`WWFFG2Subgroup` value (error).
- For FG3..FG6, ``subgroup`` is accepted in the input for informational
  purposes but is not stored in the domain model.

No I/O — all look-ups are done against caller-supplied dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from altera_api.domain.product import NormalizedProduct
from altera_api.domain.wwf import (
    WWFCompositeIngredient,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

_VALID_FG1_SUBGROUPS = {m.value: m for m in WWFFG1Subgroup}
_VALID_FG2_SUBGROUPS = {m.value: m for m in WWFFG2Subgroup}

# Food groups legal in Step 2 (FG7 is explicitly excluded per the domain model).
_STEP2_FOOD_GROUPS = {
    WWFFoodGroup.FG1,
    WWFFoodGroup.FG2,
    WWFFoodGroup.FG3,
    WWFFoodGroup.FG4,
    WWFFoodGroup.FG5,
    WWFFoodGroup.FG6,
}


@dataclass(frozen=True)
class IngredientRowError:
    ingredient_index: int
    field: str
    message: str


@dataclass(frozen=True)
class ProductIngredientResult:
    external_product_id: str
    product_id: UUID | None
    is_own_brand: bool | None
    is_composite: bool | None
    ingredient_count: int
    valid_ingredient_count: int
    total_attributed_weight_kg: Decimal
    product_weight_kg: Decimal | None
    residual_weight_kg: Decimal | None
    errors: tuple[IngredientRowError, ...]
    warnings: tuple[str, ...]
    valid_ingredients: tuple[WWFCompositeIngredient, ...]


@dataclass
class Step2ValidationResult:
    """Aggregated result for one companion ingredient upload."""

    total_products_in_file: int = 0
    unknown_product_count: int = 0
    branded_composite_count: int = 0
    non_composite_count: int = 0
    valid_product_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    product_results: list[ProductIngredientResult] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True when there are no hard errors (warnings are allowed)."""
        return self.error_count == 0

    @property
    def all_valid_ingredients(self) -> list[tuple[UUID, list[WWFCompositeIngredient]]]:
        """Return (product_id, ingredients) pairs ready for storage."""
        return [
            (r.product_id, list(r.valid_ingredients))
            for r in self.product_results
            if r.product_id is not None and r.valid_ingredients
        ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_wwf_step2_json(
    raw: dict[str, Any],
    *,
    products_by_external_id: dict[str, NormalizedProduct],
    classifications: dict[UUID, WWFProductClassification],
) -> Step2ValidationResult:
    """Validate a parsed Step 2 JSON payload.

    Parameters
    ----------
    raw:
        Parsed JSON object keyed by ``external_product_id``.
    products_by_external_id:
        Map of ``external_product_id → NormalizedProduct`` for all products
        in the project.  Used to verify parent existence and weight.
    classifications:
        Map of ``product_id → WWFProductClassification``.  Used to verify
        composites.

    Returns
    -------
    Step2ValidationResult
        Structured validation outcome.  Callers should check
        ``result.is_valid`` before persisting.
    """
    result = Step2ValidationResult()
    result.total_products_in_file = len(raw)

    for ext_id, entry in raw.items():
        product = products_by_external_id.get(ext_id)
        product_result = _validate_product_entry(
            ext_id=ext_id,
            entry=entry,
            product=product,
            classifications=classifications,
        )
        result.product_results.append(product_result)

        if product is None:
            result.unknown_product_count += 1
            result.error_count += len(product_result.errors)
            continue

        if product_result.errors:
            result.error_count += len(product_result.errors)
        if product_result.warnings:
            result.warning_count += len(product_result.warnings)

        clf = classifications.get(product.id)
        is_composite = clf.wwf_is_composite if clf is not None else None
        is_own_brand = product.wwf_fields.is_own_brand if product.wwf_fields else None

        if is_composite is False:
            result.non_composite_count += 1
        elif is_own_brand is False:
            result.branded_composite_count += 1
        elif not product_result.errors:
            result.valid_product_count += 1

    return result


# ---------------------------------------------------------------------------
# Per-product validation
# ---------------------------------------------------------------------------


def _validate_product_entry(
    *,
    ext_id: str,
    entry: Any,
    product: NormalizedProduct | None,
    classifications: dict[UUID, WWFProductClassification],
) -> ProductIngredientResult:
    errors: list[IngredientRowError] = []
    warnings: list[str] = []
    valid_ingredients: list[WWFCompositeIngredient] = []

    if product is None:
        return ProductIngredientResult(
            external_product_id=ext_id,
            product_id=None,
            is_own_brand=None,
            is_composite=None,
            ingredient_count=0,
            valid_ingredient_count=0,
            total_attributed_weight_kg=Decimal("0"),
            product_weight_kg=None,
            residual_weight_kg=None,
            errors=(
                IngredientRowError(
                    ingredient_index=-1,
                    field="external_product_id",
                    message=f"product '{ext_id}' not found in project",
                ),
            ),
            warnings=(),
            valid_ingredients=(),
        )

    # Check methodology
    from altera_api.domain.common import Methodology

    if Methodology.WWF not in product.methodologies_enabled:
        return ProductIngredientResult(
            external_product_id=ext_id,
            product_id=product.id,
            is_own_brand=product.wwf_fields.is_own_brand if product.wwf_fields else None,
            is_composite=None,
            ingredient_count=0,
            valid_ingredient_count=0,
            total_attributed_weight_kg=Decimal("0"),
            product_weight_kg=product.weight_per_item_kg,
            residual_weight_kg=None,
            errors=(
                IngredientRowError(
                    ingredient_index=-1,
                    field="external_product_id",
                    message=f"product '{ext_id}' does not have WWF enabled",
                ),
            ),
            warnings=(),
            valid_ingredients=(),
        )

    clf = classifications.get(product.id)
    is_composite = clf.wwf_is_composite if clf is not None else None
    is_own_brand = product.wwf_fields.is_own_brand if product.wwf_fields else None

    # Classification must exist
    if clf is None:
        return ProductIngredientResult(
            external_product_id=ext_id,
            product_id=product.id,
            is_own_brand=is_own_brand,
            is_composite=None,
            ingredient_count=0,
            valid_ingredient_count=0,
            total_attributed_weight_kg=Decimal("0"),
            product_weight_kg=product.weight_per_item_kg,
            residual_weight_kg=None,
            errors=(
                IngredientRowError(
                    ingredient_index=-1,
                    field="classification",
                    message=f"product '{ext_id}' has no WWF classification; classify before uploading ingredients",
                ),
            ),
            warnings=(),
            valid_ingredients=(),
        )

    # Must be composite
    if not is_composite:
        return ProductIngredientResult(
            external_product_id=ext_id,
            product_id=product.id,
            is_own_brand=is_own_brand,
            is_composite=False,
            ingredient_count=0,
            valid_ingredient_count=0,
            total_attributed_weight_kg=Decimal("0"),
            product_weight_kg=product.weight_per_item_kg,
            residual_weight_kg=None,
            errors=(
                IngredientRowError(
                    ingredient_index=-1,
                    field="wwf_is_composite",
                    message=f"product '{ext_id}' is not a composite; Step 2 only applies to composite products",
                ),
            ),
            warnings=(),
            valid_ingredients=(),
        )

    # Branded composites get a warning, not an error
    if not is_own_brand:
        warnings.append(
            f"product '{ext_id}' is a branded composite; "
            "Step 2 attribution is not stored for branded products. "
            "They are reported at Step 1 (whole product weight) only."
        )

    raw_ingredients = entry.get("ingredients", []) if isinstance(entry, dict) else []
    ingredient_count = len(raw_ingredients)
    total_weight = Decimal("0")

    for i, raw_ing in enumerate(raw_ingredients):
        ingredient, ing_errors = _validate_ingredient(
            index=i,
            raw=raw_ing,
            parent_product_id=product.id,
        )
        if ing_errors:
            errors.extend(ing_errors)
        elif ingredient is not None and is_own_brand:
            valid_ingredients.append(ingredient)
            total_weight += ingredient.ingredient_weight_kg_per_item

    residual = product.weight_per_item_kg - total_weight
    if is_own_brand and total_weight > product.weight_per_item_kg:
        warnings.append(
            f"ingredient weights sum to {total_weight} kg but product weight is "
            f"{product.weight_per_item_kg} kg; sum exceeds product weight"
        )
        residual = Decimal("0")

    return ProductIngredientResult(
        external_product_id=ext_id,
        product_id=product.id,
        is_own_brand=is_own_brand,
        is_composite=is_composite,
        ingredient_count=ingredient_count,
        valid_ingredient_count=len(valid_ingredients),
        total_attributed_weight_kg=total_weight,
        product_weight_kg=product.weight_per_item_kg,
        residual_weight_kg=residual if is_own_brand else None,
        errors=tuple(errors),
        warnings=tuple(warnings),
        valid_ingredients=tuple(valid_ingredients),
    )


# ---------------------------------------------------------------------------
# Per-ingredient validation
# ---------------------------------------------------------------------------


def _validate_ingredient(
    *,
    index: int,
    raw: Any,
    parent_product_id: UUID,
) -> tuple[WWFCompositeIngredient | None, list[IngredientRowError]]:
    errors: list[IngredientRowError] = []

    if not isinstance(raw, dict):
        return None, [
            IngredientRowError(index, "ingredient", "each ingredient must be a JSON object")
        ]

    # food_group
    fg_value = raw.get("food_group")
    if fg_value is None:
        errors.append(IngredientRowError(index, "food_group", "food_group is required"))
        return None, errors

    try:
        fg = WWFFoodGroup(fg_value)
    except ValueError:
        valid = ", ".join(g.value for g in _STEP2_FOOD_GROUPS)
        errors.append(
            IngredientRowError(
                index,
                "food_group",
                f"'{fg_value}' is not a valid food group; allowed: {valid}",
            )
        )
        return None, errors

    if fg not in _STEP2_FOOD_GROUPS:
        errors.append(
            IngredientRowError(
                index,
                "food_group",
                f"'{fg.value}' is not valid for Step 2; "
                "only FG1..FG6 are allowed (FG7 is not decomposed at ingredient level)",
            )
        )
        return None, errors

    # subgroup — required for FG1/FG2, optional (ignored) for FG3-FG6
    subgroup_value: str | None = raw.get("subgroup")
    fg1_subgroup: WWFFG1Subgroup | None = None
    fg2_subgroup: WWFFG2Subgroup | None = None

    if fg is WWFFoodGroup.FG1:
        if subgroup_value is None:
            errors.append(
                IngredientRowError(
                    index,
                    "subgroup",
                    "subgroup is required for FG1; "
                    f"valid values: {', '.join(_VALID_FG1_SUBGROUPS)}",
                )
            )
        else:
            fg1_subgroup = _VALID_FG1_SUBGROUPS.get(subgroup_value)
            if fg1_subgroup is None:
                errors.append(
                    IngredientRowError(
                        index,
                        "subgroup",
                        f"'{subgroup_value}' is not a valid FG1 subgroup; "
                        f"valid values: {', '.join(_VALID_FG1_SUBGROUPS)}",
                    )
                )

    elif fg is WWFFoodGroup.FG2:
        if subgroup_value is None:
            errors.append(
                IngredientRowError(
                    index,
                    "subgroup",
                    "subgroup is required for FG2; "
                    f"valid values: {', '.join(_VALID_FG2_SUBGROUPS)}",
                )
            )
        else:
            fg2_subgroup = _VALID_FG2_SUBGROUPS.get(subgroup_value)
            if fg2_subgroup is None:
                errors.append(
                    IngredientRowError(
                        index,
                        "subgroup",
                        f"'{subgroup_value}' is not a valid FG2 subgroup; "
                        f"valid values: {', '.join(_VALID_FG2_SUBGROUPS)}",
                    )
                )

    if errors:
        return None, errors

    # ingredient_weight_kg_per_item
    weight_raw = raw.get("ingredient_weight_kg_per_item")
    if weight_raw is None:
        errors.append(
            IngredientRowError(
                index, "ingredient_weight_kg_per_item", "ingredient_weight_kg_per_item is required"
            )
        )
        return None, errors

    try:
        weight = Decimal(str(weight_raw))
    except InvalidOperation:
        errors.append(
            IngredientRowError(
                index,
                "ingredient_weight_kg_per_item",
                f"'{weight_raw}' is not a valid number",
            )
        )
        return None, errors

    if weight <= Decimal("0"):
        errors.append(
            IngredientRowError(
                index,
                "ingredient_weight_kg_per_item",
                "ingredient_weight_kg_per_item must be greater than 0",
            )
        )
        return None, errors

    try:
        ingredient = WWFCompositeIngredient(
            id=uuid4(),
            parent_product_id=parent_product_id,
            food_group=fg,
            fg1_subgroup=fg1_subgroup,
            fg2_subgroup=fg2_subgroup,
            ingredient_weight_kg_per_item=weight,
        )
    except ValueError as exc:
        errors.append(IngredientRowError(index, "ingredient", str(exc)))
        return None, errors

    return ingredient, []

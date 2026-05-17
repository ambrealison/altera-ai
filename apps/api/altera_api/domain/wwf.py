"""WWF domain models.

Mirrors docs/methodologies/wwf.md and docs/calculation/wwf-calculation.md.
Captures state and constraints; arithmetic lives in the calculation
module (Phase 10).

A note on WWF's unit: kilogrammes of product weight as sold, **not**
protein. The plant/animal split and the 50/50 default are PT concepts
and never apply here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import (
    ClassificationSource,
    DomainBase,
    Methodology,
    NonEmptyStr,
    Quantity,
)


class WWFFoodGroup(StrEnum):
    """The seven WWF food groups plus two system states."""

    FG1 = "FG1"
    FG2 = "FG2"
    FG3 = "FG3"
    FG4 = "FG4"
    FG5 = "FG5"
    FG6 = "FG6"
    FG7 = "FG7"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"

    @property
    def is_methodology_group(self) -> bool:
        return self.value.startswith("FG")


class WWFFG1Subgroup(StrEnum):
    """FG1 subgroups. Half are animal, half are plant."""

    RED_MEAT = "red_meat"
    POULTRY = "poultry"
    PROCESSED_MEATS_ALTERNATIVES = "processed_meats_alternatives"
    SEAFOOD = "seafood"
    EGGS = "eggs"
    NUTS_SEEDS = "nuts_seeds"
    LEGUMES = "legumes"
    ALTERNATIVE_PROTEIN_SOURCES = "alternative_protein_sources"
    MEAT_EGG_SEAFOOD_ALTERNATIVES = "meat_egg_seafood_alternatives"

    @property
    def is_animal(self) -> bool:
        return self in {
            WWFFG1Subgroup.RED_MEAT,
            WWFFG1Subgroup.POULTRY,
            WWFFG1Subgroup.PROCESSED_MEATS_ALTERNATIVES,
            WWFFG1Subgroup.SEAFOOD,
            WWFFG1Subgroup.EGGS,
        }

    @property
    def is_plant(self) -> bool:
        return not self.is_animal


class WWFFG2Subgroup(StrEnum):
    """FG2 reporting subgroup.

    Combines the methodology's `wwf_fg2_kind` (`dairy_animal` /
    `dairy_alternative_plant`) and `wwf_fg2_dairy_class` (`cheese` /
    `other`) into a single conceptual subgroup. The dairy-equivalent
    factor is derived from this:

    | Subgroup                  | Factor | Animal? |
    |---------------------------|-------:|---------|
    | cheese                    | 10     | yes     |
    | other_dairy_animal        | 1      | yes     |
    | dairy_alternative_plant   | 1      | no      |
    """

    CHEESE = "cheese"
    OTHER_DAIRY_ANIMAL = "other_dairy_animal"
    DAIRY_ALTERNATIVE_PLANT = "dairy_alternative_plant"

    @property
    def dairy_equivalent_factor(self) -> Decimal:
        return Decimal("10") if self is WWFFG2Subgroup.CHEESE else Decimal("1")

    @property
    def is_animal(self) -> bool:
        return self is not WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT


class WWFFG3Subgroup(StrEnum):
    """FG3 plant/animal fat split."""

    PLANT_BASED_FAT = "plant_based_fat"
    ANIMAL_BASED_FAT = "animal_based_fat"


class WWFFG5GrainKind(StrEnum):
    """FG5 whole-vs-refined grain split."""

    WHOLE_GRAIN = "whole_grain"
    REFINED_GRAIN = "refined_grain"


class WWFFG7SnackKind(StrEnum):
    """FG7 snack plant/animal split (not the focus of the protein transition)."""

    PLANT_BASED_SNACK = "plant_based_snack"
    ANIMAL_BASED_SNACK = "animal_based_snack"


class WWFCompositeStep1Bucket(StrEnum):
    """The four Step 1 composite buckets (assigned to whole composite weight)."""

    MEAT_BASED = "meat_based"
    SEAFOOD_BASED = "seafood_based"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"


class WWFProductClassification(DomainBase):
    """The most recent WWF classification of a product.

    The cross-field rules enforce that subgroup fields are present
    exactly when their parent food group is, and that
    `composite_step1_bucket` is present exactly when `wwf_is_composite`
    is true.
    """

    product_id: UUID
    wwf_food_group: WWFFoodGroup
    wwf_is_composite: bool
    fg1_subgroup: WWFFG1Subgroup | None = None
    fg2_subgroup: WWFFG2Subgroup | None = None
    fg3_subgroup: WWFFG3Subgroup | None = None
    fg5_grain_kind: WWFFG5GrainKind | None = None
    fg7_snack_kind: WWFFG7SnackKind | None = None
    composite_step1_bucket: WWFCompositeStep1Bucket | None = None
    source: ClassificationSource
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    rule_id: NonEmptyStr | None = None
    ai_prompt_version: NonEmptyStr | None = None
    ai_model: NonEmptyStr | None = None
    reviewer_user_id: UUID | None = None
    review_reason: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def _subgroup_matches_food_group(self) -> Self:
        # Required-when rules
        required = {
            WWFFoodGroup.FG1: ("fg1_subgroup", self.fg1_subgroup),
            WWFFoodGroup.FG2: ("fg2_subgroup", self.fg2_subgroup),
            WWFFoodGroup.FG3: ("fg3_subgroup", self.fg3_subgroup),
            WWFFoodGroup.FG5: ("fg5_grain_kind", self.fg5_grain_kind),
            WWFFoodGroup.FG7: ("fg7_snack_kind", self.fg7_snack_kind),
        }
        if self.wwf_food_group in required:
            field_name, value = required[self.wwf_food_group]
            if value is None:
                raise ValueError(f"{field_name} is required when wwf_food_group={self.wwf_food_group.value}.")
        # Forbidden-when rules — subgroups must be null when their parent food
        # group is not the current one.
        forbidden = {
            "fg1_subgroup": (WWFFoodGroup.FG1, self.fg1_subgroup),
            "fg2_subgroup": (WWFFoodGroup.FG2, self.fg2_subgroup),
            "fg3_subgroup": (WWFFoodGroup.FG3, self.fg3_subgroup),
            "fg5_grain_kind": (WWFFoodGroup.FG5, self.fg5_grain_kind),
            "fg7_snack_kind": (WWFFoodGroup.FG7, self.fg7_snack_kind),
        }
        for field_name, (parent, value) in forbidden.items():
            if value is not None and self.wwf_food_group is not parent:
                raise ValueError(
                    f"{field_name} must be null when wwf_food_group != {parent.value}."
                )
        return self

    @model_validator(mode="after")
    def _composite_bucket_required(self) -> Self:
        if self.wwf_is_composite and self.composite_step1_bucket is None:
            raise ValueError(
                "composite_step1_bucket is required when wwf_is_composite=true."
            )
        if not self.wwf_is_composite and self.composite_step1_bucket is not None:
            raise ValueError(
                "composite_step1_bucket must be null when wwf_is_composite=false."
            )
        return self

    @model_validator(mode="after")
    def _system_states_have_no_subgroups(self) -> Self:
        if not self.wwf_food_group.is_methodology_group:
            subgroups = (
                self.fg1_subgroup,
                self.fg2_subgroup,
                self.fg3_subgroup,
                self.fg5_grain_kind,
                self.fg7_snack_kind,
            )
            if any(s is not None for s in subgroups):
                raise ValueError(
                    "system states (out_of_scope, unknown) must have no subgroup fields."
                )
        return self

    @model_validator(mode="after")
    def _source_dependent_fields(self) -> Self:
        match self.source:
            case ClassificationSource.DETERMINISTIC:
                if self.rule_id is None:
                    raise ValueError("rule_id is required when source=deterministic.")
                if self.confidence != Decimal("1"):
                    raise ValueError("confidence must be 1 when source=deterministic.")
            case ClassificationSource.AI:
                if self.ai_prompt_version is None or self.ai_model is None:
                    raise ValueError("ai_prompt_version and ai_model are required when source=ai.")
            case ClassificationSource.MANUAL_REVIEW:
                if self.reviewer_user_id is None:
                    raise ValueError("reviewer_user_id is required when source=manual_review.")
        return self


class WWFCompositeIngredient(DomainBase):
    """A single Step 2 ingredient attribution.

    Step 2 applies only to own-brand composites. The ingredient is
    attributed to FG1..FG6 (FG7 is not a Step 2 target — snacks are not
    decomposed). The `parent_product_id` must reference a product
    classified as `wwf_is_composite=true` and `is_own_brand=true`; this
    cross-reference is enforced at the orchestration layer rather than
    here because it requires looking at another aggregate.
    """

    id: UUID
    parent_product_id: UUID
    food_group: WWFFoodGroup
    fg1_subgroup: WWFFG1Subgroup | None = None
    fg2_subgroup: WWFFG2Subgroup | None = None
    ingredient_weight_kg_per_item: Quantity

    @model_validator(mode="after")
    def _food_group_in_range(self) -> Self:
        if self.food_group in {WWFFoodGroup.FG7, WWFFoodGroup.OUT_OF_SCOPE, WWFFoodGroup.UNKNOWN}:
            raise ValueError(
                "Step 2 ingredients may only target FG1..FG6, not FG7 or system states."
            )
        return self

    @model_validator(mode="after")
    def _subgroups_match_food_group(self) -> Self:
        if self.food_group is WWFFoodGroup.FG1:
            if self.fg1_subgroup is None:
                raise ValueError("fg1_subgroup is required when food_group=FG1.")
            if self.fg2_subgroup is not None:
                raise ValueError("fg2_subgroup must be null when food_group=FG1.")
        elif self.food_group is WWFFoodGroup.FG2:
            if self.fg2_subgroup is None:
                raise ValueError("fg2_subgroup is required when food_group=FG2.")
            if self.fg1_subgroup is not None:
                raise ValueError("fg1_subgroup must be null when food_group=FG2.")
        else:
            if self.fg1_subgroup is not None or self.fg2_subgroup is not None:
                raise ValueError(
                    f"fg1_subgroup / fg2_subgroup must be null when food_group="
                    f"{self.food_group.value}."
                )
        return self


class WWFCalculationRow(DomainBase):
    """Per-product calculation outputs for a WWF run."""

    run_id: UUID
    product_id: UUID
    in_scope: bool
    wwf_food_group: WWFFoodGroup
    wwf_subgroup_label: NonEmptyStr | None = None
    weight_kg: Quantity
    weight_kg_dairy_equiv: Quantity | None = None
    wwf_is_composite: bool
    wwf_composite_step1_bucket: WWFCompositeStep1Bucket | None = None
    methodology_version: NonEmptyStr
    methodology_source_edition: NonEmptyStr
    taxonomy_version: NonEmptyStr
    rules_version: NonEmptyStr

    @model_validator(mode="after")
    def _in_scope_matches_group(self) -> Self:
        is_real = self.wwf_food_group.is_methodology_group
        if self.in_scope != is_real:
            raise ValueError(
                f"in_scope={self.in_scope} disagrees with wwf_food_group="
                f"{self.wwf_food_group.value}."
            )
        return self

    @model_validator(mode="after")
    def _composite_bucket_iff_composite(self) -> Self:
        if self.wwf_is_composite and self.wwf_composite_step1_bucket is None:
            raise ValueError(
                "wwf_composite_step1_bucket is required when wwf_is_composite=true."
            )
        if not self.wwf_is_composite and self.wwf_composite_step1_bucket is not None:
            raise ValueError(
                "wwf_composite_step1_bucket must be null when wwf_is_composite=false."
            )
        return self

    @model_validator(mode="after")
    def _dairy_equiv_only_for_fg2(self) -> Self:
        if self.wwf_food_group is not WWFFoodGroup.FG2 and self.weight_kg_dairy_equiv is not None:
            raise ValueError("weight_kg_dairy_equiv may only be set for FG2 rows.")
        if self.wwf_food_group is WWFFoodGroup.FG2 and self.weight_kg_dairy_equiv is None:
            raise ValueError("weight_kg_dairy_equiv is required for FG2 rows.")
        return self


class WWFFoodGroupAggregate(DomainBase):
    food_group: WWFFoodGroup
    weight_kg: Quantity
    weight_kg_dairy_equiv: Quantity | None = None
    share_pct: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    phd_reference_share_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))


class WWFCalculationSummary(DomainBase):
    """Headline figures for a single WWF run."""

    run_id: UUID
    reporting_period_label: NonEmptyStr
    per_food_group: tuple[WWFFoodGroupAggregate, ...]
    total_sales_weight_in_scope_kg: Quantity
    composites_total_weight_kg: Quantity
    composites_meat_based_kg: Quantity
    composites_seafood_based_kg: Quantity
    composites_vegetarian_kg: Quantity
    composites_vegan_kg: Quantity
    whole_diet_plant_weight_kg: Quantity
    whole_diet_animal_weight_kg: Quantity
    out_of_scope_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    methodology: Methodology = Methodology.WWF
    methodology_version: NonEmptyStr
    methodology_source_edition: NonEmptyStr
    taxonomy_version: NonEmptyStr
    rules_version: NonEmptyStr

    @model_validator(mode="after")
    def _methodology_fixed(self) -> Self:
        if self.methodology is not Methodology.WWF:
            raise ValueError("WWFCalculationSummary.methodology must be wwf.")
        return self

    @model_validator(mode="after")
    def _per_food_group_unique(self) -> Self:
        seen = [a.food_group for a in self.per_food_group]
        if len(seen) != len(set(seen)):
            raise ValueError("per_food_group contains duplicate food_group entries.")
        return self

    @model_validator(mode="after")
    def _composite_buckets_sum_to_total(self) -> Self:
        total = (
            self.composites_meat_based_kg
            + self.composites_seafood_based_kg
            + self.composites_vegetarian_kg
            + self.composites_vegan_kg
        )
        if total != self.composites_total_weight_kg:
            raise ValueError(
                "composite step-1 buckets must sum to composites_total_weight_kg "
                f"(got {total} vs {self.composites_total_weight_kg})."
            )
        return self

"""Rule schema.

Each rule belongs to exactly one methodology. The rule's category is a
single PT group (a string) or a structured WWF object whose cross-field
constraints are enforced here so an invalid rule fails to load — before
any product is classified.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from altera_api.domain.common import Methodology, NonEmptyStr
from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
)


class _RuleBaseModel(BaseModel):
    """Pydantic base for rule schemas.

    Same forbid-extras + frozen as ``DomainBase`` but with strict mode
    *off*: rules come from YAML, so list-of-strings should coerce into
    ``tuple[str, ...]`` and strings should coerce into enum members.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        populate_by_name=True,
    )


class ConditionNode(_RuleBaseModel):
    """A node in a match/exclude tree.

    A node is either a *leaf* (exactly one field-matching condition) or
    a *group* (`any_of` / `all_of`). Mixing the two forms in one node is
    an error.
    """

    # --- Leaf conditions ---
    product_name_contains: tuple[str, ...] | None = None
    brand_in: tuple[str, ...] | None = None
    taxonomy_node: NonEmptyStr | None = None
    labels_contains: tuple[str, ...] | None = None
    language_in: tuple[str, ...] | None = None
    country_in: tuple[str, ...] | None = None
    ingredients_contains: tuple[str, ...] | None = None

    # --- Group conditions (recursive) ---
    any_of: tuple[ConditionNode, ...] | None = None
    all_of: tuple[ConditionNode, ...] | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Self:
        leaves = (
            self.product_name_contains,
            self.brand_in,
            self.taxonomy_node,
            self.labels_contains,
            self.language_in,
            self.country_in,
            self.ingredients_contains,
        )
        leaf_count = sum(1 for x in leaves if x is not None)
        group_count = sum(1 for x in (self.any_of, self.all_of) if x is not None)
        total = leaf_count + group_count
        if total == 0:
            raise ValueError("ConditionNode must set exactly one field; got none.")
        if total > 1:
            raise ValueError("ConditionNode must set exactly one field; cannot combine forms.")
        return self


class WWFRuleCategory(_RuleBaseModel):
    """The structured `category` value carried by a WWF rule.

    Mirrors the cross-field constraints from
    :class:`altera_api.domain.wwf.WWFProductClassification` but without
    the source/confidence fields — those are stamped by the engine.
    """

    wwf_food_group: WWFFoodGroup
    wwf_is_composite: bool = False
    wwf_fg1_subgroup: WWFFG1Subgroup | None = None
    wwf_fg2_subgroup: WWFFG2Subgroup | None = None
    wwf_fg3_subgroup: WWFFG3Subgroup | None = None
    wwf_fg5_grain_kind: WWFFG5GrainKind | None = None
    wwf_fg7_snack_kind: WWFFG7SnackKind | None = None
    wwf_composite_step1_bucket: WWFCompositeStep1Bucket | None = None

    @model_validator(mode="after")
    def _subgroup_required_for_food_group(self) -> Self:
        required = {
            WWFFoodGroup.FG1: ("wwf_fg1_subgroup", self.wwf_fg1_subgroup),
            WWFFoodGroup.FG2: ("wwf_fg2_subgroup", self.wwf_fg2_subgroup),
            WWFFoodGroup.FG3: ("wwf_fg3_subgroup", self.wwf_fg3_subgroup),
            WWFFoodGroup.FG5: ("wwf_fg5_grain_kind", self.wwf_fg5_grain_kind),
            WWFFoodGroup.FG7: ("wwf_fg7_snack_kind", self.wwf_fg7_snack_kind),
        }
        if self.wwf_food_group in required:
            field_name, value = required[self.wwf_food_group]
            if value is None:
                raise ValueError(
                    f"{field_name} is required when wwf_food_group={self.wwf_food_group.value}."
                )
        return self

    @model_validator(mode="after")
    def _subgroup_forbidden_for_other_food_group(self) -> Self:
        pairs = (
            ("wwf_fg1_subgroup", self.wwf_fg1_subgroup, WWFFoodGroup.FG1),
            ("wwf_fg2_subgroup", self.wwf_fg2_subgroup, WWFFoodGroup.FG2),
            ("wwf_fg3_subgroup", self.wwf_fg3_subgroup, WWFFoodGroup.FG3),
            ("wwf_fg5_grain_kind", self.wwf_fg5_grain_kind, WWFFoodGroup.FG5),
            ("wwf_fg7_snack_kind", self.wwf_fg7_snack_kind, WWFFoodGroup.FG7),
        )
        for name, value, parent in pairs:
            if value is not None and self.wwf_food_group is not parent:
                raise ValueError(f"{name} must be null when wwf_food_group != {parent.value}.")
        return self

    @model_validator(mode="after")
    def _composite_bucket_iff_composite(self) -> Self:
        if self.wwf_is_composite and self.wwf_composite_step1_bucket is None:
            raise ValueError("wwf_composite_step1_bucket is required when wwf_is_composite=true.")
        if not self.wwf_is_composite and self.wwf_composite_step1_bucket is not None:
            raise ValueError("wwf_composite_step1_bucket must be null when wwf_is_composite=false.")
        return self


class _RuleBase(_RuleBaseModel):
    """Common rule fields. Use ``PTRule`` or ``WWFRule`` in code."""

    id: NonEmptyStr
    methodology: Methodology
    priority: int = Field(default=1000, ge=0)
    match: ConditionNode
    exclude: ConditionNode | None = None
    notes: str | None = None


class PTRule(_RuleBase):
    methodology: Literal[Methodology.PROTEIN_TRACKER] = Methodology.PROTEIN_TRACKER
    category: ProteinTrackerGroup

    @model_validator(mode="after")
    def _category_is_real_group(self) -> Self:
        if not self.category.is_methodology_group:
            raise ValueError("PT rules cannot target system states (out_of_scope, unknown).")
        return self


class WWFRule(_RuleBase):
    methodology: Literal[Methodology.WWF] = Methodology.WWF
    category: WWFRuleCategory

    @model_validator(mode="after")
    def _category_is_real_group(self) -> Self:
        if not self.category.wwf_food_group.is_methodology_group:
            raise ValueError("WWF rules cannot target system states (out_of_scope, unknown).")
        return self


Rule = PTRule | WWFRule

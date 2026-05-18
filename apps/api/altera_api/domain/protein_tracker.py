"""Protein Tracker domain models.

Mirrors the canonical methodology described in
docs/methodologies/protein-tracker.md and the calculation rules in
docs/calculation/protein-tracker-calculation.md.

These models capture state — no arithmetic is performed here. The
calculation module (Phase 9) will read inputs and produce
`ProteinTrackerCalculationRow` and `ProteinTrackerCalculationSummary`
instances.
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


class ProteinTrackerGroup(StrEnum):
    """The four headline PT groups plus two system states."""

    PLANT_BASED_CORE = "plant_based_core"
    PLANT_BASED_NON_CORE = "plant_based_non_core"
    COMPOSITE_PRODUCTS = "composite_products"
    ANIMAL_CORE = "animal_core"
    # System states (pre-resolution or excluded)
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"

    @property
    def is_methodology_group(self) -> bool:
        """True for the four real PT groups; false for system states."""
        return self in {
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        }

    @property
    def is_plant_side(self) -> bool:
        return self in {
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        }

    @property
    def is_animal_side(self) -> bool:
        return self is ProteinTrackerGroup.ANIMAL_CORE


class ProteinTrackerProductClassification(DomainBase):
    """The most recent PT classification of a product.

    There is at most one `ProteinTrackerProductClassification` per
    product. Prior classifications are recorded as `AuditEvent`s with
    `action=classification.*` and on `classification_events`.
    """

    product_id: UUID
    pt_group: ProteinTrackerGroup
    source: ClassificationSource
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    rule_id: NonEmptyStr | None = None
    ai_prompt_version: NonEmptyStr | None = None
    ai_model: NonEmptyStr | None = None
    reviewer_user_id: UUID | None = None
    review_reason: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def _source_dependent_fields(self) -> Self:
        match self.source:
            case ClassificationSource.DETERMINISTIC:
                if self.rule_id is None:
                    raise ValueError("rule_id is required when source=deterministic.")
                if self.confidence != Decimal("1"):
                    raise ValueError("confidence must be 1 when source=deterministic.")
                if self.ai_prompt_version is not None or self.ai_model is not None:
                    raise ValueError("ai_* fields must be null when source=deterministic.")
                if self.reviewer_user_id is not None:
                    raise ValueError("reviewer_user_id must be null when source=deterministic.")
            case ClassificationSource.AI:
                if self.ai_prompt_version is None or self.ai_model is None:
                    raise ValueError("ai_prompt_version and ai_model are required when source=ai.")
                if self.rule_id is not None:
                    raise ValueError("rule_id must be null when source=ai.")
                if self.reviewer_user_id is not None:
                    raise ValueError("reviewer_user_id must be null when source=ai.")
            case ClassificationSource.MANUAL_REVIEW:
                if self.reviewer_user_id is None:
                    raise ValueError("reviewer_user_id is required when source=manual_review.")
        return self


class ProteinTrackerCalculationRow(DomainBase):
    """The per-product calculation outputs for a PT run.

    Values follow the formulas in
    docs/calculation/protein-tracker-calculation.md. This model carries
    the values; it does not compute them.
    """

    run_id: UUID
    product_id: UUID
    in_scope: bool
    pt_group: ProteinTrackerGroup
    volume_kg: Quantity
    protein_pct: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    protein_kg: Quantity
    used_per_product_split: bool
    plant_protein_kg: Quantity | None = None
    animal_protein_kg: Quantity | None = None
    methodology_version: NonEmptyStr
    methodology_source_edition: NonEmptyStr
    taxonomy_version: NonEmptyStr
    rules_version: NonEmptyStr

    @model_validator(mode="after")
    def _in_scope_matches_group(self) -> Self:
        is_real = self.pt_group.is_methodology_group
        if self.in_scope != is_real:
            raise ValueError(
                f"in_scope={self.in_scope} disagrees with pt_group={self.pt_group} "
                "(system states are out of scope)."
            )
        return self

    @model_validator(mode="after")
    def _split_consistency(self) -> Self:
        if self.used_per_product_split:
            if self.plant_protein_kg is None or self.animal_protein_kg is None:
                raise ValueError(
                    "plant_protein_kg and animal_protein_kg are required when "
                    "used_per_product_split=true."
                )
            if self.pt_group is not ProteinTrackerGroup.COMPOSITE_PRODUCTS:
                raise ValueError(
                    "used_per_product_split=true only applies to pt_group=composite_products."
                )
        else:
            if self.plant_protein_kg is not None or self.animal_protein_kg is not None:
                raise ValueError(
                    "plant_protein_kg / animal_protein_kg must be null when "
                    "used_per_product_split=false."
                )
        return self

    @model_validator(mode="after")
    def _out_of_scope_has_zero_protein(self) -> Self:
        if not self.in_scope and self.protein_kg != Decimal("0"):
            raise ValueError("out-of-scope rows must have protein_kg=0.")
        return self


class ProteinTrackerGroupAggregate(DomainBase):
    pt_group: ProteinTrackerGroup
    volume_kg: Quantity
    protein_kg: Quantity
    item_count: int = Field(ge=0)


class ProteinTrackerCalculationSummary(DomainBase):
    """Headline figures for a single PT run."""

    run_id: UUID
    reporting_period_label: NonEmptyStr
    per_group: tuple[ProteinTrackerGroupAggregate, ...]
    plant_protein_kg: Quantity
    animal_protein_kg: Quantity
    total_in_scope_protein_kg: Quantity
    plant_share_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    animal_share_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    rows_with_per_product_split: int = Field(ge=0)
    rows_protein_source_label: int = Field(ge=0)
    rows_protein_source_reference_db: int = Field(ge=0)
    out_of_scope_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    # Phase 23C — enrichment usage counters (default=0/False for backward compat)
    use_enriched_nutrition: bool = False
    enriched_nutrition_used_count: int = Field(default=0, ge=0)
    manual_enrichment_used_count: int = Field(default=0, ge=0)
    category_average_used_count: int = Field(default=0, ge=0)
    missing_protein_after_enrichment_count: int = Field(default=0, ge=0)
    methodology: Methodology = Methodology.PROTEIN_TRACKER
    methodology_version: NonEmptyStr
    methodology_source_edition: NonEmptyStr
    taxonomy_version: NonEmptyStr
    rules_version: NonEmptyStr

    @model_validator(mode="after")
    def _methodology_fixed(self) -> Self:
        if self.methodology is not Methodology.PROTEIN_TRACKER:
            raise ValueError(
                "ProteinTrackerCalculationSummary.methodology must be protein_tracker."
            )
        return self

    @model_validator(mode="after")
    def _share_nullness_consistent(self) -> Self:
        both_none = self.plant_share_pct is None and self.animal_share_pct is None
        both_set = self.plant_share_pct is not None and self.animal_share_pct is not None
        if not (both_none or both_set):
            raise ValueError("plant_share_pct and animal_share_pct must be both null or both set.")
        if both_none and self.total_in_scope_protein_kg != Decimal("0"):
            raise ValueError("Shares can only be null when total_in_scope_protein_kg is 0.")
        return self

    @model_validator(mode="after")
    def _per_group_unique(self) -> Self:
        seen = [a.pt_group for a in self.per_group]
        if len(seen) != len(set(seen)):
            raise ValueError("per_group contains duplicate pt_group entries.")
        return self

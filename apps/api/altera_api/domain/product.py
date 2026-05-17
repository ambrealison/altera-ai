"""Product models.

`RawProduct` is what we read from a CSV row (headers normalised, but no
methodology semantics). `NormalizedProduct` is what lives in the
database after validation, with optional PT-specific and WWF-specific
field blocks attached.

These models do not classify products; classification is a separate
concern in `protein_tracker.py` / `wwf.py`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import (
    Country,
    DomainBase,
    Language,
    Methodology,
    NonEmptyStr,
    Quantity,
)


class RetailChannel(StrEnum):
    """WWF retail-channel facet. Not used by PT."""

    FRESH = "fresh"
    GROCERY_AMBIENT = "grocery_ambient"
    FROZEN = "frozen"


class ProteinSource(StrEnum):
    """Where the `protein_pct` value came from. PT only."""

    LABEL = "label"
    REFERENCE_DB = "reference_db"


class RawProduct(DomainBase):
    """A single row from a CSV upload, headers normalised, no semantics yet.

    Commercially sensitive fields are dropped at the ingestion boundary
    before reaching this model. See docs/data/input-formats.md.
    """

    upload_id: UUID
    row_number: int = Field(ge=1)
    external_product_id: NonEmptyStr
    product_name: NonEmptyStr
    brand: str | None = None
    is_own_brand: bool | None = None
    retailer_category: str | None = None
    retailer_subcategory: str | None = None
    ingredients_text: str | None = None
    labels: tuple[str, ...] = ()
    language: Language | None = None
    country: Country | None = None
    retail_channel: RetailChannel | None = None
    weight_per_item_kg: Quantity | None = None
    items_purchased: Quantity | None = None
    items_sold: Quantity | None = None
    # PT-only raw fields
    protein_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    protein_source: ProteinSource | None = None
    plant_protein_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    animal_protein_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))


class PTProductFields(DomainBase):
    """Protein-Tracker-specific block on a normalised product."""

    items_purchased: Quantity
    protein_pct: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    protein_source: ProteinSource = ProteinSource.REFERENCE_DB
    plant_protein_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    animal_protein_pct: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))

    @model_validator(mode="after")
    def _split_either_both_or_neither(self) -> Self:
        if (self.plant_protein_pct is None) != (self.animal_protein_pct is None):
            raise ValueError(
                "plant_protein_pct and animal_protein_pct must be provided together "
                "or both omitted."
            )
        return self


class WWFProductFields(DomainBase):
    """WWF-specific block on a normalised product."""

    items_sold: Quantity
    retail_channel: RetailChannel
    is_own_brand: bool


class NormalizedProduct(DomainBase):
    """Validated, normalised product persisted to the `products` table.

    Per-methodology required quantities live on optional blocks. The
    `methodology_blocks` map carries one entry per methodology enabled
    on the project. Cross-field validation ensures the right block is
    present for each enabled methodology.
    """

    id: UUID
    upload_id: UUID
    project_id: UUID
    organisation_id: UUID
    row_number: int = Field(ge=1)
    external_product_id: NonEmptyStr
    product_name: NonEmptyStr
    brand: str | None = None
    is_own_brand: bool | None = None
    retailer_category: str | None = None
    retailer_subcategory: str | None = None
    ingredients_text: str | None = None
    labels: tuple[str, ...] = ()
    language: Language | None = None
    country: Country | None = None
    weight_per_item_kg: Quantity
    methodologies_enabled: frozenset[Methodology] = Field(min_length=1)
    pt_fields: PTProductFields | None = None
    wwf_fields: WWFProductFields | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _required_blocks_present(self) -> Self:
        if Methodology.PROTEIN_TRACKER in self.methodologies_enabled and self.pt_fields is None:
            raise ValueError("protein_tracker is enabled but pt_fields is missing.")
        if Methodology.WWF in self.methodologies_enabled and self.wwf_fields is None:
            raise ValueError("wwf is enabled but wwf_fields is missing.")
        if Methodology.PROTEIN_TRACKER not in self.methodologies_enabled and self.pt_fields is not None:
            raise ValueError("pt_fields is present but protein_tracker is not enabled.")
        if Methodology.WWF not in self.methodologies_enabled and self.wwf_fields is not None:
            raise ValueError("wwf_fields is present but wwf is not enabled.")
        return self

    @model_validator(mode="after")
    def _is_own_brand_required_for_wwf(self) -> Self:
        if Methodology.WWF in self.methodologies_enabled and self.is_own_brand is None:
            raise ValueError("is_own_brand is required when wwf is enabled.")
        return self

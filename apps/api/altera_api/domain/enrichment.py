"""Nutrition enrichment domain models (Phase 23A).

Enrichment records live separately from retailer-provided product data.
They never silently overwrite existing values. The Protein Tracker
calculation always reads ``NormalizedProduct.pt_fields.protein_pct``
directly — enriched values are only applied when a later pipeline step
explicitly copies them into the product record.

Separation of concerns:
  * ``original_value``  — the retailer-supplied value, immutable.
  * ``enriched_value``  — what the enrichment source found; may be None.
  * The product model is never mutated by the enrichment system.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from altera_api.domain.common import DomainBase

#: Confidence score in the closed interval [0, 1].
Confidence = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]


class NutritionEnrichmentSource(StrEnum):
    """Where an enriched nutrition value came from."""

    RETAILER_PROVIDED = "retailer_provided"
    OPEN_FOOD_FACTS = "open_food_facts"  # planned — not yet implemented
    CIQUAL = "ciqual"                    # planned — not yet implemented
    OQALI = "oqali"                      # planned — not yet implemented
    NEVO = "nevo"                        # planned — not yet implemented
    CATEGORY_AVERAGE = "category_average"
    MANUAL_ALTERA = "manual_altera"
    UNKNOWN = "unknown"


class NutritionEnrichmentStatus(StrEnum):
    """Lifecycle status of one enrichment record."""

    NOT_NEEDED = "not_needed"          # retailer value present; no action required
    NEEDED = "needed"                  # value absent; enrichment should be attempted
    ENRICHED = "enriched"              # enrichment successfully applied
    FAILED = "failed"                  # enrichment attempted; no value found
    NEEDS_MANUAL_REVIEW = "needs_manual_review"  # conflicting or low-confidence result


class NutritionEnrichmentRecord(DomainBase):
    """One enrichment observation for a single nutrient on a single product.

    ``original_value`` holds the retailer-supplied number if one exists;
    it is never modified after creation. ``enriched_value`` is the value
    the enrichment source returned; it may be ``None`` when status is
    ``NEEDED`` or ``FAILED``.

    The Protein Tracker calculation reads ``pt_fields.protein_pct`` from
    ``NormalizedProduct``, not ``enriched_value`` from this record. To
    apply enrichment to a calculation, a separate pipeline step must
    explicitly propagate ``enriched_value`` into the product record.
    """

    product_id: UUID
    nutrient: str                        # e.g. "protein_pct"
    original_value: Decimal | None       # retailer-provided; immutable
    enriched_value: Decimal | None       # enrichment result; None until found
    unit: str                            # e.g. "g_per_100g"
    source: NutritionEnrichmentSource
    confidence: Confidence | None        # 0–1; None if not applicable
    status: NutritionEnrichmentStatus
    rationale: str
    created_at: datetime
    created_by: UUID | None = None       # None for automated enrichment
    # Phase 33I-AI — how the reference for this record was picked.
    # "deterministic" — exact/alias/token match on the reference table
    # "ai_assisted"   — LLM picked the reference from a deterministic
    #                   candidate shortlist. The protein VALUE still
    #                   comes from the reference row, not from the AI.
    # "manual"        — Altera staff entered the value via the manual
    #                   enrichment endpoint.
    match_method: str = "deterministic"

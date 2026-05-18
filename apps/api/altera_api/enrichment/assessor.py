"""Protein enrichment needs assessor (Phase 23A).

Examines ``NormalizedProduct`` records and emits ``NutritionEnrichmentRecord``
objects describing each product's enrichment status. Pure function — no I/O,
no store access, no external calls.

Assessment rules (protein_pct only):
  * PT-enabled product with protein_pct present → NOT_NEEDED / RETAILER_PROVIDED.
  * PT-enabled product with protein_pct absent  → NEEDED / UNKNOWN.
  * Non-PT products are omitted from results.

The assessor never modifies the product; ``enriched_value`` is always
``None`` in the output. A downstream step (Phase 23B+) will look up
external sources and create ENRICHED records.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.product import NormalizedProduct

_PROTEIN_NUTRIENT = "protein_pct"
_UNIT = "g_per_100g"


def assess_protein_enrichment_needs(
    products: Sequence[NormalizedProduct],
    *,
    now: datetime,
    created_by: UUID | None = None,
) -> list[NutritionEnrichmentRecord]:
    """Return one enrichment status record per PT-enabled product.

    Products without PT enabled are silently skipped.
    Results are in the same order as ``products``.
    """
    records: list[NutritionEnrichmentRecord] = []

    for product in products:
        if Methodology.PROTEIN_TRACKER not in product.methodologies_enabled:
            continue

        if product.pt_fields is not None and product.pt_fields.protein_pct is not None:
            records.append(
                NutritionEnrichmentRecord(
                    product_id=product.id,
                    nutrient=_PROTEIN_NUTRIENT,
                    original_value=product.pt_fields.protein_pct,
                    enriched_value=None,
                    unit=_UNIT,
                    source=NutritionEnrichmentSource.RETAILER_PROVIDED,
                    confidence=Decimal("1.00"),
                    status=NutritionEnrichmentStatus.NOT_NEEDED,
                    rationale=(
                        "Retailer-provided protein_pct is present; "
                        "enrichment is not needed."
                    ),
                    created_at=now,
                    created_by=created_by,
                )
            )
        else:
            records.append(
                NutritionEnrichmentRecord(
                    product_id=product.id,
                    nutrient=_PROTEIN_NUTRIENT,
                    original_value=None,
                    enriched_value=None,
                    unit=_UNIT,
                    source=NutritionEnrichmentSource.UNKNOWN,
                    confidence=None,
                    status=NutritionEnrichmentStatus.NEEDED,
                    rationale=(
                        "protein_pct is absent from retailer data; "
                        "enrichment from an external or manual source is needed."
                    ),
                    created_at=now,
                    created_by=created_by,
                )
            )

    return records

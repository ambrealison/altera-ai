"""Enrichment record selection logic (Phase 23C).

Pure function — no I/O, no store access.

Priority order (lower = preferred):
  0  MANUAL_ALTERA      — Altera team override; highest non-retailer trust
  1  CATEGORY_AVERAGE   — statistical fallback

Only records with ``status=ENRICHED`` and a non-None ``enriched_value``
are eligible. NEEDED / FAILED / NEEDS_MANUAL_REVIEW records are ignored.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

#: Lower priority number = preferred source.
_SOURCE_PRIORITY: dict[NutritionEnrichmentSource, int] = {
    NutritionEnrichmentSource.MANUAL_ALTERA: 0,
    NutritionEnrichmentSource.CATEGORY_AVERAGE: 1,
}


def select_protein_enrichment(
    records: Sequence[NutritionEnrichmentRecord],
) -> tuple[Decimal, NutritionEnrichmentSource] | None:
    """Return ``(enriched_value, source)`` for the best eligible record, or ``None``.

    Filters to records with:
      - ``nutrient == "protein_pct"``
      - ``status == ENRICHED``
      - ``enriched_value is not None``

    Among eligible records selects by ``_SOURCE_PRIORITY`` (manual_altera
    first, category_average second). Unknown sources are ranked last.
    Returns ``None`` if no eligible record exists.
    """
    eligible = [
        r
        for r in records
        if r.nutrient == "protein_pct"
        and r.status is NutritionEnrichmentStatus.ENRICHED
        and r.enriched_value is not None
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda r: _SOURCE_PRIORITY.get(r.source, 99))
    best = eligible[0]
    assert best.enriched_value is not None  # narrowed above
    return best.enriched_value, best.source

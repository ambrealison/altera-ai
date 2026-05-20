"""Enrichment record selection logic (Phase 23C / 33A / 33E / 33H).

Pure function — no I/O, no store access.

Priority order (lower = preferred):
  0  MANUAL_ALTERA      — Altera team override; highest non-retailer trust
  1  NEVO               — RIVM 2025 v9.0; supplies total + plant/animal split
  2  CIQUAL             — ANSES; total protein only (no plant/animal split)
  3  CATEGORY_AVERAGE   — statistical fallback

Only records with ``status=ENRICHED`` and a non-None ``enriched_value``
are eligible. NEEDED / FAILED / NEEDS_MANUAL_REVIEW records are ignored.

Phase 33H adds plant/animal split surfacing: when the selected source
also published ``plant_protein_pct`` and ``animal_protein_pct``
enrichment records (sibling NutritionEnrichmentRecord rows on the same
product, with the same source), those values are returned alongside
the total so the calculation can use a true split instead of falling
back to the Protein Tracker classification assumption.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

#: Lower priority number = preferred source.
_SOURCE_PRIORITY: dict[NutritionEnrichmentSource, int] = {
    NutritionEnrichmentSource.MANUAL_ALTERA: 0,
    NutritionEnrichmentSource.NEVO: 1,
    NutritionEnrichmentSource.CIQUAL: 2,
    NutritionEnrichmentSource.CATEGORY_AVERAGE: 3,
}


@dataclass(frozen=True)
class ResolvedProteinEnrichment:
    """The protein values resolved for one product, with provenance.

    ``plant_protein_pct`` and ``animal_protein_pct`` are populated only
    when the selected source published a complete plant/animal split via
    sibling enrichment records (e.g. NEVO PROTPL/PROTAN). When either is
    missing — as for CIQUAL, category_average, or NEVO entries without
    PROTPL — both are None and the calculation falls back to the
    Protein Tracker classification assumption.
    """

    protein_pct: Decimal
    source: NutritionEnrichmentSource
    plant_protein_pct: Decimal | None = None
    animal_protein_pct: Decimal | None = None


def select_protein_enrichment(
    records: Sequence[NutritionEnrichmentRecord],
) -> ResolvedProteinEnrichment | None:
    """Return the resolved protein/split values for the best eligible record.

    Selects the ``nutrient="protein_pct"`` record with the lowest source
    priority. If that source also has sibling ``"plant_protein_pct"`` and
    ``"animal_protein_pct"`` ENRICHED records on the same product, they
    are returned alongside; otherwise only the total is returned.
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

    # Sibling split records — same source, ENRICHED, non-None value.
    plant = _sibling_value(records, "plant_protein_pct", best.source)
    animal = _sibling_value(records, "animal_protein_pct", best.source)
    return ResolvedProteinEnrichment(
        protein_pct=best.enriched_value,
        source=best.source,
        plant_protein_pct=plant,
        animal_protein_pct=animal,
    )


def _sibling_value(
    records: Sequence[NutritionEnrichmentRecord],
    nutrient: str,
    source: NutritionEnrichmentSource,
) -> Decimal | None:
    for r in records:
        if (
            r.nutrient == nutrient
            and r.source is source
            and r.status is NutritionEnrichmentStatus.ENRICHED
            and r.enriched_value is not None
        ):
            return r.enriched_value
    return None

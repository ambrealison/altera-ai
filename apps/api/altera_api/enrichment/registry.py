"""Static source registry for nutrition enrichment (Phase 23A / 33A).

Lists every planned and currently-available enrichment source with its
metadata. External sources are registered here so the system can reason
about the enrichment pipeline even before implementations exist.

Priority ordering (lower number = higher preference when multiple sources
have data for the same product/nutrient):

  0  retailer_provided  — authoritative; never overwritten
  1  manual_altera      — Altera team override; highest non-retailer trust
  2  ciqual             — ANSES reference database; analytically measured
  3  category_average   — statistical fallback; available now
  4  open_food_facts    — planned external
  5  oqali              — planned external (French surveillance DB)
  6  nevo               — planned external (RIVM, Netherlands)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from altera_api.domain.enrichment import NutritionEnrichmentSource


@dataclass(frozen=True)
class EnrichmentSourceInfo:
    """Descriptor for one enrichment data source."""

    source: NutritionEnrichmentSource
    priority: int               # lower = higher preference
    is_external: bool           # True if requires an external API call
    is_available: bool          # True if a working implementation exists
    expected_confidence: Decimal | None  # typical 0–1 confidence for this source
    notes: str


ENRICHMENT_SOURCE_REGISTRY: tuple[EnrichmentSourceInfo, ...] = (
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.RETAILER_PROVIDED,
        priority=0,
        is_external=False,
        is_available=True,
        expected_confidence=Decimal("1.00"),
        notes=(
            "Retailer-provided label data. Highest authority. "
            "Never overwritten by enrichment — status is always NOT_NEEDED."
        ),
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.MANUAL_ALTERA,
        priority=1,
        is_external=False,
        is_available=True,
        expected_confidence=Decimal("0.90"),
        notes="Manually entered by the Altera methodology team via the review UI.",
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.CIQUAL,
        priority=2,
        is_external=False,  # data is imported locally; no runtime API calls
        is_available=True,
        expected_confidence=Decimal("0.80"),
        notes=(
            "ANSES CIQUAL French food composition table (2025). "
            "Imported via import_ciqual.py; no runtime external calls. "
            "Exact name match (0.80) or food-group average (0.55). "
            "Attribution: Anses. 2025. Ciqual French food composition table."
        ),
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
        priority=3,
        is_external=False,
        is_available=True,
        expected_confidence=Decimal("0.60"),
        notes=(
            "Category-level protein average used as a statistical fallback. "
            "Accuracy varies with category breadth; use with caution."
        ),
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.OPEN_FOOD_FACTS,
        priority=4,
        is_external=True,
        is_available=False,
        expected_confidence=Decimal("0.75"),
        notes=(
            "Open Food Facts open database (openfoodfacts.org). "
            "Planned — not yet implemented. "
            "Matching by barcode or product name; community-contributed data."
        ),
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.OQALI,
        priority=5,
        is_external=True,
        is_available=False,
        expected_confidence=Decimal("0.80"),
        notes=(
            "OQALI French food product surveillance database. "
            "Planned — not yet implemented. "
            "Product-level label data; French market coverage."
        ),
    ),
    EnrichmentSourceInfo(
        source=NutritionEnrichmentSource.NEVO,
        priority=6,
        is_external=True,
        is_available=False,
        expected_confidence=Decimal("0.80"),
        notes=(
            "RIVM NEVO Dutch food composition database. "
            "Planned — not yet implemented. "
            "Category-level matching; Dutch and European products."
        ),
    ),
)

#: Subset ordered by priority, available implementations only.
AVAILABLE_SOURCES: tuple[EnrichmentSourceInfo, ...] = tuple(
    s for s in ENRICHMENT_SOURCE_REGISTRY if s.is_available
)

#: External sources registered but not yet implemented.
PLANNED_EXTERNAL_SOURCES: tuple[EnrichmentSourceInfo, ...] = tuple(
    s for s in ENRICHMENT_SOURCE_REGISTRY if s.is_external and not s.is_available
)

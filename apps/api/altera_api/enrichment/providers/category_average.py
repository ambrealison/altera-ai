"""Category-average protein enrichment provider (Phase 23B).

Loads a static YAML table of protein_pct averages keyed by PT group.
Used as a statistical fallback when a product has no retailer-provided
protein data and no manual override is available.

Not suitable as a primary source. Always prefer:
  1. retailer_provided  (in pt_fields.protein_pct)
  2. manual_altera
  3. category_average  ← this module
  4. external databases (planned, Phase 23C+)

No external calls are made. The YAML table ships with the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import yaml

from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.protein_tracker import ProteinTrackerGroup

_DATA_FILE = Path(__file__).parent.parent / "data" / "category_protein_averages.yaml"

#: Sentinel so we can distinguish "not loaded yet" from an empty table.
_UNLOADED: dict[tuple[str, str], _Entry] = {}  # type: ignore[type-arg]


@dataclass(frozen=True)
class _Entry:
    pt_group: str
    nutrient: str
    value: Decimal
    unit: str
    confidence: Decimal
    rationale: str


# Module-level cache; loaded once at first use (no I/O in tests unless needed).
_TABLE: dict[tuple[str, str], _Entry] | None = None


def _load_table() -> dict[tuple[str, str], _Entry]:
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    with _DATA_FILE.open(encoding="utf-8") as fh:
        rows = yaml.safe_load(fh)
    _TABLE = {
        (row["pt_group"], row["nutrient"]): _Entry(
            pt_group=row["pt_group"],
            nutrient=row["nutrient"],
            value=Decimal(str(row["value"])),
            unit=row["unit"],
            confidence=Decimal(str(row["confidence"])),
            rationale=row["rationale"].strip(),
        )
        for row in rows
    }
    return _TABLE


def lookup_category_average(
    pt_group: ProteinTrackerGroup,
    nutrient: str,
) -> _Entry | None:
    """Return the static average entry for ``(pt_group, nutrient)``, or ``None``."""
    return _load_table().get((pt_group.value, nutrient))


class CategoryAverageProvider:
    """Provides protein enrichment values from a static category-average table.

    This provider satisfies the ``NutritionEnrichmentProvider`` protocol for
    the ``enrich_by_group`` use-case. It does not implement the base
    ``enrich(product_id, nutrient)`` signature because it requires the
    product's PT group classification, which is not known from the ID alone.

    Usage (from a route handler):
        provider = CategoryAverageProvider()
        record = provider.enrich_by_group(product_id, pt_group, "protein_pct", now=now)
    """

    source = NutritionEnrichmentSource.CATEGORY_AVERAGE
    is_available = True

    def enrich_by_group(
        self,
        product_id: UUID,
        pt_group: ProteinTrackerGroup,
        nutrient: str,
        *,
        now: datetime,
        created_by: UUID | None = None,
    ) -> NutritionEnrichmentRecord | None:
        """Return an ENRICHED record for the product's PT group, or ``None``.

        Returns ``None`` when the group has no entry in the table
        (i.e. ``out_of_scope`` and ``unknown`` groups are not enriched).
        Never raises.
        """
        entry = lookup_category_average(pt_group, nutrient)
        if entry is None:
            return None
        return NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient=nutrient,
            original_value=None,
            enriched_value=entry.value,
            unit=entry.unit,
            source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
            confidence=entry.confidence,
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale=entry.rationale,
            created_at=now,
            created_by=created_by,
        )

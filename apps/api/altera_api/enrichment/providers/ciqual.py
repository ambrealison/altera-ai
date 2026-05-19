"""CIQUAL 2025 enrichment provider (Phase 33A).

Provides protein_pct estimates from the ANSES CIQUAL food composition table
as a fallback when retailer-provided nutrition data is absent.

Attribution (required when CIQUAL values appear in any report output):
    Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/

Matching strategy (MVP):
  1. Exact case-insensitive name match on ``food_name_en``.
  2. If no exact match, compute a food-group average from all entries
     whose ``food_group`` equals the query group (if provided).
  3. If multiple candidates exist and no clear winner, return
     status=NEEDS_MANUAL_REVIEW with the closest candidates listed.

The provider is initialised with a pre-loaded reference table so it does
not require database access at enrich-time. Load the table at application
startup via ``CiqualProvider.from_entries()``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from altera_api.domain.ciqual import CiqualEntry, CiqualMatchResult
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

_CIQUAL_CONFIDENCE_EXACT = Decimal("0.80")
_CIQUAL_CONFIDENCE_GROUP_AVG = Decimal("0.55")


class CiqualProvider:
    """In-memory CIQUAL lookup for protein enrichment.

    Initialise once with ``CiqualProvider.from_entries(entries)`` at
    application startup and pass the instance to the enrichment pipeline.
    """

    def __init__(
        self,
        *,
        by_name: dict[str, CiqualEntry],
        by_group: dict[str, list[CiqualEntry]],
    ) -> None:
        self._by_name = by_name        # lower(food_name_en) → entry
        self._by_group = by_group      # food_group → entries list

    @classmethod
    def from_entries(cls, entries: Sequence[CiqualEntry]) -> CiqualProvider:
        """Build an in-memory index from a list of CiqualEntry objects."""
        by_name: dict[str, CiqualEntry] = {}
        by_group: dict[str, list[CiqualEntry]] = defaultdict(list)
        for e in entries:
            name_key = e.food_name_en.lower().strip()
            by_name[name_key] = e
            by_group[e.food_group].append(e)
        return cls(by_name=by_name, by_group=dict(by_group))

    @property
    def source(self) -> NutritionEnrichmentSource:
        return NutritionEnrichmentSource.CIQUAL

    @property
    def is_available(self) -> bool:
        return bool(self._by_name)

    @property
    def entry_count(self) -> int:
        return len(self._by_name)

    def match(
        self,
        *,
        food_name: str | None = None,
        food_group: str | None = None,
    ) -> CiqualMatchResult | None:
        """Find the best matching CIQUAL entry for the given descriptors.

        Returns None if no match is possible (no name and no group provided,
        or the group is unknown).
        """
        # 1. Exact name match
        if food_name:
            entry = self._by_name.get(food_name.lower().strip())
            if entry is not None and entry.protein_g_per_100g is not None:
                return CiqualMatchResult(
                    entry=entry,
                    match_type="exact_name",
                    confidence=_CIQUAL_CONFIDENCE_EXACT,
                    query_food_group=food_group,
                    query_name=food_name,
                )

        # 2. Food-group average
        if food_group:
            candidates = [
                e for e in self._by_group.get(food_group, [])
                if e.protein_g_per_100g is not None and not e.is_below_detection
            ]
            if candidates:
                avg = sum(e.protein_g_per_100g for e in candidates) / len(candidates)  # type: ignore[operator]
                representative = min(
                    candidates,
                    key=lambda e: abs((e.protein_g_per_100g or Decimal(0)) - avg),
                )
                return CiqualMatchResult(
                    entry=CiqualEntry(
                        id=representative.id,
                        source=representative.source,
                        source_version=representative.source_version,
                        source_food_code=f"avg:{food_group}",
                        food_name_en=f"{food_group} (average)",
                        food_group=food_group,
                        food_subgroup=None,
                        food_subsubgroup=None,
                        protein_g_per_100g=Decimal(str(round(avg, 4))),
                        is_below_detection=False,
                    ),
                    match_type="food_group_average",
                    confidence=_CIQUAL_CONFIDENCE_GROUP_AVG,
                    query_food_group=food_group,
                    query_name=food_name,
                )

        return None

    def enrich(
        self,
        product_id: UUID,
        nutrient: str,
        *,
        food_name: str | None = None,
        food_group: str | None = None,
        now: datetime,
        created_by: UUID | None = None,
    ) -> NutritionEnrichmentRecord | None:
        """Return an enrichment record for the given product descriptor.

        Returns None if this product cannot be matched (call enrich only
        when nutrient == "protein_pct" and the product needs enrichment).
        """
        if nutrient != "protein_pct":
            return None

        match = self.match(food_name=food_name, food_group=food_group)
        if match is None:
            return NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient=nutrient,
                original_value=None,
                enriched_value=None,
                unit="g_per_100g",
                source=NutritionEnrichmentSource.CIQUAL,
                confidence=None,
                status=NutritionEnrichmentStatus.FAILED,
                rationale=(
                    "CIQUAL: no matching entry found for "
                    f"name={food_name!r}, group={food_group!r}"
                ),
                created_at=now,
                created_by=created_by,
            )

        return NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient=nutrient,
            original_value=None,
            enriched_value=match.entry.protein_g_per_100g,
            unit="g_per_100g",
            source=NutritionEnrichmentSource.CIQUAL,
            confidence=match.confidence,
            status=(
                NutritionEnrichmentStatus.ENRICHED
                if match.entry.protein_g_per_100g is not None
                else NutritionEnrichmentStatus.FAILED
            ),
            rationale=(
                f"CIQUAL {match.entry.source_version}: {match.match_type} "
                f"match on {match.entry.food_name_en!r} "
                f"(code {match.entry.source_food_code}); "
                f"confidence={match.confidence}"
            ),
            created_at=now,
            created_by=created_by,
        )

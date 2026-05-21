"""NEVO 2025 enrichment provider (Phase 33E).

Provides protein_pct + plant_protein / animal_protein estimates from the
NEVO 2025 v9.0 reference database as a fallback when retailer-provided
nutrition data is absent.

NEVO is preferred over CIQUAL for Protein Tracker because NEVO publishes
PROTPL (plant protein) and PROTAN (animal protein) per 100 g, which
gives a plant/animal split that CIQUAL does not provide.

Attribution (required when NEVO values appear in any report output):
    RIVM. 2025. NEVO-Online 2025 v9.0. https://nevo-online.rivm.nl/

Matching strategy:
  1. Exact case-insensitive name match on ``food_name_en``.
  2. If no English match, exact case-insensitive match on ``food_name_nl``.
  3. Phase 34L — fuzzy token-overlap match. The product name is run
     through ``clean_product_name`` (strips packaging tokens) and
     ``_expand_aliases`` (FR → EN/NL food terms) and then scored
     against every NEVO entry. The top candidate is returned when its
     token-overlap score is >= the fuzzy threshold.
  4. If still no match and a ``food_group`` was provided, food-group
     average.
  5. Otherwise return None.

The provider is initialised with a pre-loaded reference table so it does
not require database access at enrich-time. Load the table at application
startup via ``NevoProvider.from_entries()``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.nevo import NevoEntry, NevoMatchResult

_NEVO_CONFIDENCE_EXACT = Decimal("0.85")
_NEVO_CONFIDENCE_FUZZY = Decimal("0.75")
_NEVO_CONFIDENCE_GROUP_AVG = Decimal("0.60")
# Minimum number of overlapping (post-alias) tokens required for the
# fuzzy fallback to consider a candidate. Two tokens is permissive
# enough to catch "Blanc de Poulet" → "Chicken breast" (poulet→chicken,
# blanc→breast) without firing on a single coincidental token like
# "nature" or "bio".
_FUZZY_MIN_SCORE = 2


def _has_split(entry: NevoEntry) -> bool:
    return (
        entry.plant_protein_g_per_100g is not None
        and entry.animal_protein_g_per_100g is not None
    )


class NevoProvider:
    """In-memory NEVO lookup for protein enrichment with plant/animal split."""

    def __init__(
        self,
        *,
        by_name_en: dict[str, NevoEntry],
        by_name_nl: dict[str, NevoEntry],
        by_group: dict[str, list[NevoEntry]],
        all_entries: list[NevoEntry] | None = None,
    ) -> None:
        self._by_name_en = by_name_en
        self._by_name_nl = by_name_nl
        self._by_group = by_group
        # Phase 34L — keep the full list around so the fuzzy fallback
        # can iterate every entry once. The flat list is small
        # (~1000 entries) so this O(N) scan per product is negligible.
        self._all_entries: list[NevoEntry] = all_entries or []

    @classmethod
    def from_entries(cls, entries: Sequence[NevoEntry]) -> NevoProvider:
        """Build an in-memory index from a list of NevoEntry objects."""
        by_name_en: dict[str, NevoEntry] = {}
        by_name_nl: dict[str, NevoEntry] = {}
        by_group: dict[str, list[NevoEntry]] = defaultdict(list)
        for e in entries:
            if e.food_name_en:
                by_name_en[e.food_name_en.lower().strip()] = e
            if e.food_name_nl:
                by_name_nl[e.food_name_nl.lower().strip()] = e
            if e.food_group:
                by_group[e.food_group].append(e)
        return cls(
            by_name_en=by_name_en,
            by_name_nl=by_name_nl,
            by_group=dict(by_group),
            all_entries=[
                e for e in entries if e.protein_g_per_100g is not None
            ],
        )

    @property
    def source(self) -> NutritionEnrichmentSource:
        return NutritionEnrichmentSource.NEVO

    @property
    def is_available(self) -> bool:
        return bool(self._by_name_en) or bool(self._by_name_nl)

    @property
    def entry_count(self) -> int:
        # Count distinct entries — same entry may be indexed by both NL and EN.
        return len({id(e) for e in self._by_name_en.values()} | {id(e) for e in self._by_name_nl.values()})

    def match(
        self,
        *,
        food_name: str | None = None,
        food_group: str | None = None,
    ) -> NevoMatchResult | None:
        """Find the best matching NEVO entry for the given descriptors."""
        if food_name:
            key = food_name.lower().strip()
            entry = self._by_name_en.get(key)
            if entry is not None and entry.protein_g_per_100g is not None:
                return NevoMatchResult(
                    entry=entry,
                    match_type="exact_name_en",
                    confidence=_NEVO_CONFIDENCE_EXACT,
                    query_food_group=food_group,
                    query_name=food_name,
                    split_available=_has_split(entry),
                )
            entry = self._by_name_nl.get(key)
            if entry is not None and entry.protein_g_per_100g is not None:
                return NevoMatchResult(
                    entry=entry,
                    match_type="exact_name_nl",
                    confidence=_NEVO_CONFIDENCE_EXACT,
                    query_food_group=food_group,
                    query_name=food_name,
                    split_available=_has_split(entry),
                )

            # Phase 34L — fuzzy token-overlap fallback. Uses the same
            # tokenizer + alias dictionary that the AI-shortlist path
            # uses, so behaviour is consistent whether or not the AI
            # nutrition matching is enabled.
            fuzzy = self._fuzzy_match(food_name)
            if fuzzy is not None:
                return NevoMatchResult(
                    entry=fuzzy,
                    match_type="fuzzy_token_overlap",
                    confidence=_NEVO_CONFIDENCE_FUZZY,
                    query_food_group=food_group,
                    query_name=food_name,
                    split_available=_has_split(fuzzy),
                )

        if food_group:
            candidates = [
                e
                for e in self._by_group.get(food_group, [])
                if e.protein_g_per_100g is not None
            ]
            if candidates:
                total_avg = sum(
                    e.protein_g_per_100g for e in candidates  # type: ignore[misc]
                ) / len(candidates)
                # Plant/animal split average across entries that have both.
                split_candidates = [e for e in candidates if _has_split(e)]
                if split_candidates:
                    plant_avg: Decimal | None = sum(
                        e.plant_protein_g_per_100g for e in split_candidates  # type: ignore[misc]
                    ) / len(split_candidates)
                    animal_avg: Decimal | None = sum(
                        e.animal_protein_g_per_100g for e in split_candidates  # type: ignore[misc]
                    ) / len(split_candidates)
                else:
                    plant_avg = None
                    animal_avg = None
                representative = candidates[0]
                avg_entry = NevoEntry(
                    id=representative.id,
                    source=representative.source,
                    source_version=representative.source_version,
                    nevo_code=f"avg:{food_group}",
                    food_name_nl=f"{food_group} (gemiddeld)",
                    food_name_en=f"{food_group} (average)",
                    food_group=food_group,
                    quantity_basis="per 100g",
                    protein_g_per_100g=Decimal(str(round(total_avg, 4))),
                    plant_protein_g_per_100g=(
                        Decimal(str(round(plant_avg, 4))) if plant_avg is not None else None
                    ),
                    animal_protein_g_per_100g=(
                        Decimal(str(round(animal_avg, 4))) if animal_avg is not None else None
                    ),
                )
                return NevoMatchResult(
                    entry=avg_entry,
                    match_type="food_group_average",
                    confidence=_NEVO_CONFIDENCE_GROUP_AVG,
                    query_food_group=food_group,
                    query_name=food_name,
                    split_available=_has_split(avg_entry),
                )

        return None

    def _fuzzy_match(self, food_name: str) -> NevoEntry | None:
        """Pick the best fuzzy candidate for ``food_name`` via token
        overlap on the cleaned + alias-expanded query tokens against
        each NEVO entry's English/Dutch name. Returns the top
        candidate when its score meets ``_FUZZY_MIN_SCORE``.

        The same helpers that build the AI-shortlist (Phase 34D/K) are
        reused here so the deterministic fallback uses the SAME
        token vocabulary the AI sees — no drift between the two paths.
        """
        # Lazy import to avoid a circular dependency with the ai/
        # module (which itself imports from domain/).
        from altera_api.ai.nutrition_candidates import (
            _expand_aliases,
            _tokenize,
            clean_product_name,
        )

        cleaned = clean_product_name(food_name)
        query_tokens = _tokenize(cleaned) | _tokenize(food_name)
        if not query_tokens:
            return None
        query_expanded = _expand_aliases(query_tokens, cleaned)
        if not query_expanded:
            return None

        best_entry: NevoEntry | None = None
        best_score = 0
        for entry in self._all_entries:
            name = entry.food_name_en or entry.food_name_nl or ""
            if not name:
                continue
            cand_tokens = _tokenize(name)
            if not cand_tokens:
                continue
            score = len(query_expanded & cand_tokens)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_score < _FUZZY_MIN_SCORE:
            return None
        return best_entry

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
        """Return an enrichment record for the given product descriptor."""
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
                source=NutritionEnrichmentSource.NEVO,
                confidence=None,
                status=NutritionEnrichmentStatus.FAILED,
                rationale=(
                    "NEVO: no matching entry found for "
                    f"name={food_name!r}, group={food_group!r}"
                ),
                created_at=now,
                created_by=created_by,
            )

        rationale_extra = (
            "with plant/animal split"
            if match.split_available
            else "total only (no plant/animal split)"
        )
        return NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient=nutrient,
            original_value=None,
            enriched_value=match.entry.protein_g_per_100g,
            unit="g_per_100g",
            source=NutritionEnrichmentSource.NEVO,
            confidence=match.confidence,
            status=(
                NutritionEnrichmentStatus.ENRICHED
                if match.entry.protein_g_per_100g is not None
                else NutritionEnrichmentStatus.FAILED
            ),
            rationale=(
                f"NEVO {match.entry.source_version}: {match.match_type} "
                f"match on {match.entry.food_name_en!r} "
                f"(code {match.entry.nevo_code}); "
                f"{rationale_extra}; confidence={match.confidence}"
            ),
            created_at=now,
            created_by=created_by,
        )

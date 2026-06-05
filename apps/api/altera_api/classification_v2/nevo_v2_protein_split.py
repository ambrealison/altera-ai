"""Phase Quality-V2-Z — derive a plant/animal protein split from V2 totals.

V2 enrichment wrote only TOTAL protein (``nutrient='protein_pct'``), so the
calculation has no per-product split and the UI plant/animal columns stay blank
(it falls back to the classification assumption at calc time, but no stored
split record exists).

The Protein Tracker already classifies each product into a group. For the
headline groups the split is unambiguous:

  * ``animal_core``            → animal = total, plant = 0
  * ``plant_based_core``       → plant  = total, animal = 0
  * ``plant_based_non_core``   → plant  = total, animal = 0
  * ``composite_products`` / ``unknown`` / ``out_of_scope`` → NO auto split
    (route to review — a composite product genuinely mixes plant and animal).

This module is pure policy (no I/O, no DB). The split is surfaced to the
calculation as sibling ENRICHED enrichment records — ``nutrient='plant_protein_pct'``
and ``nutrient='animal_protein_pct'`` with the SAME ``source=nevo`` as the V2
total record (see ``enrichment/selection.py``), so no schema change is needed.

Manual overrides always win: if a product already carries any manual enrichment,
we never propose an automatic split for it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from altera_api.domain.enrichment import (
    SOURCE_VERSION_V2_EMBEDDINGS,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

#: provenance tag for V2-derived split records (additive; no migration).
SPLIT_SOURCE_VERSION = "v2_embeddings_split"

SPLIT_ACTIONS = (
    "would_split", "needs_review", "skip_missing_class", "skip_manual_override",
)

_ANIMAL_GROUP = "animal_core"
_PLANT_GROUPS = frozenset({"plant_based_core", "plant_based_non_core"})
_REVIEW_GROUPS = frozenset({"composite_products", "unknown", "out_of_scope"})


def is_v2_total_protein(record: Any) -> bool:
    """True for a V2 total-protein enrichment record eligible for splitting."""
    return (
        getattr(record, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS
        and getattr(record, "source", None) is NutritionEnrichmentSource.NEVO
        and getattr(record, "nutrient", None) == "protein_pct"
        and getattr(record, "unit", None) == "g_per_100g"
        and getattr(record, "status", None)
        is NutritionEnrichmentStatus.ENRICHED
        and getattr(record, "enriched_value", None) is not None
    )


def is_manual(record: Any) -> bool:
    return (
        getattr(record, "match_method", None) == "manual"
        or getattr(record, "source", None)
        is NutritionEnrichmentSource.MANUAL_ALTERA
    )


def has_existing_split(records: list[Any]) -> bool:
    """True if the product already has a plant/animal split enrichment record."""
    return any(
        getattr(r, "nutrient", None) in ("plant_protein_pct", "animal_protein_pct")
        and getattr(r, "status", None) is NutritionEnrichmentStatus.ENRICHED
        and getattr(r, "enriched_value", None) is not None
        for r in records
    )


def split_proposal(
    *, pt_group: Any, total_protein: Decimal | None,
    has_manual_override: bool, has_classification: bool,
) -> dict[str, Any]:
    """Return ``{action, plant, animal, reason}`` for one product."""
    if has_manual_override:
        return {"action": "skip_manual_override", "plant": None, "animal": None,
                "reason": "product has a manual enrichment override; manual "
                          "values always win"}
    if not has_classification or pt_group is None or total_protein is None:
        return {"action": "skip_missing_class", "plant": None, "animal": None,
                "reason": "no PT classification (or no total) for product"}

    group = str(getattr(pt_group, "value", pt_group))
    if group == _ANIMAL_GROUP:
        return {"action": "would_split", "plant": Decimal("0"),
                "animal": total_protein,
                "reason": "animal_core → all protein is animal"}
    if group in _PLANT_GROUPS:
        return {"action": "would_split", "plant": total_protein,
                "animal": Decimal("0"),
                "reason": f"{group} → all protein is plant"}
    if group in _REVIEW_GROUPS:
        return {"action": "needs_review", "plant": None, "animal": None,
                "reason": f"{group} mixes plant/animal — no automatic split"}
    return {"action": "needs_review", "plant": None, "animal": None,
            "reason": f"unrecognized PT group {group!r} — no automatic split"}

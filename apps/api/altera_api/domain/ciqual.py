"""CIQUAL food composition reference model (Phase 33A).

CIQUAL (Centre d'Information sur la Qualité des Aliments) is the French
national food composition table published by ANSES.

Attribution (required when CIQUAL values are used in any output):
    Anses. 2025. Ciqual French food composition table.
    https://ciqual.anses.fr/

Usage rules:
  - CIQUAL values are reference averages for food categories, not label
    data for specific retail SKUs.
  - Use only as a fallback when retailer-provided nutrition data is absent.
  - Disclose usage in any published report (source, version, count).
  - Never overwrite retailer-provided protein_pct.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import DomainBase

CIQUAL_SOURCE = "ciqual"
CIQUAL_CITATION = (
    "Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/"
)


class CiqualEntry(DomainBase):
    """One food entry from the CIQUAL 2025 reference database."""

    id: UUID
    source: str = CIQUAL_SOURCE
    source_version: str                  # e.g. "2025"
    source_food_code: str                # alim_code from CIQUAL
    food_name_en: str                    # alim_nom_eng
    food_group: str                      # alim_grp_nom_eng
    food_subgroup: str | None            # alim_ssgrp_nom_eng
    food_subsubgroup: str | None         # alim_ssssgrp_nom_eng
    protein_g_per_100g: Decimal | None   # None when not analysed
    is_below_detection: bool = False     # True when source value was "< N"


class CiqualMatchResult(DomainBase):
    """Outcome of matching a product descriptor to a CIQUAL entry."""

    entry: CiqualEntry
    match_type: str        # "exact_name" | "food_group_average" | "ambiguous"
    confidence: Decimal    # 0–1
    query_food_group: str | None
    query_name: str | None

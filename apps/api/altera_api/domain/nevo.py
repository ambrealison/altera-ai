"""NEVO 2025 food composition reference model (Phase 33E).

NEVO (Nederlands Voedingsstoffenbestand) is the Dutch food composition
database published by RIVM. Unlike CIQUAL it provides PROT (total
protein), PROTPL (plant protein) and PROTAN (animal protein) per 100 g,
which makes it the higher-priority fallback for Protein Tracker
plant/animal split enrichment.

Attribution (required whenever NEVO values appear in any output):
    RIVM. 2025. NEVO-Online 2025 v9.0. Rijksinstituut voor Volksgezondheid
    en Milieu, Bilthoven. https://nevo-online.rivm.nl/

Usage rules:
  - NEVO values are reference averages, not retail-SKU labels.
  - Use only as a fallback when retailer-provided nutrition data is absent.
  - When PROTPL/PROTAN are blank for an entry, only the total is returned
    and the split is reported as unavailable.
  - Never overwrite retailer-provided protein_pct.
  - Disclose source in any published report (source, version, count).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import DomainBase

NEVO_SOURCE = "nevo"
NEVO_CITATION = (
    "RIVM. 2025. NEVO-Online 2025 v9.0. Rijksinstituut voor Volksgezondheid "
    "en Milieu, Bilthoven. https://nevo-online.rivm.nl/"
)


class NevoEntry(DomainBase):
    """One food entry from the NEVO 2025 v9.0 reference database."""

    id: UUID
    source: str = NEVO_SOURCE
    source_version: str                              # e.g. "2025_v9.0"
    nevo_code: str                                   # NEVO-code (string for safe leading zeros)
    food_name_nl: str                                # Voedingsmiddelnaam/Dutch food name
    food_name_en: str                                # Engelse naam/Food name
    food_group: str                                  # Food group (English column)
    quantity_basis: str                              # Hoeveelheid/Quantity (e.g. "per 100g")
    protein_g_per_100g: Decimal | None               # PROT (g) — total
    plant_protein_g_per_100g: Decimal | None         # PROTPL (g)
    animal_protein_g_per_100g: Decimal | None        # PROTAN (g)


class NevoMatchResult(DomainBase):
    """Outcome of matching a product descriptor to a NEVO entry."""

    entry: NevoEntry
    match_type: str          # "exact_name_en" | "exact_name_nl" | "food_group_average"
    confidence: Decimal      # 0–1
    query_food_group: str | None
    query_name: str | None
    split_available: bool    # True iff both PROTPL and PROTAN are present on the entry

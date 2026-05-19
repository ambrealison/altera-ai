"""Column mapping: synonym registry, inference, and application.

Phase 33B adds a flexible mapping layer between the raw CSV headers
uploaded by retailers and the canonical field names expected by the
ingestion pipeline. The flow is:

  1. Browser sends first CSV line to ``POST /api/v1/uploads/preview-mapping``
  2. Server normalises headers, infers canonical mappings from the
     synonym registry, and returns a ``MappingPreviewResult``
  3. User reviews / corrects the suggested mapping in the UI
  4. Confirmed mapping is passed as ``column_mapping`` to both the
     direct-upload and storage-ingest endpoints
  5. ``apply_column_mapping()`` renames row keys inside the pipeline
     before ``filter_commercial_columns`` and ``parse_row``

Security note: ``filter_commercial_columns`` runs *after* mapping, so
commercial/sensitive columns (revenue, margin, supplier pricing) are
still stripped even when they have been remapped to a recognisable name.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from altera_api.ingestion.headers import normalise_header

# ---------------------------------------------------------------------------
# Canonical field sets
# ---------------------------------------------------------------------------

#: All canonical fields understood by the ingestion pipeline.
#: "ignore" is a special sentinel meaning "drop this column".
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        # Common
        "external_product_id",
        "product_name",
        "weight_per_item_kg",
        "brand",
        "retailer_category",
        "retailer_subcategory",
        "ingredients_text",
        "is_own_brand",
        "ean",
        "labels",
        "country",
        "language",
        "reporting_period",
        # Protein Tracker specific
        "items_purchased",
        "protein_pct",
        # WWF specific
        "items_sold",
        "retail_channel",
    }
)

# ---------------------------------------------------------------------------
# Synonym registry
# ---------------------------------------------------------------------------
# Keys are canonical field names.
# Values are lists of *already-normalised* synonyms (run through normalise_header).
# The canonical name itself is also implicitly matched (handled in infer_mapping).

_RAW_SYNONYMS: dict[str, list[str]] = {
    "external_product_id": [
        "sku",
        "sku_id",
        "item_code",
        "product_code",
        "product_id",
        "article_id",
        "article_code",
        "code_article",
        "reference",
        "ref",
        "id_produit",
        "code_produit",
        "product_reference",
        "retailer_product_id",
    ],
    "product_name": [
        "name",
        "item_name",
        "description",
        "product_description",
        "libelle",
        "libelle_produit",
        "designation",
        "nom_produit",
        "nom",
        "article_name",
        "article_description",
    ],
    "weight_per_item_kg": [
        "weight_kg",
        "weight",
        "poids_kg",
        "poids",
        "unit_weight_kg",
        "unit_weight",
        "grammes",
        "gram_weight",
        "kg_per_unit",
        "weight_per_unit",
        "weight_per_unit_kg",
        "net_weight_kg",
        "net_weight",
    ],
    "brand": [
        "marque",
        "brand_name",
        "manufacturer",
        "fabricant",
        "supplier_brand",
    ],
    "retailer_category": [
        "category",
        "categorie",
        "cat",
        "product_category",
        "main_category",
        "department",
        "rayon",
        "famille",
        "level1_category",
        "l1_category",
        "category_level1",
        "cat1",
    ],
    "retailer_subcategory": [
        "subcategory",
        "sous_categorie",
        "sub_category",
        "subcat",
        "product_subcategory",
        "level2_category",
        "l2_category",
        "category_level2",
        "cat2",
        "sous_famille",
        "segment",
    ],
    "ingredients_text": [
        "ingredients",
        "ingredient_list",
        "ingredient_text",
        "composition",
        "liste_ingredients",
        "composants",
        "raw_ingredients",
    ],
    "is_own_brand": [
        "own_brand",
        "private_label",
        "marque_propre",
        "mdd",
        "mdd_flag",
        "own_label",
        "is_private_label",
        "prive_label",
        "store_brand",
    ],
    "ean": [
        "gtin",
        "barcode",
        "ean13",
        "ean_code",
        "upc",
        "gtin13",
        "code_barre",
        "codebarre",
        "bar_code",
    ],
    "labels": [
        "certifications",
        "claims",
        "product_claims",
        "product_labels",
        "label",
        "ecolabel",
        "eco_labels",
        "certifs",
    ],
    "country": [
        "pays",
        "country_code",
        "country_of_sale",
        "market",
        "pays_de_vente",
    ],
    "language": [
        "lang",
        "locale",
        "langue",
        "product_language",
    ],
    "reporting_period": [
        "period",
        "periode",
        "quarter",
        "trimestre",
        "year",
        "annee",
        "fiscal_period",
        "report_period",
        "perioden",
    ],
    "items_purchased": [
        "quantity",
        "qty",
        "units_purchased",
        "volume",
        "nb_items",
        "nb_units",
        "quantite",
        "quantite_achetee",
        "purchases",
        "units_bought",
        "items",
        "nb_articles",
        "articles_achetes",
    ],
    "protein_pct": [
        "protein",
        "protein_percent",
        "proteins",
        "proteines",
        "pct_protein",
        "protein_percentage",
        "proteines_pct",
        "proteines_percent",
        "protein_g_per_100g",
        "proteines_g_100g",
    ],
    "items_sold": [
        "units_sold",
        "sales_units",
        "sales_volume",
        "qty_sold",
        "quantity_sold",
        "nb_ventes",
        "nb_unites_vendues",
        "ventes",
        "sold_units",
    ],
    "retail_channel": [
        "channel",
        "product_type",
        "format",
        "type",
        "rayon_type",
        "product_channel",
        "temperature_zone",
        "conservation",
    ],
}

# Build lookup: normalised_synonym → canonical_field
_SYNONYM_LOOKUP: dict[str, str] = {}
for _canonical, _synonyms in _RAW_SYNONYMS.items():
    for _syn in _synonyms:
        _norm = normalise_header(_syn)
        _SYNONYM_LOOKUP[_norm] = _canonical
    # The canonical name itself is also a synonym
    _SYNONYM_LOOKUP[normalise_header(_canonical)] = _canonical

# ---------------------------------------------------------------------------
# Pydantic models (API contract)
# ---------------------------------------------------------------------------


class ColumnMappingEntry(BaseModel):
    """One suggested header→canonical mapping."""

    raw_header: str
    normalised_header: str
    canonical_field: str | None
    confidence: Literal["exact", "synonym", "none"]
    enrichment_needed: bool = False


class MappingPreviewRequest(BaseModel):
    """Request body for ``POST /api/v1/uploads/preview-mapping``."""

    headers: list[str]


class MappingPreviewResult(BaseModel):
    """Response for the preview-mapping endpoint."""

    entries: list[ColumnMappingEntry]
    missing_required_pt: list[str]
    missing_required_wwf: list[str]
    duplicate_normalised: list[str]


# ---------------------------------------------------------------------------
# Required fields per methodology
# ---------------------------------------------------------------------------

_REQUIRED_PT: frozenset[str] = frozenset(
    {"external_product_id", "product_name", "weight_per_item_kg", "items_purchased"}
)
_REQUIRED_WWF: frozenset[str] = frozenset(
    {
        "external_product_id",
        "product_name",
        "weight_per_item_kg",
        "items_sold",
        "is_own_brand",
        "retail_channel",
    }
)

# Fields that are optionally enriched from CIQUAL if absent
_ENRICHABLE: frozenset[str] = frozenset({"protein_pct"})

# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def infer_mapping(headers: list[str]) -> MappingPreviewResult:
    """Infer canonical field mappings for a list of raw CSV headers.

    Parameters
    ----------
    headers:
        Raw header strings exactly as they appear in the CSV (first row).

    Returns
    -------
    MappingPreviewResult
        Suggested mapping for each header, plus diagnostic lists of
        missing required fields and duplicate normalised headers.
    """
    entries: list[ColumnMappingEntry] = []
    seen_normalised: dict[str, int] = {}  # normalised → count
    mapped_canonical: set[str] = set()

    for raw in headers:
        norm = normalise_header(raw)
        seen_normalised[norm] = seen_normalised.get(norm, 0) + 1

        if norm in _SYNONYM_LOOKUP:
            canonical = _SYNONYM_LOOKUP[norm]
            confidence: Literal["exact", "synonym", "none"] = (
                "exact" if normalise_header(canonical) == norm else "synonym"
            )
        else:
            canonical = None
            confidence = "none"

        if canonical is not None:
            mapped_canonical.add(canonical)

        enrichment_needed = canonical in _ENRICHABLE if canonical else False

        entries.append(
            ColumnMappingEntry(
                raw_header=raw,
                normalised_header=norm,
                canonical_field=canonical,
                confidence=confidence,
                enrichment_needed=enrichment_needed,
            )
        )

    duplicate_normalised = [n for n, c in seen_normalised.items() if c > 1]

    missing_required_pt = sorted(_REQUIRED_PT - mapped_canonical)
    missing_required_wwf = sorted(_REQUIRED_WWF - mapped_canonical)

    return MappingPreviewResult(
        entries=entries,
        missing_required_pt=missing_required_pt,
        missing_required_wwf=missing_required_wwf,
        duplicate_normalised=sorted(duplicate_normalised),
    )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_column_mapping(
    row: dict[str, object],
    column_mapping: dict[str, str],
) -> dict[str, object]:
    """Rename keys in a row according to a confirmed column mapping.

    Parameters
    ----------
    row:
        A dict with *normalised* header keys (as produced by ``csv_reader``).
    column_mapping:
        Maps ``normalised_original_header → canonical_field | "ignore"``.
        Headers not present in the mapping are passed through unchanged.
        Headers mapped to ``"ignore"`` are dropped.

    Returns
    -------
    dict
        New row dict with canonical field names as keys.
    """
    if not column_mapping:
        return row

    result: dict[str, object] = {}
    for key, value in row.items():
        target = column_mapping.get(key)
        if target is None:
            result[key] = value
        elif target != "ignore":
            result[target] = value
        # "ignore" → skip the key entirely
    return result

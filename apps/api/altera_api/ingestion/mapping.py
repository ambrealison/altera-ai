"""Column mapping: synonym registry, inference, and application.

Phase 33B adds a flexible mapping layer between the raw CSV headers
uploaded by retailers and the canonical field names expected by the
ingestion pipeline. Phase 33B-hotfix makes required-field checking
methodology-aware and adds official Protein Tracker template synonyms.

The flow is:

  1. Browser sends first CSV line to ``POST /api/v1/uploads/preview-mapping``
     along with the project's enabled methodologies.
  2. Server normalises headers, infers canonical mappings from the
     synonym registry, and returns a ``MappingPreviewResult``.
  3. User reviews / corrects the suggested mapping in the UI.
  4. Confirmed mapping is passed as ``column_mapping`` to both the
     direct-upload and storage-ingest endpoints.
  5. ``apply_column_mapping()`` renames row keys inside the pipeline
     before ``filter_commercial_columns`` and ``parse_row``.

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
#: Includes unit-variant aliases (e.g. weight_per_item_g) that the
#: pipeline handles via unit conversion in normalise_weight_kg().
#: "ignore" is a special sentinel meaning "drop this column".
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        # Common
        "external_product_id",
        "product_name",
        "weight_per_item_kg",
        "weight_per_item_g",   # unit variant — pipeline converts g→kg
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
        "plant_protein_pct",
        "animal_protein_pct",
        # WWF specific
        "items_sold",
        "retail_channel",
    }
)

# ---------------------------------------------------------------------------
# Known output / diagnostic column patterns
# ---------------------------------------------------------------------------
# These are column names produced by Altera's own processing (classification
# results, AI outputs, script metadata). Retailers may include them if they
# re-export a processed file. We auto-suggest "ignore" for these.

_OUTPUT_COLUMN_NORMALISED: frozenset[str] = frozenset(
    normalise_header(h)
    for h in [
        "Deterministic Label",
        "AI Label",
        "Final Label",
        "Explanation",
        "Confidence",
        "Source",
        "Match",
        "Script version",
        "Script Version",
        "Model",
        "PT Group",
        "WWF Group",
        "Review status",
        "Review Status",
        "Processing status",
        "Classification",
        "Label",
        "Altera Label",
        "Predicted Label",
        "Predicted Category",
        "Output",
        "Result",
    ]
)

# ---------------------------------------------------------------------------
# Synonym registry
# ---------------------------------------------------------------------------
# Keys are canonical field names (or unit-variant aliases).
# Values are lists of *raw* synonym strings (run through normalise_header
# at module load time to build _SYNONYM_LOOKUP).

_RAW_SYNONYMS: dict[str, list[str]] = {
    "external_product_id": [
        "ID",
        "id",
        "sku",
        "sku_id",
        "item_code",
        "product_code",
        "product_id",
        "product_pk",
        "article_id",
        "article_code",
        "code_article",
        "reference",
        "ref",
        "id_produit",
        "code_produit",
        "product_reference",
        "retailer_product_id",
        "internal_id",
        "identifiant_produit_sku",   # Phase 33J — French template header
        "identifiant_produit",
        "identifiant",
        "item_id",
        "stock_code",
        "barref",
    ],
    "product_name": [
        "Name",
        "name",
        "item_name",
        "description",
        "product_description",
        "libelle",
        "libelle_produit",
        "designation",
        "nom_produit",
        "nom_du_produit",
        "nom",
        "article_name",
        "article_description",
        "product_title",
        "product_name_fr",          # Phase 33J — French-suffixed retailer exports
        "nom_du_produit_fr",
    ],
    # weight_per_item_kg: values already in kg
    "weight_per_item_kg": [
        "weight_kg",
        "poids_kg",
        "poids_unitaire_kg",
        "unit_weight_kg",
        "unit_weight",
        "kg_per_unit",
        "weight_per_unit",
        "weight_per_unit_kg",
        "net_weight_kg",
        "net_weight",
        "poids_net_kg",
    ],
    # weight_per_item_g: values in grams (pipeline converts g→kg)
    # Phase 33J: "poids_unitaire_produit" (Carrefour-style sparse export)
    # and "poids_unitaire_g" land here by default — common French
    # retailers ship grammes-per-unit under generic labels.
    "weight_per_item_g": [
        "Weight gram",
        "weight_gram",
        "weight_g",
        "poids_gramme",
        "poids_g",
        "poids_unitaire_g",
        "poids_unitaire_produit",
        "poids_unitaire",
        "weight_grams",
        "weight",           # ambiguous but common; grams assumed for PT template
        "grammes",
        "gram_weight",
        "poids_en_grammes",
        "net_weight_g",
        "net_weight_grams",
        "grammage",
        "weight_per_item",
        # Phase WWF-I-hotfix2 — alias for the "100-product dataset"
        # template the operator was uploading. Pack weight in grams
        # is the most common retailer export shape.
        "pack_weight_g",
        "pack_weight_grams",
        "package_weight_g",
        "unit_pack_weight_g",
        "poids_paquet",
        "poids_paquet_g",
    ],
    "brand": [
        "marque",
        "brand_name",
        "manufacturer",
        "fabricant",
        "supplier_brand",
        "fournisseur",
    ],
    "retailer_category": [
        "L1 category",
        "L1_category",
        "l1_category",
        "category",
        "categorie",
        "categorie_retailer",        # Phase 33J — French template header
        "cat",
        "product_category",
        "main_category",
        "department",
        "rayon",
        "famille",
        "level1_category",
        "l1",
        "category_level1",
        "cat1",
        "famille_niveau1",
        "cat_l1",
        "category_l1",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "raw_product_category",
        "raw_category",
    ],
    "retailer_subcategory": [
        "L2 category",
        "L2_category",
        "l2_category",
        "L3 category",      # L3 also maps here; no deeper canonical field
        "L3_category",
        "l3_category",
        "subcategory",
        "sous_categorie",
        "sous_categorie_retailer",   # Phase 33J — French template header
        "sub_category",
        "subcat",
        "product_subcategory",
        "level2_category",
        "l2",
        "category_level2",
        "cat2",
        "sous_famille",
        "segment",
        "level3_category",
        "cat3",
        "cat_l2",
        "category_l2",
        "sous_rayon",
    ],
    "ingredients_text": [
        "Ingredients",
        "ingredients",
        "ingredient_list",
        "ingredient_text",
        "composition",
        "liste_ingredients",
        "composants",
        "raw_ingredients",
        "ingredient_declaration",
        "ingredient",                # Phase 33J — singular FR form
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "ingredient_declaration_simulated",
        "ingredient_text_simulated",
    ],
    "is_own_brand": [
        "Store label",
        "store_label",
        "own_brand",
        "private_label",
        "marque_propre",
        "mdd",
        "mdd_flag",
        "own_label",
        "is_private_label",
        "prive_label",
        "store_brand",
        "label_propre",
        "pl",               # common abbreviation for private label
        "is_pl",
        "store_own_label",
        # Phase WWF-E — extra French aliases per brief.
        "marque_distributeur",
        "marque_du_distributeur",
        "marque_enseigne",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        # ``brand_type`` carries values like "Own brand" / "Branded";
        # the parser's enhanced _coerce_bool now understands those.
        "brand_type",
        "type_marque",
    ],
    "ean": [
        "EAN",
        "ean",
        "gtin",
        "barcode",
        "ean13",
        "ean_code",
        "upc",
        "gtin13",
        "code_barre",
        "code_barres",                # Phase 33J — French template header
        "ean_code_barres",
        "codebarre",
        "bar_code",
        "ean_barcode",
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
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "label_claims_notes",
        "label_claims",
        "claims_notes",
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
        "periode_de_reporting",       # Phase 33J — French template header
        "quarter",
        "trimestre",
        "year",
        "annee",
        "fiscal_period",
        "report_period",
        "perioden",
    ],
    "items_purchased": [
        "Sales",
        "sales",
        "quantity",
        "qty",
        "units_purchased",
        "volume",
        "volume_nombre_d_unites",     # Phase 33J — French template header
        "volume_nombre_dunites",      # same with curly apostrophe stripped
        "nombre_d_unites",
        "nombre_dunites",
        "nombre_unites",
        "nb_items",
        "nb_units",
        "quantite",
        "quantite_achetee",
        "purchases",
        "units_bought",
        "items",
        "nb_articles",
        "articles_achetes",
        "ventes",           # "sales" in French
        "nb_ventes",
        "total_purchases",
        "purchase_qty",
    ],
    "protein_pct": [
        "Protein per 100 gram",
        "Protein per 100g",
        "protein_per_100_gram",
        "protein_per_100g",
        "protein",
        "protein_percent",
        "proteins",
        "proteines",
        "proteines_totales",          # Phase 33J — French template header
        "pct_protein",
        "protein_percentage",
        "proteines_pct",
        "proteines_percent",
        "protein_g_per_100g",
        "proteines_g_100g",
        "protein_100g",
        "prot_pct",
        "prot",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "protein_total_g_per_100g",
        "proteines_totales_g_par_100g",
    ],
    "plant_protein_pct": [
        "Plant protein per 100g",
        "plant_protein_per_100g",
        "plant_protein",
        "vegetal_protein_pct",
        "proteines_vegetales",        # Phase 33J — French template header
        "proteines_vegetales_pct",
        "plant_prot",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "protein_plant_g_per_100g",
    ],
    "animal_protein_pct": [
        "Animal protein per 100g",
        "animal_protein_per_100g",
        "animal_protein",
        "proteines_animales",         # Phase 33J — French template header
        "proteines_animales_pct",
        "animal_prot",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        "protein_animal_g_per_100g",
    ],
    "items_sold": [
        "units_sold",
        "sales_units",
        "sales_volume",
        "qty_sold",
        "quantity_sold",
        "nb_unites_vendues",
        "sold_units",
        "total_sold",
        # Phase WWF-E — extra French + English aliases per brief.
        "ventes_unites",
        "ventes_unite",
        "quantite_vendue",
        "quantites_vendues",
        "nombre_vendu",
        "nb_vendu",
        "volume_vendu",
        "volume_ventes",
        "items_solds",
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
        "storage_type",
        # Phase WWF-E — extra French + English aliases per brief.
        "canal",
        "canal_de_vente",
        "canal_de_distribution",
        "rayon_canal",
        "distribution_channel",
        "sales_channel",
        # Phase WWF-I-hotfix2 — "100-product dataset" header.
        # ``retail_category`` carries values like "Grocery/Ambient" /
        # "Fresh" / "Frozen"; the parser's enhanced retail-channel
        # alias map now understands them.
        "retail_category",
    ],
}

# Build lookup: normalised_synonym → canonical_field
_SYNONYM_LOOKUP: dict[str, str] = {}
for _canonical, _synonyms in _RAW_SYNONYMS.items():
    for _syn in _synonyms:
        _norm = normalise_header(_syn)
        _SYNONYM_LOOKUP[_norm] = _canonical
    # Canonical name itself is also a valid key
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
    auto_ignore: bool = False  # True for known output/diagnostic columns


class MappingPreviewRequest(BaseModel):
    """Request body for ``POST /api/v1/uploads/preview-mapping``."""

    headers: list[str]
    methodologies: list[str] | None = None  # e.g. ["protein_tracker"] or ["wwf"]


class MappingPreviewResult(BaseModel):
    """Response for the preview-mapping endpoint."""

    entries: list[ColumnMappingEntry]
    missing_required_pt: list[str]
    missing_required_wwf: list[str]
    duplicate_normalised: list[str]


# ---------------------------------------------------------------------------
# Required fields per methodology
# ---------------------------------------------------------------------------

# Phase 33J: external_product_id is no longer required for Protein
# Tracker — when missing, the parser generates an internal ID. We keep
# it required for WWF where SKU continuity matters for the underlying
# basket methodology; an explicit decision can revisit that later.
_REQUIRED_PT: frozenset[str] = frozenset(
    {"product_name", "weight_per_item_kg", "items_purchased"}
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

# Fields for which missing is acceptable if enrichment is available.
# These are shown as warnings in the UI, not hard blockers.
_ENRICHABLE: frozenset[str] = frozenset({"protein_pct"})

# weight_per_item_g satisfies the weight_per_item_kg requirement
# (the pipeline converts g→kg via normalise_weight_kg).
_WEIGHT_VARIANTS: frozenset[str] = frozenset(
    {"weight_per_item_kg", "weight_per_item_g", "weight_per_item_lb", "weight_per_item_oz"}
)

# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def infer_mapping(
    headers: list[str],
    methodologies: list[str] | None = None,
) -> MappingPreviewResult:
    """Infer canonical field mappings for a list of raw CSV headers.

    Parameters
    ----------
    headers:
        Raw header strings exactly as they appear in the CSV (first row).
    methodologies:
        Optional list of methodology slugs enabled for the project
        (e.g. ``["protein_tracker"]``). When provided, ``missing_required_pt``
        is only populated for protein_tracker projects and
        ``missing_required_wwf`` only for wwf projects. When ``None``,
        both are populated (backwards-compatible behaviour for callers
        that don't pass methodology context).

    Returns
    -------
    MappingPreviewResult
        Suggested mapping for each header, plus diagnostic lists of
        missing required fields and duplicate normalised headers.
    """
    entries: list[ColumnMappingEntry] = []
    seen_normalised: dict[str, int] = {}
    mapped_canonical: set[str] = set()

    for raw in headers:
        norm = normalise_header(raw)
        seen_normalised[norm] = seen_normalised.get(norm, 0) + 1

        is_output = norm in _OUTPUT_COLUMN_NORMALISED

        if is_output:
            canonical: str | None = None
            confidence: Literal["exact", "synonym", "none"] = "none"
        elif norm in _SYNONYM_LOOKUP:
            canonical = _SYNONYM_LOOKUP[norm]
            confidence = (
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
                auto_ignore=is_output,
            )
        )

    duplicate_normalised = [n for n, c in seen_normalised.items() if c > 1]

    # Weight variants: any weight unit variant satisfies the weight requirement.
    if mapped_canonical & _WEIGHT_VARIANTS:
        mapped_canonical.add("weight_per_item_kg")

    # Methodology-aware required-field reporting.
    include_pt = methodologies is None or "protein_tracker" in methodologies
    include_wwf = methodologies is None or "wwf" in methodologies

    missing_required_pt = sorted(_REQUIRED_PT - mapped_canonical) if include_pt else []
    missing_required_wwf = sorted(_REQUIRED_WWF - mapped_canonical) if include_wwf else []

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

"""Download endpoints for client data upload templates (Phase 33A).

Every authenticated user (Altera or client) can download templates.
Templates are generated in-memory — no file I/O required.

Available templates:
  GET /api/v1/templates/protein-tracker.csv
  GET /api/v1/templates/wwf.csv
  GET /api/v1/templates/wwf-step2-ingredients.csv
  GET /api/v1/templates/business-assumptions.csv
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from altera_api.auth import authed_user

templates_router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

_CSV_MEDIA = "text/csv"
_DISPOSITION = "attachment"


def _csv_response(filename: str, rows: list[list[str]]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type=_CSV_MEDIA,
        headers={"Content-Disposition": f'{_DISPOSITION}; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Protein Tracker template
# ---------------------------------------------------------------------------

_PT_HEADER = [
    "external_product_id",
    "product_name",
    "brand",
    "retailer_category",
    "retailer_subcategory",
    "weight_per_item_kg",
    "items_purchased",
    "protein_pct",
    "is_own_brand",
    "ingredients_text",
    "labels",
    "country",
    "language",
    "ean",
    "reporting_period",
]

_PT_NOTES = [
    "# REQUIRED: unique product ID / SKU in your system",
    "# REQUIRED: product name as it appears on shelf",
    "# recommended: brand name",
    "# recommended: your internal product category",
    "# recommended: your internal product subcategory",
    "# REQUIRED: item weight in kilograms (e.g. 0.400 for 400 g). Use kg.",
    "# REQUIRED: number of units purchased in the reporting period",
    "# recommended: protein as % of product weight (from nutrition label). If absent Altera can enrich from reference data.",
    "# recommended: true/false — is this an own-brand / private-label product?",
    "# recommended: ingredient list text from product label",
    "# optional: pipe-separated labels e.g. organic|gluten-free|vegan",
    "# optional: ISO 3166-1 alpha-2 country code, e.g. FR",
    "# optional: ISO 639-1 language code, e.g. fr",
    "# optional: EAN / GTIN barcode",
    "# optional: e.g. 2024-Q4",
]

_PT_EXAMPLE1 = [
    "SKU-001",
    "Organic Tofu Block",
    "NatureBio",
    "Chilled Plant-Based",
    "Tofu & Tempeh",
    "0.400",
    "1250",
    "8.0",
    "false",
    "Soybeans (100%)",
    "organic|vegan",
    "FR",
    "fr",
    "3012345678901",
    "2024-Q4",
]

_PT_EXAMPLE2 = [
    "SKU-002",
    "Chicken Breast Fillet",
    "AcmeFarm",
    "Chilled Meat",
    "Poultry",
    "0.500",
    "8400",
    "23.2",
    "true",
    "Chicken breast (100%)",
    "",
    "FR",
    "fr",
    "3012345678902",
    "2024-Q4",
]

_PT_EXAMPLE3 = [
    "SKU-003",
    "Mixed Grain Salad",
    "AcmeChef",
    "Deli",
    "Salads & Ready Meals",
    "0.250",
    "530",
    "",  # protein_pct missing — Altera will enrich
    "true",
    "Rice (30%), Lentils (25%), Peppers (20%), Olive oil (10%), Vinegar (5%)",
    "",
    "FR",
    "fr",
    "",
    "2024-Q4",
]


@templates_router.get("/protein-tracker.csv")
def protein_tracker_template(_auth=Depends(authed_user)) -> StreamingResponse:
    """Download the Protein Tracker CSV upload template."""
    return _csv_response(
        "protein_tracker_template.csv",
        [
            _PT_HEADER,
            _PT_NOTES,
            _PT_EXAMPLE1,
            _PT_EXAMPLE2,
            _PT_EXAMPLE3,
        ],
    )


# ---------------------------------------------------------------------------
# WWF template
# ---------------------------------------------------------------------------

_WWF_HEADER = [
    "external_product_id",
    "product_name",
    "brand",
    "retailer_category",
    "retailer_subcategory",
    "weight_per_item_kg",
    "items_sold",
    "is_own_brand",
    "retail_channel",
    "ingredients_text",
    "labels",
    "country",
    "language",
    "ean",
    "reporting_period",
]

_WWF_NOTES = [
    "# REQUIRED: unique product ID / SKU in your system",
    "# REQUIRED: product name as it appears on shelf",
    "# recommended: brand name",
    "# recommended: your internal product category",
    "# recommended: your internal product subcategory",
    "# REQUIRED: item weight in kilograms (e.g. 0.400 for 400 g). Use kg.",
    "# REQUIRED: number of units sold in the reporting period",
    "# REQUIRED: true/false — is this an own-brand / private-label product?",
    "# REQUIRED: product storage type — one of: fresh | grocery_ambient | frozen",
    "# recommended: ingredient list text (required for Step 2 composite attribution)",
    "# optional: pipe-separated labels e.g. organic|gluten-free|vegan",
    "# optional: ISO 3166-1 alpha-2 country code, e.g. FR",
    "# optional: ISO 639-1 language code, e.g. fr",
    "# optional: EAN / GTIN barcode",
    "# optional: e.g. 2024-Q4",
]

_WWF_EXAMPLE1 = [
    "SKU-001",
    "Organic Tofu Block",
    "NatureBio",
    "Chilled Plant-Based",
    "Tofu & Tempeh",
    "0.400",
    "2100",
    "false",
    "fresh",
    "Soybeans (100%)",
    "organic|vegan",
    "FR",
    "fr",
    "3012345678901",
    "2024-Q4",
]

_WWF_EXAMPLE2 = [
    "SKU-002",
    "Beef Mince 5% Fat",
    "AcmeFarm",
    "Chilled Meat",
    "Beef",
    "0.500",
    "9800",
    "true",
    "fresh",
    "Beef (100%)",
    "",
    "FR",
    "fr",
    "3012345678902",
    "2024-Q4",
]


@templates_router.get("/wwf.csv")
def wwf_template(_auth=Depends(authed_user)) -> StreamingResponse:
    """Download the WWF CSV upload template."""
    return _csv_response(
        "wwf_template.csv",
        [_WWF_HEADER, _WWF_NOTES, _WWF_EXAMPLE1, _WWF_EXAMPLE2],
    )


# ---------------------------------------------------------------------------
# WWF Step 2 ingredients template
# ---------------------------------------------------------------------------
# NOTE: The actual Step 2 upload format is JSON, not CSV.
# This CSV template is for data collection purposes — to help clients
# organise their ingredient data before converting to the JSON upload format.
# See docs/data/input-formats.md for the JSON schema.
# ---------------------------------------------------------------------------

_STEP2_HEADER = [
    "parent_product_id",
    "ingredient_food_group",
    "ingredient_subgroup",
    "ingredient_weight_kg_per_item",
    "notes",
]

_STEP2_NOTES = [
    "# REQUIRED: external_product_id of the parent own-brand composite product",
    "# REQUIRED: WWF food group — one of: FG1 FG2 FG3 FG4 FG5 FG6",
    "# required for FG1/FG2; optional for FG3/FG5 (see docs for valid values)",
    "# REQUIRED: ingredient weight per item in kilograms. Must be > 0.",
    "# optional: free-text notes for your own reference (not uploaded)",
]

_STEP2_NOTE_ROW = [
    "# NOTE: The actual upload format is JSON. Use this CSV to collect data,",
    "# then convert to JSON per docs/data/input-formats.md",
    "",
    "",
    "",
]

_STEP2_EXAMPLE1 = ["SKU-003", "FG2", "freshwater_fish", "0.080", "Salmon fillet portion"]
_STEP2_EXAMPLE2 = ["SKU-003", "FG5", "wheat", "0.050", "Breadcrumb coating"]
_STEP2_EXAMPLE3 = ["SKU-003", "FG4", "", "0.020", "Olive oil"]


@templates_router.get("/wwf-step2-ingredients.csv")
def wwf_step2_template(_auth=Depends(authed_user)) -> StreamingResponse:
    """Download the WWF Step 2 ingredients data collection template."""
    return _csv_response(
        "wwf_step2_ingredients_template.csv",
        [
            _STEP2_HEADER,
            _STEP2_NOTES,
            _STEP2_NOTE_ROW,
            _STEP2_EXAMPLE1,
            _STEP2_EXAMPLE2,
            _STEP2_EXAMPLE3,
        ],
    )


# ---------------------------------------------------------------------------
# Business assumptions template (optional)
# ---------------------------------------------------------------------------

_BIZ_HEADER = [
    "assumption_key",
    "value",
    "unit",
    "notes",
]

_BIZ_NOTES = [
    "# Key name for the assumption",
    "# Numeric value",
    "# Unit (% / EUR / ratio / etc.)",
    "# Free-text description",
]

_BIZ_ROWS = [
    ["total_food_sales", "1200000000", "EUR", "Total food & beverage net sales for the reporting period"],
    ["protein_basket_sales", "180000000", "EUR", "Net sales in protein-containing categories"],
    ["current_plant_share", "12.5", "%", "Plant-based protein as % of protein basket sales"],
    ["current_animal_share", "87.5", "%", "Animal-based protein as % of protein basket sales"],
    ["target_plant_share", "20.0", "%", "Target plant-based share by target year"],
    ["target_animal_share", "80.0", "%", "Target animal-based share by target year"],
    ["private_label_share", "35.0", "%", "Own-brand as % of total food sales"],
    ["plant_based_growth_assumption", "8.0", "%", "Assumed annual growth rate for plant-based category"],
    ["meat_price_inflation_assumption", "3.5", "%", "Assumed annual price inflation for animal protein"],
    ["margin_assumption_plant", "28.0", "%", "Average gross margin on plant-based products"],
    ["margin_assumption_animal", "22.0", "%", "Average gross margin on animal-based products"],
]


@templates_router.get("/business-assumptions.csv")
def business_assumptions_template(_auth=Depends(authed_user)) -> StreamingResponse:
    """Download the optional business assumptions CSV template."""
    return _csv_response(
        "business_assumptions_template.csv",
        [_BIZ_HEADER, _BIZ_NOTES] + _BIZ_ROWS,
    )

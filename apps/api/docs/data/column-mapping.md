# Column Mapping (Phase 33B)

Retailers often use non-standard header names in their export files ("SKU", "Quantité", "nb_articles"). Phase 33B adds a flexible mapping layer so these files can be ingested without manual reformatting.

## How it works

1. **File selection** — The browser reads the first line of the uploaded CSV and extracts the raw header names.
2. **Preview** — The frontend calls `POST /api/v1/uploads/preview-mapping` with the headers. The server normalises each header and looks it up in a server-side synonym registry, returning a suggested `canonical_field` and `confidence` for each.
3. **User review** — The mapping table shows each raw header and a dropdown pre-populated with the suggested canonical field. The user can change any mapping, mark headers to ignore, or leave them as-is.
4. **Upload** — The confirmed mapping is sent alongside the file as `column_mapping: { normalised_header → canonical_field | "ignore" }`.
5. **Pipeline application** — Inside `ingest_csv_bytes`, `apply_column_mapping()` renames row keys before `filter_commercial_columns` and `parse_row`.

## Header normalisation

All headers are normalised before any lookup or mapping is applied:

```
strip whitespace → NFKD decompose (accent marks stripped) → ASCII encode
  → lowercase → non-alphanumeric runs → underscores → strip leading/trailing underscores
```

Examples:
- `"Items Purchased"` → `items_purchased`
- `"Éléments achetés"` → `elements_achetes`
- `"nb_items_purchased"` → `nb_items_purchased`
- `"Product.Name!"` → `product_name`

## Confidence levels

| Confidence | Meaning |
|---|---|
| `exact` | The normalised header exactly equals the canonical field name |
| `synonym` | The normalised header matched a known synonym in the registry |
| `none` | No match found — the user must assign a canonical field manually |

## Canonical fields

| Field | Methodology |
|---|---|
| `external_product_id` | PT + WWF |
| `product_name` | PT + WWF |
| `weight_per_item_kg` | PT + WWF |
| `brand` | PT + WWF |
| `retailer_category` | PT + WWF |
| `retailer_subcategory` | PT + WWF |
| `ingredients_text` | PT + WWF |
| `is_own_brand` | PT + WWF |
| `ean` | PT + WWF |
| `labels` | PT + WWF |
| `country` | PT + WWF |
| `language` | PT + WWF |
| `reporting_period` | PT + WWF |
| `items_purchased` | Protein Tracker |
| `protein_pct` | Protein Tracker (enrichable from CIQUAL) |
| `items_sold` | WWF |
| `retail_channel` | WWF |

## Special values

- `"ignore"` — The column is dropped before entering the pipeline. Commercial-sensitivity check still applies, so a column mapped to a canonical field that matches a commercial pattern will still be dropped.
- `"__none__"` (UI sentinel) — The UI uses this to mean "leave this column as-is, don't include it in the mapping". It is never sent to the server.

## API

### `POST /api/v1/uploads/preview-mapping`

Authentication: required.

Request:
```json
{ "headers": ["SKU", "Product Name", "Quantité", "..."] }
```

Response (`MappingPreviewResult`):
```json
{
  "entries": [
    {
      "raw_header": "SKU",
      "normalised_header": "sku",
      "canonical_field": "external_product_id",
      "confidence": "synonym",
      "enrichment_needed": false
    }
  ],
  "missing_required_pt": ["weight_per_item_kg", "items_purchased"],
  "missing_required_wwf": ["items_sold", "is_own_brand", "retail_channel"],
  "duplicate_normalised": []
}
```

### Upload endpoints

Both upload endpoints accept an optional `column_mapping` parameter:

- **`POST /api/v1/projects/{id}/uploads`** (multipart): `column_mapping` Form field, JSON-encoded.
- **`POST /api/v1/projects/{id}/uploads/{upload_id}/ingest`** (storage flow): `column_mapping` field in the JSON body.

## Synonym registry

The server-side registry (`ingestion/mapping.py`) maps canonical fields to lists of normalised synonyms. Contributions of new synonyms should be made via PR to `_RAW_SYNONYMS`. Run `pytest tests/ingestion/test_phase33b_mapping.py` to verify synonym coverage.

Selected examples:

| Canonical field | Synonyms (sample) |
|---|---|
| `external_product_id` | sku, sku_id, item_code, product_code, article_id, code_article, id_produit |
| `items_purchased` | quantity, qty, units_purchased, quantite, nb_articles, articles_achetes |
| `items_sold` | units_sold, sales_units, qty_sold, nb_ventes |
| `weight_per_item_kg` | weight_kg, poids_kg, unit_weight, net_weight |
| `product_name` | name, description, libelle, libelle_produit, nom_produit |
| `is_own_brand` | own_brand, private_label, marque_propre, mdd |
| `protein_pct` | protein, protein_percent, proteines, pct_protein |

## Security

`filter_commercial_columns` runs **after** `apply_column_mapping`, so commercial/sensitive columns (revenue, margin, supplier pricing) are always stripped regardless of what they are mapped to.

Fields whose canonical name matches a forbidden pattern (see `ingestion/column_filter.py`) will still be dropped.

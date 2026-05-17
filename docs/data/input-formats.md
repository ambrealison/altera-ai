# Input formats

This document specifies the file formats Altera AI accepts as input
and what each field is for.

## Supported formats at MVP

- **CSV** — UTF-8, comma-separated, RFC 4180 quoting. Primary format.
- **TSV** — UTF-8, tab-separated. Same column conventions as CSV.

Files are uploaded through the SaaS UI and stored in Supabase Storage
under the organisation's bucket; see
[../saas/multi-tenancy.md](../saas/multi-tenancy.md).

## Maximum sizes at MVP

- 50 MB per file.
- 200,000 rows per file.

Files exceeding either limit are rejected at the upload boundary with
a clear error.

## Columns

The canonical column set, by methodology. A project enabling both
methodologies needs the union of both methodologies' columns; column
order does not matter.

### Shared columns (always required)

| Column                       | Required | Purpose                                  |
|------------------------------|:--------:|------------------------------------------|
| `external_product_id`        |    x     | Retailer-side stable id                   |
| `product_name`               |    x     | Used for AI classification                |
| `retailer_category`          |          | Taxonomy mapping and AI                   |
| `retailer_subcategory`       |          | Taxonomy mapping and AI                   |
| `brand`                      |          | AI signals                                |
| `is_own_brand`               |  WWF: x  | `true` / `false`. Required for WWF.       |
| `ingredients_text`           |          | AI signals                                |
| `labels`                     |          | Pipe-separated, e.g. `vegan|organic`      |
| `language`                   |          | ISO 639-1                                 |
| `country`                    |          | ISO 3166-1 alpha-2                        |
| `weight_per_item_kg`         |    x     | Per-item weight (drained where applicable) |
| `retail_channel`             |  WWF: x  | `fresh` / `grocery_ambient` / `frozen`     |

### Protein Tracker-only columns

| Column                       | Required | Purpose                                  |
|------------------------------|:--------:|------------------------------------------|
| `items_purchased`            |    x     | Items purchased over the reporting period |
| `protein_pct`                |    x     | % protein by mass (g per 100g)            |
| `protein_source`             |          | `label` or `reference_db`; defaults to `reference_db` if missing |
| `plant_protein_pct`          |          | Enables per-product composite split extension |
| `animal_protein_pct`         |          | Enables per-product composite split extension |

### WWF-only columns

| Column                       | Required | Purpose                                  |
|------------------------------|:--------:|------------------------------------------|
| `items_sold`                 |    x     | Items sold over the reporting period      |
| `wwf_is_composite`           |          | If known; otherwise inferred by classification |

WWF Step 2 ingredient-level data for own-brand composites is supplied
via a separate **companion JSON file** uploaded alongside the CSV; see
the next section.

### Step 2 ingredient file (WWF, optional)

For projects running WWF Step 2, the analyst supplies a JSON file
keyed by `external_product_id`:

```json
{
  "<external_product_id>": {
    "ingredients": [
      {
        "food_group": "FG1",
        "subgroup": "alternative_protein_sources",
        "ingredient_weight_kg_per_item": 0.070
      },
      {
        "food_group": "FG2",
        "subgroup": "dairy_alternative_plant",
        "ingredient_weight_kg_per_item": 0.020
      }
    ]
  }
}
```

Ingredient weights are per item. The system multiplies them by
`items_sold` at calculation time. The sum of ingredient weights for a
product may be less than the product's whole weight (the remainder is
unreported residual, e.g. water).

Step 2 data is accepted only for `is_own_brand=true` products, per
the methodology.

## Disallowed columns

The following columns are **dropped silently** at the ingestion
boundary, with an entry written to the upload's audit metadata:

`sales_value`, `revenue`, `margin`, `cost_price`, `supplier_id`,
`supplier_name`, `contract_terms`, `store_id`, `store_name`,
`store_region`, `promotion_*`, and any column starting with
`confidential_` or `internal_`.

`items_purchased` and `items_sold` are physical quantities required
by the methodologies and are **allowed** in the database, but they
are never included in any prompt sent to an AI provider; see
[../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).

## Header normalisation

Header names are normalised on ingest:

- Lowercased.
- Whitespace and hyphens converted to underscores.
- Surrounding whitespace stripped.

So `"Items Purchased"`, `"items-purchased"`, and `"items_purchased"`
all map to `items_purchased`.

## Encoding and locale

- File encoding must be UTF-8.
- Decimal separator must be `.`.
- Date columns are not used at MVP.

## Future formats

- **Parquet** — efficient ingest for larger uploads.
- **Structured recipe composition** beyond Step 2's JSON, with
  fractional rather than weight-based ingredient breakdowns.

Both are tracked in [../project/scope.md](../project/scope.md) as
deferred past MVP.

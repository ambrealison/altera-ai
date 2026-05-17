# Output formats

For MVP, Altera AI produces three output formats. Each format is a
faithful rendering of the same underlying data; the choice is about
audience, not content.

| Format    | Audience                      | Use                              |
|-----------|-------------------------------|----------------------------------|
| CSV       | Analysts, downstream tooling   | Per-row breakdown, re-aggregation |
| JSON      | Engineers, programmatic users  | Full structured result + metadata |
| Markdown  | Reviewers, stakeholders        | Human-readable summary            |

Excel (`.xlsx`) and PDF are deferred past MVP.

## CSV

A CSV export is the per-row `calculation_rows` for a run, joined to
the product master to include identity fields. The column set is
methodology-specific because the two methodologies produce different
per-row quantities.

### Protein Tracker CSV columns

```
run_id, methodology, methodology_version, methodology_source_edition,
taxonomy_version, rules_version, reporting_period_label,
product_id, external_product_id, product_name, brand,
pt_group,
weight_per_item_kg, items_purchased, volume_kg,
protein_pct, protein_source, protein_kg,
used_per_product_split, plant_protein_kg, animal_protein_kg,
classification_source, classification_confidence,
classification_rule_id, classification_ai_model,
classification_reviewer_user_id
```

### WWF CSV columns

```
run_id, methodology, methodology_version, methodology_source_edition,
taxonomy_version, rules_version, reporting_period_label,
product_id, external_product_id, product_name, brand, is_own_brand,
retail_channel,
wwf_food_group, wwf_subgroup, wwf_is_composite,
wwf_composite_step1_bucket,
weight_per_item_kg, items_sold, weight_kg, weight_kg_dairy_equiv,
wwf_step2_ingredient_weights_json,
classification_source, classification_confidence,
classification_rule_id, classification_ai_model,
classification_reviewer_user_id
```

Encoding: UTF-8 with a BOM. Decimals use `.` and numeric values are
written at full `Decimal` precision.

## JSON

A JSON export is a single document. Numbers are emitted as JSON
strings to preserve `Decimal` precision.

### Top-level shape

```json
{
  "run": {
    "id": "...",
    "methodology": "protein_tracker" /* or "wwf" */,
    "methodology_version": "1.0.0",
    "methodology_source_edition": "GPA & ProVeg Foodservice 2024-08",
    "taxonomy_version": "1.0.0",
    "rules_version": "1.0.0",
    "reporting_period_label": "FY 2024",
    "started_at": "...",
    "finished_at": "...",
    "triggered_by": "..."
  },
  "summary": { /* methodology-specific */ },
  "breakdowns": { /* methodology-specific */ },
  "data_quality": [ ... ],
  "rows": [ /* one per calculation_rows entry */ ]
}
```

### `summary` for Protein Tracker

```json
{
  "total_in_scope_protein_kg": "...",
  "plant_protein_kg": "...",
  "animal_protein_kg": "...",
  "plant_share_pct": "...",
  "animal_share_pct": "...",
  "by_group": {
    "plant_based_core":     { "item_count": 0, "volume_kg": "0.0", "protein_kg": "0.0" },
    "plant_based_non_core": { ... },
    "composite_products":   { ... },
    "animal_core":          { ... }
  },
  "out_of_scope_rows": 0,
  "unknown_rows": 0,
  "per_product_split_rows": 0,
  "pt_validation_status": "draft"
}
```

### `summary` and `breakdowns` for WWF

```json
{
  "summary": {
    "total_in_scope_weight_kg": "...",
    "total_composite_weight_kg": "...",
    "composite_share_of_sales_pct": "...",
    "out_of_scope_rows": 0,
    "unknown_rows": 0
  },
  "breakdowns": {
    "by_food_group": {
      "FG1": { "share_pct": "...", "phd_share_pct": "16.00" },
      "FG2": { "share_pct": "...", "phd_share_pct": "19.00" },
      "FG3": { "share_pct": "...", "phd_share_pct": "4.00" },
      "FG4": { "share_pct": "...", "phd_share_pct": "39.00" },
      "FG5": { "share_pct": "...", "phd_share_pct": "18.00" },
      "FG6": { "share_pct": "...", "phd_share_pct": "4.00" },
      "FG7": { "share_pct": "...", "phd_share_pct": null }
    },
    "fg1_subgroups": { /* per-subgroup share */ },
    "fg1_plant_vs_animal": { "plant_share_pct": "...", "animal_share_pct": "..." },
    "fg2_plant_vs_animal": { "plant_share_pct": "...", "animal_share_pct": "..." },
    "fg3_plant_vs_animal_fats": { ... },
    "fg5_whole_vs_refined_grains": { ... },
    "fg7_plant_vs_animal_snacks": { ... },
    "composites_step1": {
      "meat_based":    "...",
      "seafood_based": "...",
      "vegetarian":    "...",
      "vegan":         "..."
    },
    "composites_step2": { /* per-food-group ingredient-weight shares */ },
    "whole_diet_plant_vs_animal_context": { "plant_share_pct": "...", "animal_share_pct": "..." },
    "by_retail_channel": { /* same shape replicated per channel */ }
  }
}
```

## Markdown

A Markdown export is a single `.md` file suitable for pasting into a
document, email, or PR.

For PT it contains: the headline plant/animal split, the four-group
breakdown table, the data-quality block, the validation state, and a
methodology footnote citing the GPA & ProVeg edition.

For WWF it contains: the per-food-group share table with PHD
references, FG1 subgroup table, plant/animal splits per relevant food
group, the composite Step 1 bucket table, the whole-diet context
line, the data-quality block, and a methodology footnote citing the
WWF 2024 retailer methodology.

The Markdown export does not include per-row data; CSV is the
per-row format.

## Naming

```
altera_<project_slug>_<methodology>_<run_id_short>_<yyyymmdd>.{csv,json,md}
```

## Future formats

- **Excel (`.xlsx`)**: a workbook with separate Summary / Rows /
  Data Quality sheets, plus a PHD-comparison chart for WWF.
- **PDF**: a styled, paginated version of the Markdown report.
  Deferred past MVP. The Markdown export is intentionally
  PDF-friendly so the same content drives both.

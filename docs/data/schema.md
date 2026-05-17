# Data schema

This is the canonical reference for the database entities used by
Altera AI. SQL migrations live under `supabase/migrations/`; this
document explains intent and invariants. When the migration file and
this document disagree, the migration file is what runs, and this
document is the bug.

All tables include `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` and
`created_at TIMESTAMPTZ NOT NULL DEFAULT now()` unless stated
otherwise. All multi-tenant tables include `organisation_id UUID NOT
NULL` and are covered by RLS policies; see [../saas/rls.md](../saas/rls.md).

## Entities

### organisations
The top-level tenant.

| Column        | Type        | Notes                              |
|---------------|-------------|------------------------------------|
| id            | UUID        | PK                                 |
| name          | TEXT        | Display name                       |
| slug          | TEXT UNIQUE | URL slug                           |

### memberships
Joins users to organisations with a role.

| Column          | Type | Notes                                   |
|-----------------|------|-----------------------------------------|
| user_id         | UUID | FK to `auth.users`                       |
| organisation_id | UUID | FK to `organisations`                    |
| role            | TEXT | `owner`, `admin`, `analyst`, `reviewer`, `viewer` |
| PRIMARY KEY (user_id, organisation_id) |

### projects
A unit of work within an organisation.

| Column                | Type | Notes                                  |
|-----------------------|------|----------------------------------------|
| id                    | UUID | PK                                     |
| organisation_id       | UUID | FK to organisations                    |
| name                  | TEXT |                                        |
| methodologies_enabled | TEXT[] | Subset of `{'protein_tracker','wwf'}` |
| reporting_period_label | TEXT | E.g. 'FY 2024'                         |
| reporting_period_start | DATE | Optional                                |
| reporting_period_end   | DATE | Optional                                |
| pinned_pt_version     | TEXT | Nullable                                |
| pinned_wwf_version    | TEXT | Nullable                                |
| pinned_taxonomy_version | TEXT | Nullable                              |
| pinned_rules_version  | TEXT | Nullable                                |
| pt_validation_status  | TEXT | `none`, `draft`, `submitted`, `validated` — applies only to PT-enabled projects |
| created_by            | UUID | FK to `auth.users`                      |

### uploads
A single file submitted by a user.

| Column           | Type | Notes                                              |
|------------------|------|----------------------------------------------------|
| id               | UUID | PK                                                 |
| project_id       | UUID | FK to projects                                     |
| organisation_id  | UUID | Denormalised for RLS                                |
| storage_path     | TEXT | Supabase Storage object key                         |
| original_filename| TEXT |                                                    |
| status           | TEXT | `pending`, `validating`, `valid`, `invalid`         |
| row_count        | INT  | Set after parsing                                    |
| uploaded_by      | UUID |                                                    |

### products
A normalised, validated product row drawn from an upload.

#### Shared identity and content fields
| Column                       | Type   | Notes                                       |
|------------------------------|--------|---------------------------------------------|
| id                           | UUID   | PK                                          |
| upload_id                    | UUID   | FK                                          |
| project_id                   | UUID   | FK                                          |
| organisation_id              | UUID   | Denormalised for RLS                         |
| row_number                   | INT    | 1-based row in the upload                    |
| external_product_id          | TEXT   | Retailer-side stable id                      |
| product_name                 | TEXT   |                                             |
| brand                        | TEXT   | Nullable                                    |
| is_own_brand                 | BOOLEAN| WWF needs this; PT does not                  |
| retailer_category            | TEXT   | Nullable                                    |
| retailer_subcategory         | TEXT   | Nullable                                    |
| ingredients_text             | TEXT   | Nullable                                    |
| labels                       | TEXT[] | E.g. `{'vegan','organic'}`                   |
| language                     | TEXT   | ISO 639-1                                    |
| country                      | TEXT   | ISO 3166-1 alpha-2                           |
| retail_channel               | TEXT   | `fresh`, `grocery_ambient`, `frozen`, NULL — WWF only |

#### Quantity fields
| Column              | Type    | Notes                                  |
|---------------------|---------|----------------------------------------|
| weight_per_item_kg  | NUMERIC | Per-item product weight (drained where applicable). Used by both methodologies. |
| items_purchased     | NUMERIC | PT requires this for the reporting period. NULL if not supplied. |
| items_sold          | NUMERIC | WWF requires this for the reporting period. NULL if not supplied. |

#### Protein content fields (PT only)
| Column                       | Type    | Notes                                       |
|------------------------------|---------|---------------------------------------------|
| protein_pct                  | NUMERIC | Percent by mass; equivalent to g/100g.       |
| protein_source               | TEXT    | `label` or `reference_db`                     |
| plant_protein_pct            | NUMERIC | Nullable; enables per-product composite split extension |
| animal_protein_pct           | NUMERIC | Nullable; enables per-product composite split extension |

#### PT classification (one column block)
| Column     | Type | Notes                                                                                 |
|------------|------|---------------------------------------------------------------------------------------|
| pt_group   | TEXT | One of `plant_based_core`, `plant_based_non_core`, `composite_products`, `animal_core`. System states `out_of_scope`, `unknown` are allowed pre-resolution. |

#### WWF classification (multiple column blocks)
| Column                       | Type    | Notes                                       |
|------------------------------|---------|---------------------------------------------|
| wwf_food_group               | TEXT    | `FG1`..`FG7`, or system `out_of_scope` / `unknown`. |
| wwf_is_composite             | BOOLEAN |                                             |
| wwf_fg1_subgroup             | TEXT    | For FG1 only: `red_meat`, `poultry`, `processed_meats_alternatives`, `seafood`, `eggs`, `nuts_seeds`, `legumes`, `alternative_protein_sources`, `meat_egg_seafood_alternatives`. |
| wwf_fg2_kind                 | TEXT    | For FG2 only: `dairy_animal`, `dairy_alternative_plant`. |
| wwf_fg2_dairy_class          | TEXT    | For FG2 dairy_animal only: `cheese`, `other`. |
| wwf_fg3_kind                 | TEXT    | For FG3 only: `plant_based_fat`, `animal_based_fat`. |
| wwf_fg5_grain_kind           | TEXT    | For FG5 only: `whole_grain`, `refined_grain`. |
| wwf_fg7_kind                 | TEXT    | For FG7 only: `plant_based_snack`, `animal_based_snack`. |
| wwf_composite_step1_bucket   | TEXT    | For composites only: `meat_based`, `seafood_based`, `vegetarian`, `vegan`. |

#### WWF Step 2 composite ingredients
A composite product that is own-brand may have a child table
`product_composite_ingredients`:

| Column            | Type    | Notes                                  |
|-------------------|---------|----------------------------------------|
| id                | UUID    | PK                                     |
| product_id        | UUID    | FK to `products`                        |
| food_group        | TEXT    | `FG1`..`FG6`                            |
| subgroup          | TEXT    | Optional FG-specific subgroup tag       |
| ingredient_weight_kg_per_item | NUMERIC | Weight per item of this ingredient inside the product |
| dairy_class       | TEXT    | If `food_group='FG2' AND wwf_fg2_kind='dairy_animal'`, one of `cheese`, `other` |

Note: the schema does **not** carry any column for sales revenue,
margin, supplier terms, store-level performance, or any commercial
strategy. These are dropped at the ingestion boundary; see
[../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md)
and [../data/input-formats.md](input-formats.md).

### classifications
The most recent classification of a product under one methodology.
There is at most one row per `(product_id, methodology)`. Prior
classifications are kept in `classification_events`.

| Column              | Type   | Notes                                       |
|---------------------|--------|---------------------------------------------|
| product_id          | UUID   | FK                                          |
| methodology         | TEXT   | `protein_tracker` or `wwf`                   |
| category            | TEXT   | The headline category for the methodology (e.g. PT `pt_group`, or WWF `wwf_food_group`). Methodology-specific sub-fields are stored on `products`. |
| source              | TEXT   | `deterministic`, `ai`, `manual_review`        |
| confidence          | NUMERIC| 0.0–1.0                                      |
| rule_id             | TEXT   | Nullable; set when `source='deterministic'`   |
| ai_prompt_version   | TEXT   | Nullable                                    |
| ai_model            | TEXT   | Nullable                                    |
| reviewer_user_id    | UUID   | Nullable; set when `source='manual_review'`   |
| review_reason       | TEXT   | Nullable                                    |
| updated_at          | TIMESTAMPTZ |                                          |
| PRIMARY KEY (product_id, methodology) |

### classification_events
Immutable log of every classification decision made on a product.

| Column            | Type   |
|-------------------|--------|
| id                | UUID   |
| product_id        | UUID   |
| methodology       | TEXT   |
| from_category     | TEXT   |
| to_category       | TEXT   |
| source            | TEXT   |
| confidence        | NUMERIC|
| reviewer_user_id  | UUID   |
| reason            | TEXT   |
| created_at        | TIMESTAMPTZ |

### review_queue
Products awaiting manual review.

| Column         | Type | Notes                                |
|----------------|------|--------------------------------------|
| product_id     | UUID | FK                                   |
| methodology    | TEXT |                                      |
| reason         | TEXT | `low_confidence`, `ai_parse_failed`, `rule_collision`, `requested` |
| created_at     | TIMESTAMPTZ |                              |

### runs
A single calculation run on a project.

| Column                     | Type | Notes                                  |
|----------------------------|------|----------------------------------------|
| id                         | UUID | PK                                     |
| project_id                 | UUID |                                        |
| methodology                | TEXT |                                        |
| methodology_version        | TEXT |                                        |
| methodology_source_edition | TEXT |                                        |
| taxonomy_version           | TEXT |                                        |
| rules_version              | TEXT |                                        |
| reporting_period_label     | TEXT |                                        |
| status                     | TEXT | `pending`, `running`, `success`, `failed` |
| started_at                 | TIMESTAMPTZ |                                  |
| finished_at                | TIMESTAMPTZ |                                  |
| triggered_by               | UUID |                                        |

### calculation_rows
The per-product calculation outputs for a run.

| Column              | Type    | Notes                                  |
|---------------------|---------|----------------------------------------|
| run_id              | UUID    |                                        |
| product_id          | UUID    |                                        |
| in_scope            | BOOLEAN |                                        |
| **PT-specific** fields, nullable for WWF runs |||
| pt_group            | TEXT    |                                        |
| volume_kg           | NUMERIC | `weight_per_item_kg * items_purchased`  |
| protein_pct         | NUMERIC |                                        |
| protein_kg          | NUMERIC | `volume_kg * protein_pct / 100`         |
| used_per_product_split | BOOLEAN | Composite extension; null for non-composites |
| plant_protein_kg    | NUMERIC | Only set if per-product split used      |
| animal_protein_kg   | NUMERIC | Only set if per-product split used      |
| **WWF-specific** fields, nullable for PT runs |||
| wwf_food_group      | TEXT    |                                        |
| wwf_subgroup        | TEXT    | Combined subgroup tag for that food group |
| weight_kg           | NUMERIC | `weight_per_item_kg * items_sold`        |
| weight_kg_dairy_equiv | NUMERIC | Dairy-equivalent applied for FG2 cheese / other / plant-alt |
| wwf_is_composite    | BOOLEAN |                                        |
| wwf_composite_step1_bucket | TEXT |                                     |
| wwf_step2_ingredient_weights | JSONB | For Step 2 own-brand composites |
| retail_channel      | TEXT    |                                        |

### audit_logs
General audit trail (auth, role changes, exports, run lifecycle, PT
validation lifecycle, commercial-data block events, etc.).

| Column            | Type        |
|-------------------|-------------|
| id                | UUID        |
| organisation_id   | UUID        |
| actor_user_id     | UUID        |
| action            | TEXT        |
| target_table      | TEXT        |
| target_id         | UUID        |
| metadata          | JSONB       |
| created_at        | TIMESTAMPTZ |

## Invariants enforced in the database

- A product belongs to exactly one upload and one project, and is in
  the same organisation as both.
- `classifications.category` and the methodology-specific fields on
  `products` must be valid under `classifications.methodology`.
  Enforced with `CHECK` constraints.
- `classification_events` is append-only.
- `calculation_rows.in_scope` is derived from the classification and
  is denormalised for reporting performance, with a `CHECK`
  constraint ensuring agreement.
- A PT-only project may have null WWF fields on `products`; a
  WWF-only project may have null PT fields. A project enabling both
  requires both quantity inputs (`items_purchased` and `items_sold`)
  on every in-scope product.

# Report structure

A **report** is the user-facing view of a run. The report exists in
the web UI and in any export. Its structure is the same regardless of
format; the format is a rendering decision.

A project running both methodologies produces **two parallel report
blocks** — never merged.

## Report lifecycle (Phase 20)

Every `report_exports` row follows a five-stage lifecycle:

```
draft → under_review → approved → delivered
            ↓ rejected (from draft or under_review)
```

| Status         | Who sets it              | Client sees                        |
|----------------|--------------------------|------------------------------------|
| `draft`        | Export renderer          | "Being prepared"                   |
| `under_review` | Any Altera internal      | "Under Altera review"              |
| `approved`     | `altera_methodology_lead`| Downloadable                       |
| `rejected`     | `altera_methodology_lead`| "Being revised" (reason hidden)    |
| `delivered`    | Lead or admin            | "Delivered" — explicitly surfaced  |

Fields stored on `report_exports`:

- `approval_status` — one of the five values above.
- `approved_by` / `approved_at` — set on approval.
- `rejected_by` / `rejected_at` / `rejection_reason` — set on rejection.
- `under_review_by` / `under_review_at` — set when submitted for review.
- `delivered_by` / `delivered_at` — set on delivery.
- `client_downloaded_at` / `client_download_count` — tracked on each client download.

**Download gate**: clients may only download `approved` or `delivered` exports.
Altera staff can preview any status from the internal UI.
The `list_exports` endpoint only returns `approved`/`delivered` records to clients;
Altera sees all statuses.

**Audit events** are emitted for every lifecycle transition and for each
client download: `export.submitted_for_review`, `export.approved`,
`export.rejected`, `export.delivered`, `export.downloaded`.

The header block (below) carries the approval state, the approver,
and the approval timestamp on any client-facing export.

## Protein Tracker report block

1. **Header.** Organisation, project, methodology = `protein_tracker`,
   methodology version, methodology source edition (e.g. `GPA &
   ProVeg Foodservice 2024-08`), taxonomy version, rules version,
   reporting period, run id, started/finished, triggered_by,
   **approval status, approved_by, approved_at, delivered_to_client_at**.
2. **Headline figure.** Plant share (%) and animal share (%) of
   total in-scope protein.
3. **Group totals.** For each of `plant_based_core`,
   `plant_based_non_core`, `composite_products`, `animal_core`:
   item count, total purchase volume (kg), total protein (kg).
4. **Composite handling note.** Either "50/50 default applied to all
   composite protein" or, if the per-product split extension is used
   for some rows, "50/50 default applied to the residual composite
   protein after X rows used per-product splits".
5. **Data quality.** Number of rows where `protein_pct` came from a
   product label vs. a reference DB. Number of rows missing protein
   values. Number of `out_of_scope` and `unknown` rows.
6. **Sources of classifications.** Bar chart and table showing
   counts by `deterministic`, `ai`, `manual_review`.
7. **PT validation state.** `draft` / `submitted` / `validated`.
8. **How to interpret.** Methodology-specific footnote linking to
   the methodology page in the docs and naming GPA / ProVeg as the
   validating bodies.

## WWF report block

1. **Header.** Organisation, project, methodology = `wwf`, methodology
   version, source edition (`WWF Food Practice 2024`), taxonomy
   version, rules version, reporting period, run id, timing,
   triggered_by, **approval status, approved_by, approved_at,
   delivered_to_client_at**.
2. **Per food group share (FG1–FG7).** Percentage of in-scope sales
   by weight per food group; side-by-side with the Planetary Health
   Diet reference share for FG1–FG6.
3. **FG1 subgroup breakdown.** Red meat, poultry, processed meats &
   alternatives, fish & shellfish, eggs, nuts & seeds, legumes,
   alternative protein sources, meat / egg / seafood alternatives.
4. **FG1 plant vs animal split** (within FG1).
5. **FG2 plant vs animal split** (within FG2; animal in dairy
   equivalents — cheese ×10, other ×1; plant alternatives ×1).
6. **FG3 plant fats vs animal fats share.**
7. **FG5 whole grains vs refined grains share.**
8. **FG7 plant vs animal snacks share** (informational).
9. **Composite products.** Overall share of composite in total
   measured sales. Step 1 bucket breakdown: `meat_based`,
   `seafood_based`, `vegetarian`, `vegan`. If Step 2 own-brand data
   is supplied (uploaded via `POST /projects/{id}/wwf-ingredients/upload`,
   Phase 24A), the same numbers re-stated after ingredient attribution.
   Branded composites always appear at Step 1 only.
10. **Whole-diet plant vs animal split.** A single percentage,
    presented as **context only** with a footnote that this number
    alone is not sufficient to monitor diet quality.
11. **Retail channel facet.** Same shares replicated per `fresh` /
    `grocery_ambient` / `frozen` if channel data is supplied.
12. **Data quality.** Number of `out_of_scope` items (and the
    methodology-excluded categories they fell into), number of
    `unknown` items.
13. **Sources of classifications.** Same as PT.
14. **How to interpret.** Methodology footnote linking to the WWF
    methodology page; PHD reference shares cited.

## Two-methodology projects

The two blocks are laid out side-by-side in the UI. They share the
project header but each carries its own methodology header (versions,
source edition, etc.). There is **no** combined headline; the
arithmetic and units differ.

## Report API endpoint (Phase 21)

`GET /api/v1/projects/{project_id}/runs/{run_id}/report` returns a
`ReportDocument` JSON object assembled at request time.

**Access control**

| User type        | Condition                        | Result              |
|------------------|----------------------------------|---------------------|
| Altera internal  | Any run state                    | 200 with full doc   |
| Client           | Export is `approved` or `delivered` | 200 with full doc |
| Client           | No approved/delivered export     | 403                 |

**`ReportDocument` shape**

```json
{
  "meta": {
    "run_id": "...", "project_name": "...", "organisation_id": "...",
    "reporting_period": "2024", "methodology": "protein_tracker",
    "generated_at": "...", "approval_status": "approved",
    "approved_by": "...", "approved_at": "...",
    "delivered_at": null, "export_id": "..."
  },
  "executive_summary": "For the 2024 reporting period, ...",
  "pt_section": { ... },   // populated for protein_tracker runs
  "wwf_section": null,      // populated for wwf runs
  "review_summary": {
    "total_reviewed": 12, "accepted": 8, "changed": 3,
    "deferred": 1, "pending": 2, "top_reasons": ["low_confidence"]
  },
  "coverage": { ... }   // see Phase 22 below
}
```

The executive summary is **deterministic** — no LLM is called; the text
is assembled from a fixed template per methodology, incorporating the
headline metric, methodology version, and approval phrase.

## Data coverage and uncertainty (Phase 22)

The `coverage` field in `ReportDocument` carries upload validation
metrics, product-tier counts, percentages, a deterministic uncertainty
label, methodology-specific caveats, and a review completion note.

### Uncertainty labels

Labels are computed deterministically from the coverage metrics — no
LLM is involved.

| Level    | Triggered by                                                                  |
|----------|-------------------------------------------------------------------------------|
| `high`   | Any blocking upload error; unknown product share ≥ 10%; pending review ≥ 5% of total |
| `medium` | AI-classified share ≥ 30%; missing label protein % ≥ 10% (PT only); missing weight ≥ 10%; any pending review items |
| `low`    | None of the above                                                             |

### `CoverageSection` shape

```json
{
  "uploaded_rows": 500,
  "valid_rows": 498,
  "invalid_rows": 2,
  "warning_count": 3,
  "error_count": 2,
  "products_total": 120,
  "products_classified": 110,
  "products_unknown": 5,
  "products_out_of_scope": 5,
  "products_sent_to_review": 8,
  "products_reviewed_by_altera": 6,
  "products_ai_classified": 30,
  "products_rule_classified": 80,
  "products_manual_classified": 6,
  "products_with_missing_weight": 4,
  "products_with_missing_protein": 2,
  "products_with_missing_category": 1,
  "products_with_missing_ingredients": 10,
  "valid_row_share_pct": "99.60",
  "classified_product_share_pct": "91.67",
  "ai_classified_share_pct": "25.00",
  "manual_review_share_pct": "6.67",
  "unknown_product_share_pct": "4.17",
  "missing_weight_share_pct": "3.33",
  "missing_protein_share_pct": "1.67",
  "uncertainty_level": "low",
  "uncertainty_rationale": "Most products were classified deterministically ...",
  "caveats": [
    "50/50 default protein split applied to all 5 composite product row(s)."
  ],
  "review_completion_note": "6 of 8 manual review item(s) resolved; 2 still pending."
}
```

Fields that are methodology-specific (`products_with_missing_protein`,
`missing_protein_share_pct`) are `null` for WWF runs.
Percentages are `null` when the denominator is zero.

### Recommendations section (Phase 25A)

The `recommendations` field in `ReportDocument` is always present (empty list
when no triggers fire). Each item is a `Recommendation` with:

- `action_type` — unique identifier from the taxonomy (e.g.
  `increase_plant_core_share`)
- `category` — one of: `pt_protein_shift`, `wwf_food_group`, `data_quality`,
  `composite_quality`, `enrichment`
- `title`, `description`, `rationale`, `expected_direction` — display text;
  entirely deterministic, no LLM
- `priority` — `low` / `medium` / `high` / `critical`
- `confidence` — `low` / `medium` / `high`
- `evidence` — bullet list of data points that triggered the recommendation
- `caveats` — static caveats from the taxonomy
- `id` — UUID when loaded from the persistence store; `null` for ephemeral engine output
- `run_id` — UUID when persisted; `null` for ephemeral
- `status` — `draft` | `proposed` | `accepted` | `dismissed` | `archived`
- `client_facing` — `true` for GMS-visible items; `false` for Altera-only

**Phase 25A constraints (still apply):**

- Recommendations are deterministic. Same inputs → same output, no randomness.
- No LLM is called.
- No numeric impact estimates.
- No scenario modelling.
- No unsupported health or nutrition claims.

**Phase 25B — persistence and lifecycle:**

Recommendations can be persisted via `POST /runs/{run_id}/recommendations/generate`
(Altera internal only).  Once persisted, `build_report_document` uses the stored
records rather than calling the engine again.

Lifecycle transitions (all require `ALTERA_METHODOLOGY_LEAD` or `ALTERA_ADMIN`):

```
draft → proposed  (POST /recommendations/{id}/propose)
proposed → accepted  (POST /recommendations/{id}/accept)
any → dismissed   (POST /recommendations/{id}/dismiss)
any → archived    (POST /recommendations/{id}/archive)
```

Client visibility gate: the `GET /recommendations` endpoint and
`build_report_document` filter to `proposed` and `accepted` status for
non-Altera callers.  Altera users see all statuses.

Re-running `generate` is safe: existing records with status already
`proposed`, `accepted`, `dismissed`, or `archived` keep their status;
only new (not yet stored) items are inserted at `draft`.

See [../recommendations/action-taxonomy.md](../recommendations/action-taxonomy.md)
for the full trigger list.

### WWF Step 2 coverage caveats (Phase 24B)

The `caveats` list in WWF coverage sections includes Step 2 disclosure
messages when relevant:

- **Step 2 applied**: `"Step 2 ingredient attribution applied to N own-brand composite product(s). Ingredient weights replace whole product weights for these products in the food-group breakdown."`
  Emitted when `N > 0` own-brand composites have stored Step 2 ingredients.
- **Branded at Step 1**: `"N branded composite product(s) reported at Step 1 (whole product weight). Ingredient-level attribution is not available for branded products."`
  Emitted when `N > 0` branded composites are present in the run.

These caveats are always surfaced when their conditions are met, regardless of
the report's uncertainty level.

### Enrichment caveats (Phase 23A/23B/23C)

The `caveats` list in PT coverage sections is dual-mode:

**Run-mode** (when `use_enriched_nutrition=true` was set on the run):
Caveats reflect what was actually used in the calculation, taken from the run summary:

- **MANUAL used**: `"N product(s) used manually-entered protein % values in this calculation (Altera methodology team override). Enriched values are not from retailer labels."`
- **CATEGORY_AVERAGE used**: `"N product(s) used category-average protein % values in this calculation (statistical fallback, confidence ≤ 0.60). Enriched values are not from retailer labels."`
- **MISSING**: `"N product(s) had missing protein % and no valid enrichment record; excluded from protein totals."`

**Project-mode** (default, when `use_enriched_nutrition=false`):
Caveats reflect the state of stored enrichment records for the project, signalling that enrichment is available but not yet applied:

- **NEEDED**: `"N product(s) are missing label protein %; enrichment from an external or manual source is recommended."`
- **MANUAL stored**: `"N product(s) have manually-entered protein % values (Altera methodology team override) not yet applied to this calculation."`
- **CATEGORY_AVERAGE stored**: `"N product(s) have category-average protein % values (statistical fallback, confidence ≤ 0.60) not yet applied to this calculation."`
- **OTHER stored**: a generic note for other enrichment sources.

The enrichment source for each individual product is stored in `NutritionEnrichmentRecord.source`.

#### Calculation usage policy

Enriched protein values are **not** used in Protein Tracker calculations unless the run
is triggered with `use_enriched_nutrition=true` (Altera internal users only, Phase 23C).
When enabled, the orchestrator pre-resolves a `{product_id: (protein_pct, source)}`
lookup from stored ENRICHED records before calling the pure `calculate_pt_run` function.
Priority: `manual_altera` (0) > `category_average` (1). FAILED/NEEDED/NEEDS_MANUAL_REVIEW
records are ignored. Retailer-provided `pt_fields.protein_pct` values are never overridden.

The formula is identical regardless of whether the protein_pct came from the retailer
label or an enrichment record.

## What is never shown

- Any sales revenue, sales value, margin, or supplier figure.
- Any cross-organisation comparison.
- Any AI rationale beyond the short sentence captured per
  classification. The full rationale is surfaced only on the
  per-product detail view, not the report summary.
- A "blended Protein Tracker / WWF" number, except behind an
  internal experimental feature flag, labelled accordingly.

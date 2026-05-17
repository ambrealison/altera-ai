# Report structure

A **report** is the user-facing view of a run. The report exists in
the web UI and in any export. Its structure is the same regardless of
format; the format is a rendering decision.

A project running both methodologies produces **two parallel report
blocks** — never merged.

## Approval gate

Reports are gated by an **Altera-internal approval step** before they
are visible or downloadable by clients. Every `report_exports` row
carries:

- `approval_status` — `draft` | `approved` | `rejected`.
- `approved_by` — the `altera_methodology_lead` user id at approval.
- `approved_at` — timestamp at approval.
- `delivered_to_client_at` — timestamp when released to the client.

The download endpoint refuses any request from a `gms_client` user
where `approval_status != 'approved'`. Altera staff can preview drafts
and rejected reports inside the internal-operator UI; clients see
only "Under Altera review" in the client UI for those states.

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
   is supplied, the same numbers re-stated after ingredient
   attribution.
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

## What is never shown

- Any sales revenue, sales value, margin, or supplier figure.
- Any cross-organisation comparison.
- Any AI rationale beyond the short sentence captured per
  classification. The full rationale is surfaced only on the
  per-product detail view, not the report summary.
- A "blended Protein Tracker / WWF" number, except behind an
  internal experimental feature flag, labelled accordingly.

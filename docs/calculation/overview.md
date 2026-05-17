# Calculation — overview

A **run** computes the protein-source figures for a project under one
methodology. A project with both methodologies enabled produces two
runs per execution. The two runs are stored, reported, and versioned
independently, and their outputs are in **different units**:

- Protein Tracker: kilogrammes of protein.
- WWF: kilogrammes / tonnes of product weight as sold (with
  dairy-equivalent conversion for FG2 cheese).

The two are not aggregated or averaged.

## Inputs to a run

- The set of products from a single upload (or the union of recent
  uploads in a project; configurable per project).
- The active classification(s) for those products under the run's
  methodology — for PT, the four-group classification; for WWF, the
  food group + subgroup fields plus the composite step bucket and any
  Step 2 ingredient data.
- The run's pinned methodology version, source edition, taxonomy
  version, and rules version.

## Outputs of a run

- A `runs` row with status, timing, methodology, and versions.
- A set of `calculation_rows` (one per product) with the per-row
  quantities used by the aggregator (volume_kg and protein_kg for
  PT; weight_kg and any dairy-equivalent weight for WWF; ingredient
  weights for WWF Step 2 composites).
- Aggregated figures (plant share, per-food-group share, composite
  bucket share, etc.) computed at read-time from `calculation_rows`
  rather than stored as separate rows; this avoids drift between sums
  and totals.

## Computation order

1. **Resolve classifications.** For each product, fetch the active
   classification under the run's methodology. If `unknown`, mark the
   product not in scope.
2. **Resolve quantities.**
   - PT: choose `volume_kg = weight_per_item_kg * items_purchased` and
     `protein_pct` (from product label or reference DB).
   - WWF: choose `weight_kg = weight_per_item_kg * items_sold` (as-
     sold weight, drained weight where applicable). Apply
     dairy-equivalent factors for FG2 cheese / other dairy.
3. **Compute per-product contributions.**
   - PT: `protein_kg = volume_kg * protein_pct / 100`.
   - WWF: for whole products, attribute `weight_kg` to the product's
     food group, subgroup, and any plant/animal split; for composite
     products at Step 1, attribute `weight_kg` to the composite
     bucket; for own-brand composites at Step 2, distribute the
     ingredient weights into food groups.
4. **Aggregate.** Sum per group, per subgroup, per composite bucket,
   per retail channel as the methodology requires.
5. **Derive headline figures.**
   - PT: `plant_share_pct = plant_protein_kg / (plant_protein_kg + animal_protein_kg) * 100`.
   - WWF: per-food-group `share_pct`, FG1 animal/plant split, FG2
     animal/plant split (dairy equivalents), composite bucket shares,
     whole-diet split as context.
6. **Stamp.** Every `calculation_rows` row carries the run id, which
   carries the version fields.

## What runs never do

- They never change a classification. Re-classification is a separate
  operation that occurs before a run.
- They never mix methodologies. A run is bound to one of
  `protein_tracker` or `wwf`.
- They never average the two methodologies into a single figure.

## Re-runs and history

A user may trigger a new run on the same upload at any time. The new
run gets a new `runs.id`. Old runs are retained until the upload is
purged by the organisation. Reports always reference an explicit run
id.

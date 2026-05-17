# WWF calculation

This document specifies the calculation behaviour of the WWF module as
implemented in Altera AI. The methodology definition is in
[../methodologies/wwf.md](../methodologies/wwf.md); that file (and its
source PDF) is authoritative.

## Unit

The unit of the WWF calculation is **kilogrammes (or tonnes) of
product weight as sold**, not protein. Where the product is sold in a
drained state (e.g. canned goods), the **drained weight** is used.

The reporting period is configured per project (typically a fiscal
year).

## Inputs available per product

- `wwf_food_group` — one of `FG1` … `FG7`. Plus system states
  `out_of_scope` and `unknown`, which are excluded from reporting.
- `wwf_fg1_subgroup` — for FG1 only:
  `red_meat`, `poultry`, `processed_meats_alternatives`,
  `seafood`, `eggs`, `nuts_seeds`, `legumes`,
  `alternative_protein_sources`, `meat_egg_seafood_alternatives`.
  The subgroup also encodes plant vs animal:
  `red_meat`, `poultry`, `processed_meats_alternatives`, `seafood`,
  `eggs` are animal; the rest are plant.
- `wwf_fg2_kind` — for FG2 only: `dairy_animal` or `dairy_alternative_plant`.
- `wwf_fg2_dairy_class` — for FG2 only, when `dairy_animal`: `cheese`
  (×10 conversion factor) or `other` (×1). For
  `dairy_alternative_plant`, no conversion factor applies.
- `wwf_fg3_kind` — for FG3 only: `plant_based_fat` or `animal_based_fat`.
- `wwf_fg5_grain_kind` — for FG5 only: `whole_grain` or `refined_grain`.
- `wwf_fg7_kind` — for FG7 only: `plant_based_snack` or `animal_based_snack`.
- `is_composite` — `true` if the product is a composite (multi-
  ingredient, principal components from more than one food group).
- `is_own_brand` — `true` for own-brand; `false` for branded.
- `composite_step1_bucket` — for composites at Step 1, one of
  `meat_based`, `seafood_based`, `vegetarian`, `vegan`.
- `composite_ingredients` — for composites at Step 2 (own-brand only):
  a list of `(food_group_code, ingredient_subgroup, weight_kg)`
  tuples summing to less than or equal to the composite's whole
  weight (the remainder is unreported residual, e.g. water).
- `weight_per_item_kg` — per-item weight as sold.
- `items_sold` — number of items sold over the reporting period.
- `retail_channel` — `fresh`, `grocery_ambient`, or `frozen`.

## Per-product weight

```
weight_kg(p) = weight_per_item_kg(p) * items_sold(p)
```

For composites at **Step 2 (own-brand only)**, the per-ingredient
weights replace `weight_kg(p)` when aggregating into food groups; the
whole-weight composite-bucket figures (`meat_based` etc.) are still
reported alongside.

## Dairy equivalents (FG2)

For any FG2 row classified as `dairy_animal`:

```
fg2_equivalent_weight_kg(p) = weight_kg(p) * dairy_factor(wwf_fg2_dairy_class(p))

where dairy_factor =
  cheese -> 10
  other  -> 1
```

For `dairy_alternative_plant`, no conversion factor is applied
(`factor = 1`).

The dairy equivalents are computed at calculation time; the raw
`weight_kg(p)` value is still retained on the row for traceability.

## Aggregation — protein transition (FG1 and FG2)

For each food group `g ∈ {FG1, FG2}` and each subgroup or animal/plant
split:

```
group_weight_kg(g)                  = sum of weight_kg over whole products in g
                                       + (Step 1 composites attributed to g)
                                       + (Step 2 composites contribute ingredient weights)
group_share_pct(g)                  = 100 * group_weight_kg(g) / total_sales_weight_in_scope
fg1_animal_weight_kg                = sum over FG1 whole products with animal subgroup
                                       + Step 2 composite animal-subgroup ingredient weights
fg1_plant_weight_kg                 = sum over FG1 whole products with plant subgroup
                                       + Step 2 composite plant-subgroup ingredient weights
fg1_animal_share_within_fg1_pct     = 100 * fg1_animal_weight_kg / (fg1_animal_weight_kg + fg1_plant_weight_kg)
fg1_plant_share_within_fg1_pct      = 100 - fg1_animal_share_within_fg1_pct
```

FG2 follows the same shape, using **dairy-equivalent** weights for
animal dairy. The plant-share-within-FG2 numerator uses
`dairy_alternative_plant` weights (×1).

The FG1 subgroup breakdown is reported separately:

```
fg1_subgroup_share_pct(s) = 100 * fg1_subgroup_weight_kg(s) / total_sales_weight_in_scope
```

for each subgroup `s` (red meat, poultry, fish & shellfish, eggs,
processed meats/alternatives, nuts & seeds, legumes, alternative
protein sources, meat/egg/seafood alternatives).

## Aggregation — healthy & sustainable diet shift (FG3–FG7)

If the project's WWF run is configured for the full diet view, the
same `group_share_pct` figure is reported for FG3–FG7. Additional
breakdowns are reported:

- FG3: `animal_based_fats_share_pct`, `plant_based_fats_share_pct`.
- FG5: `whole_grains_share_pct`, `refined_grains_share_pct`.
- FG7: `plant_based_snacks_share_pct`, `animal_based_snacks_share_pct`.

Each is computed as `100 * subgroup_weight_kg / total_sales_weight_in_scope`,
not as a within-food-group share.

## Composite product reporting

At **Step 1**, every composite (own-brand and branded) contributes its
whole weight to one of `meat_based`, `seafood_based`, `vegetarian`,
`vegan`:

```
composite_bucket_share_pct(b) = 100 * composite_bucket_weight_kg(b) / total_composite_weight_kg
overall_composite_share_pct   = 100 * total_composite_weight_kg / total_sales_weight_in_scope
```

At **Step 2** (own-brand only), the composite's ingredient weights are
distributed into FG1 / FG2 (and optionally FG3–FG6) and contribute to
the per-food-group aggregates above. Branded composites continue to be
reported at Step 1. A project may run Step 1 and Step 2 in the same
report; Step 2 ingredient weights replace the Step 1 whole-weight
contribution to food groups for the same own-brand composite, while
the Step 1 bucket figures continue to be reported as context.

## Whole-diet plant-vs-animal split (context only)

```
whole_diet_plant_weight_kg  = sum of plant-attributed weights across FG1, FG2 (dairy alternatives),
                              FG3 plant_based_fat, FG4, FG5, FG6, FG7 plant_based_snack
whole_diet_animal_weight_kg = sum of animal-attributed weights across FG1, FG2 (dairy animal in equivalents),
                              FG3 animal_based_fat, FG7 animal_based_snack
whole_diet_plant_share_pct  = 100 * whole_diet_plant_weight_kg / (whole_diet_plant_weight_kg + whole_diet_animal_weight_kg)
```

This single number is reported as **context**, not as the headline, in
line with the methodology's explicit guidance that the single split
is "not sufficient" to monitor diet quality.

## Planetary Health Diet (PHD) comparison

When configured, the report places each food-group share side-by-side
with its PHD reference share (FG1 16%, FG2 19%, FG3 4%, FG4 39%, FG5
18%, FG6 4%) and shows the gap. PHD reference shares are constants in
the WWF module, versioned with it; changes to PHD references in a
future WWF edition trigger a methodology version bump.

## Reported figures

A WWF result block contains:

- Reporting period.
- Per-food-group share (% of in-scope sales by weight), and the PHD
  reference share for FG1–FG6 where applicable.
- FG1 subgroup breakdown.
- FG1 animal/plant share within FG1.
- FG2 animal/plant share within FG2 (using dairy equivalents).
- FG5 whole-grain vs refined-grain share within FG5.
- FG3 and FG7 plant/animal subgroup shares.
- Composite-product overall share, and Step 1 bucket breakdown
  (`meat_based`, `seafood_based`, `vegetarian`, `vegan`).
- Step 2 own-brand composite-ingredient breakdown if available.
- Per retail channel (`fresh`, `grocery_ambient`, `frozen`): same
  food-group shares facet, when channel data is supplied.
- Whole-diet plant-vs-animal split (context only).
- Number of `out_of_scope` and `unknown` items at run close.
- Methodology version, source edition, taxonomy version, rules version.

## Numerical handling

- All arithmetic uses `Decimal` with 8 decimal places of precision.
- Rounding for display happens at the report layer.
- Underlying values are stored on `calculation_rows` so external
  consumers can aggregate without compounding rounding error.

## Versioning impact

Any change that affects per-row or per-group arithmetic is a **major**
version bump on the `wwf` module. Changes to the PHD reference shares
or dairy-equivalent factors are major. Adding new reported summary
fields without changing arithmetic is a minor bump.

# Protein Tracker calculation

This document specifies the calculation behaviour of the Protein
Tracker module as implemented in Altera AI. The methodology definition
is in [../methodologies/protein-tracker.md](../methodologies/protein-tracker.md);
that file (and its source PDF) is authoritative.

## Unit

The unit of the Protein Tracker calculation is **kilogrammes of
protein**. Volumes are in kilogrammes; protein content is in
percent-by-mass (g per 100g of product equals % by mass).

The reporting period is configured per project; typically one full
calendar year. All products in scope contribute their purchases over
that period.

## Inputs available per product

- `pt_group` — one of `plant_based_core`, `plant_based_non_core`,
  `composite_products`, `animal_core`. (System states `out_of_scope`
  and `unknown` are excluded from the calculation.)
- `weight_per_item_kg` — the per-item weight of the product, in
  kilogrammes.
- `items_purchased` — the number of items purchased over the
  reporting period.
- `protein_pct` — protein content as a percentage of product mass
  (equivalent to grams per 100g).
- `protein_source` — `label` if from the product's ingredients/
  nutrition declaration, `reference_db` if from NEVO Online or an
  equivalent country-specific reference. Recorded for data quality;
  does not affect arithmetic.
- Optionally, for composites with known per-product split (a
  forward-compatible extension): `plant_protein_pct`,
  `animal_protein_pct`. See [Per-product composite split
  extension](#per-product-composite-split-extension) below.

## Per-product calculation

```
volume_kg        = weight_per_item_kg * items_purchased
protein_kg       = volume_kg * (protein_pct / 100)
```

`out_of_scope` and `unknown` rows compute neither volume nor protein
for reporting purposes and are excluded from sums; their counts are
reported separately.

## Per-group aggregation

For each Protein Tracker group `g ∈ {plant_based_core,
plant_based_non_core, composite_products, animal_core}`:

```
group_protein_kg(g) = sum over in-scope products p in group g of protein_kg(p)
group_volume_kg(g)  = sum over in-scope products p in group g of volume_kg(p)
group_item_count(g) = count of in-scope products p in group g
```

## Plant/animal split (the headline)

```
plant_protein_kg  = group_protein_kg(plant_based_core)
                  + group_protein_kg(plant_based_non_core)
                  + 0.5 * group_protein_kg(composite_products)

animal_protein_kg = group_protein_kg(animal_core)
                  + 0.5 * group_protein_kg(composite_products)

total_in_scope_protein_kg = plant_protein_kg + animal_protein_kg

plant_share_pct  = 100 * plant_protein_kg  / total_in_scope_protein_kg
animal_share_pct = 100 * animal_protein_kg / total_in_scope_protein_kg
```

If `total_in_scope_protein_kg == 0`, the report records the shares as
`null` and notes "no in-scope protein found".

## Per-product composite split extension

This is **not part of the published methodology** but is forward-
compatible with what the methodology authors explicitly anticipate:
"Until we have full disclosure of these data, we work with the
assumption that these proteins are 50% animal-sourced and 50% plant-
sourced." The extension is enabled per-row when both
`plant_protein_pct` and `animal_protein_pct` are present and their
sum equals `protein_pct` within a small tolerance.

For such a row:

```
plant_kg(p)  = volume_kg(p) * plant_protein_pct(p)  / 100
animal_kg(p) = volume_kg(p) * animal_protein_pct(p) / 100
```

The row's contribution is added directly to `plant_protein_kg` and
`animal_protein_kg` and is **removed** from `group_protein_kg(composite_products)`
before the 50/50 split is applied to the remaining composite total.

The data-quality section of the report names every composite row that
used a per-product split. If a project requires strict methodology
fidelity, the extension can be disabled per project (default: enabled
when data allows).

## Reported figures

A Protein Tracker result block contains:

- Reporting period.
- Per-group: `group_volume_kg`, `group_protein_kg`, `group_item_count`.
- `plant_protein_kg`, `animal_protein_kg`, `plant_share_pct`,
  `animal_share_pct`.
- Number of composite rows that used a per-product split (if any).
- Number of products whose `protein_pct` came from a reference DB vs.
  a product label.
- Number of `out_of_scope` rows (counts only; not in any protein
  total).
- Number of `unknown` rows at run close (counts only; flagged as a
  data-quality issue).
- Methodology version, source edition, taxonomy version, rules version.

## Numerical handling

- All arithmetic uses `Decimal` with 8 decimal places of precision.
- Rounding for display happens at the report layer, not the
  calculation layer.
- Underlying values are stored on `calculation_rows` so external
  consumers can aggregate without re-introducing rounding error.

## Versioning impact

Any change that affects per-row or per-group arithmetic is a **major**
version bump on the `protein_tracker` module. Adding new reported
summary fields without changing arithmetic is a minor bump. Adopting a
new edition of the source PDF that changes the comparability of
results is a major bump; an edition that only refines wording is a
patch bump.

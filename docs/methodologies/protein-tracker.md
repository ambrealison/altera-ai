# Protein Tracker methodology

This document specifies how Altera AI implements the **Protein Tracker
methodology**. The source is *The Protein Tracker — Foodservice* (Green
Protein Alliance & ProVeg Netherlands, issue: August 2024). A separate
retail edition exists and is structurally identical for our purposes;
where the two diverge, the differences are noted.

The methodology itself is external. This document is the canonical
implementation reference; behaviour in code that deviates from this
document and the source PDF is a bug.

> **Implementation version covered:** `protein_tracker` v1.0.0 — tracks
> the August 2024 foodservice edition.

## Purpose

Protein Tracker quantifies the split between **plant-based and animal
protein in the products a company purchases** over a defined period
(typically one full calendar year). It is a procurement-side
measurement, not a sales or assortment measurement.

The final outputs are the total purchased plant protein, the total
purchased animal protein, and the resulting plant/animal split.

## The four Protein Tracker groups

The methodology defines **exactly four groups**:

| Group                 | Definition                                                                                  |
|-----------------------|---------------------------------------------------------------------------------------------|
| `plant_based_core`    | Products with only plant-based ingredients **and** that act as direct alternatives to animal-protein products (pulses, tofu/tempeh/seitan, plant-based meat/cheese/dairy alternatives, nuts/seeds, hummus, peanut butter, plant-based drinks, etc.). |
| `plant_based_non_core`| Products with only plant-based ingredients but that **do not directly contribute** to a plant-protein shift (fruit, vegetables, rice, bread, pasta, plant-based ready meals/pizzas, plant-based snacks, oils, etc.). |
| `composite_products`  | Products containing both plant- and animal-sourced ingredients **or** product categories where individual products mix plant and animal items and the data does not yet allow a per-product split (ready meals with meat, pizzas with cheese, chocolate, pastries with butter/eggs, vegetarian meat substitutes that contain dairy/eggs, etc.). |
| `animal_core`         | Products made mainly or exclusively from animal ingredients/proteins (meat, poultry, fish/seafood, eggs, dairy, animal-based ready meals, pure animal fats, etc.). |

Note: the methodology has only these four groups. **`out_of_scope`** and
**`unknown`** are system-implementation states used by Altera AI to
manage products outside the methodology's scope (e.g. non-food items)
or items still being classified; they are **not** methodology
categories and are excluded from the reported split.

The methodology limits itself to **food for human consumption** —
non-food items, pet food, and similar are out of scope by definition.

## Linking products to groups

The methodology drives grouping primarily through **product
categories**:

- The source PDF supplies an extensive category mapping (Appendix 1)
  using a Headgroup / Category / Subcategory hierarchy aligned with the
  Dutch RIVM / NEVO Online dataset and foodservice-sector preferences.
- Each (sub)category resolves to one of the four groups.
- Individual products that deviate from their category's default
  (e.g. a plant-based product appearing in an animal-dominated
  subcategory) **may be manually reassigned** to the correct group by
  the company during data analysis.

Altera AI realises this through:

- A versioned [taxonomy](../data/taxonomy.md) that mirrors the PDF's
  Appendix 1 category mapping for PT.
- A [deterministic rules engine](../classification/deterministic-rules.md)
  for product-level overrides driven by keywords, labels, and brands.
- An [AI classifier](../classification/ai-classifier.md) for residual
  products the rules engine cannot place.
- [Manual review](../classification/review.md) as the final authority,
  matching the PDF's instruction that companies may manually reassign
  individual deviating products.

### Labelling rules used by the methodology

- A product labelled **vegan** is automatically a `plant_based_core` or
  `plant_based_non_core` item; the methodology considers proteins from
  fungi and microorganisms (mushrooms, yeast, mycoprotein) as vegan.
- A product labelled **vegetarian** typically contains dairy or eggs and
  is therefore `animal_core` or `composite_products`, not plant-based.

### Why some seemingly plant items live in `composite_products`

The PDF explicitly places categories such as chips, chocolate, granola,
and tapenades into `composite_products` even when most products in them
are plant-based, because individual items inside contain small animal
fractions. The justifications given are:

1. The 50/50 composite assumption (see below) leaves 50% of these
   categories' protein on the plant side anyway, limiting distortion.
2. Treating these categories as fully plant-based would remove the
   incentive to switch to genuinely 100% vegan alternatives.
3. Once data quality improves to enable per-product classification,
   plant items inside these categories will move to plant_core /
   plant_non_core.

The classification engine respects this: a product whose taxonomy
default is `composite_products` only moves to a plant group via an
explicit rule or AI/manual decision.

## Volume and protein-content calculation

For each product `p` purchased during the reporting period:

```
volume_p     = weight_per_item_kg(p) * items_purchased(p)
protein_p_kg = volume_p * (protein_pct(p) / 100)
```

`volume_p` is in kilogrammes; `protein_p_kg` is kilogrammes of protein.
`protein_pct(p)` is the protein content expressed as g per 100g of
product (which equals percent by mass).

Per group `g`:

```
group_protein_kg(g) = sum over products p in g of protein_p_kg
```

## Establishing the plant/animal split

The methodology then aggregates:

```
plant_protein_kg  = group_protein_kg(plant_based_core)
                  + group_protein_kg(plant_based_non_core)
                  + 0.5 * group_protein_kg(composite_products)

animal_protein_kg = group_protein_kg(animal_core)
                  + 0.5 * group_protein_kg(composite_products)
```

The headline figures:

```
plant_share_pct  = 100 * plant_protein_kg  / (plant_protein_kg + animal_protein_kg)
animal_share_pct = 100 * animal_protein_kg / (plant_protein_kg + animal_protein_kg)
```

## The 50/50 composite assumption

The methodology states: *"Until we have full disclosure of these data,
we work with the assumption that these proteins are 50% animal-sourced
and 50% plant-sourced."* The 50/50 split is applied at the **group
level** on the total composite protein.

This is the source of the 50/50 rule. It is a deliberate, methodology-
defined assumption — not a configuration knob.

### Future-compatible extension (not part of the published methodology)

If per-product plant/animal protein splits become available for items
in `composite_products`, those items may be removed from the 50/50
calculation and accounted for using their actual split. The published
methodology anticipates this evolution. Altera AI's data model carries
nullable `plant_protein_g_per_100g` and `animal_protein_g_per_100g`
fields per product to support this once the data is available.

If applied, this extension causes the row to be reported as a
**Per-product-split composite** in the data-quality section, and the
50/50 calculation is then performed only on the remaining composite
rows.

> **Note on the original brief's "Level A / Level B" terminology.** The
> PDF does not use these terms; the methodology specifies a single
> calculation. What we previously called "Level A" corresponds to the
> per-product-split extension above; "Level B" corresponds to the
> as-published methodology. To avoid creating impressions that this
> A/B distinction is part of the methodology, the implementation does
> not expose the terms.

## Sourcing protein content per product

The methodology supports two data sources for `protein_pct(p)`,
prioritised in this order:

1. **Product-level ingredients/nutrition declaration.** Protein per
   100g from the actual product label. This is the most accurate.
2. **National or equivalent reference database.** Where a product
   declaration is missing, an average from a national food-composition
   database (NEVO Online in the Netherlands, or an equivalent
   country-specific dataset) may be used, either per product or as a
   per-product-category average.

Altera AI records the source per product in the data-quality
metadata so a downstream auditor can see which protein values came
from labels and which from reference data.

## Reporting

A Protein Tracker run produces a report block containing:

- The reporting period (e.g. FY 2024).
- The four group totals in kg of protein.
- The 50/50 attribution applied to `composite_products`.
- The plant and animal protein totals.
- The plant and animal share percentages.
- Counts and total purchase volume per group.
- Data-quality flags: protein values from product label vs. reference
  DB, items still in `unknown`, items excluded as out-of-scope.
- Methodology version, taxonomy version, and rules version.

## Validation (governance, not arithmetic)

The Protein Tracker methodology requires that before a company publicly
communicates its results, the calculations are **validated by GPA and
ProVeg**. Altera AI surfaces this as a project state:

- `draft` — calculations have run; results are internal.
- `submitted_for_validation` — results sent for GPA / ProVeg review.
- `validated` — results may be shared externally as "Protein Tracker
  approved".

This is a SaaS workflow, not a calculation behaviour; see
[../saas/workflow.md](../saas/workflow.md).

## What this module never does

- It never blends with the WWF outcome.
- It never reads or transmits commercial data (revenue, margin,
  supplier terms, store-level performance) to any AI provider; see
  [../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).
- It never alters the 50/50 composite default. If a per-product split
  is available the row is excluded from the 50/50 calculation and
  accounted for at its actual split, but the default for unsplit
  composites remains 50/50.

## References

- *The Protein Tracker — Foodservice*, Green Protein Alliance & ProVeg
  Netherlands, August 2024 (the PDF on which this implementation is
  based).
- The retail edition of the Protein Tracker, published by GPA & ProVeg
  in March 2024, used for nation-wide Dutch supermarket benchmarking.
- NEVO Online (Dutch Food Composition Database) — used as the default
  reference DB in NL deployments.

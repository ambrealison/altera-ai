# Methodologies — overview

Altera AI supports two methodologies, which are kept strictly separate
in code and in reporting:

1. **Protein Tracker** — Green Protein Alliance & ProVeg, Aug 2024
   foodservice edition (a retail edition also exists). Source-PDF
   based; the implementation reference is
   [protein-tracker.md](protein-tracker.md).
2. **WWF Planet-Based Diets Retailer Methodology** — WWF Food Practice,
   2024. Implementation reference is [wwf.md](wwf.md).

## How fundamentally different they are

The two methodologies share a goal — quantifying the plant vs. animal
balance of what a retailer or foodservice operator offers — but they
measure **different things in different units**:

| Dimension                       | Protein Tracker                                          | WWF                                                                |
|---------------------------------|----------------------------------------------------------|--------------------------------------------------------------------|
| Unit of measurement              | Kilogrammes of **protein**                                | Kilogrammes / tonnes of **product weight as sold**                  |
| Data source                      | **Purchases** over a reporting period                     | **Sales** over a reporting period                                   |
| Number of categories             | 4 groups                                                 | 7 food groups + per-group plant/animal subgroups + composite buckets|
| Plant-rich vs animal-rich split  | Across all in-scope products (single split)               | Within FG1 and FG2 (and FG3, FG7); plus optional whole-diet split   |
| Treatment of mixed-source items  | `composite_products` bucket; 50/50 default at group level | `composite_products` flag; **no 50/50 default**; reported as Meat-/Seafood-/Vegetarian-/Vegan-based by whole weight (Step 1) or by ingredient weight (Step 2, own brand only) |
| Reference target                  | National policy targets (e.g. NL 50/50 by 2030)           | Planetary Health Diet (PHD), national dietary guidelines            |
| External validation               | GPA & ProVeg validate calculations before public claims   | Self-reported under WWF's framework                                 |

The two outputs are **not comparable**. They are not in the same unit
and they are not aggregations of the same thing. Altera AI never
averages them or produces a single "blended" number.

## Why we keep them separate in code

- Different unit of measurement (protein-kg vs product-kg) means
  different per-product fields, different ingestion validation, and
  different report figures.
- Different category structure (4 PT groups vs 7 WWF food groups with
  subgroups and composite buckets) means different classification
  outputs.
- Different rules for composites (PT applies 50/50 at the group level;
  WWF assigns whole-weight to Meat-/Seafood-/Vegetarian-/Vegan-based
  at Step 1, or splits by ingredient weight at Step 2).
- Different governance (PT requires GPA & ProVeg validation before
  public claims).

This drives the implementation choices in
[../development/adr/0002-strict-methodology-separation.md](../development/adr/0002-strict-methodology-separation.md).

## How a project chooses

When an analyst creates a project, they pick one or both methodologies.
Choosing both runs each independently — there is no shared
classification pipeline at the methodology layer; the deterministic
rules, taxonomy, and AI classifier are each scoped per methodology.
The report shows the two side-by-side, never merged.

## Versioning model

Each methodology module carries a semantic version
(`MAJOR.MINOR.PATCH`):

- `MAJOR` — methodology rules changed in a way that breaks
  comparability with previous results.
- `MINOR` — additive change that does not break prior results.
- `PATCH` — bug fix or wording change with no numerical effect.

Every calculation row stores the exact methodology version, plus the
taxonomy version and rules version, so any row can be re-computed
deterministically. See [versioning.md](versioning.md).

## What this directory contains

- [protein-tracker.md](protein-tracker.md) — full Protein Tracker spec.
- [wwf.md](wwf.md) — full WWF spec.
- [comparison.md](comparison.md) — analyst-facing side-by-side reference.
- [versioning.md](versioning.md) — methodology version policy and source
  citations.

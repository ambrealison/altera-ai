# Unit conversion and normalisation

This document specifies the units used per methodology, accepted
inputs, and what happens when a value cannot be normalised.

## Per-item weight (`weight_per_item_kg`) — both methodologies

Both methodologies require a per-item product weight in **kilogrammes**.
For products sold in a drained state (e.g. canned goods), the
**drained weight** is used, per the WWF methodology. PT does not
specify drained vs. as-sold; for consistency, Altera AI uses drained
weight when both methodologies are enabled.

Accepted input variants for `weight_per_item_kg`:

| Header pattern                  | Conversion                            |
|---------------------------------|---------------------------------------|
| `weight_per_item_kg`            | Used directly                         |
| `weight_per_item_g`             | Divided by 1000                       |
| `weight_per_item_lb`            | Multiplied by 0.45359237              |
| `weight_per_item_oz`            | Multiplied by 0.028349523125          |

Mixed units within a single upload are rejected.

A `weight_per_item_kg` value of `<= 0` or `> 50` is treated as invalid
and the row is rejected with a precise error code; the upper bound of
50 kg is a sanity guard.

## Protein content (`protein_pct`) — PT only

`protein_pct` is the protein content as a **percentage of mass**,
equivalent to grams per 100g of product. Accepted input variants:

| Header pattern                              | Interpretation                              |
|---------------------------------------------|---------------------------------------------|
| `protein_pct`                                | Used directly                                |
| `protein_g_per_100g`                         | Used directly (same numerical value)         |
| `protein_g_per_100ml` + `density_g_per_ml`   | Converted to `protein_pct`                   |
| `protein_g_per_serving` + `serving_g`        | Converted to `protein_pct`                   |
| `protein_kj` / `protein_kcal`                | **Rejected.** Energy is not protein.         |

A `protein_pct` value outside `[0, 100]` is treated as invalid; the
row is rejected.

A missing `protein_pct` causes the row to be classified (so it appears
in counts) but excluded from PT protein totals; the report flags it.

## Item counts (`items_purchased`, `items_sold`)

Both are non-negative integers (decimals accepted but truncated with
a warning). Missing `items_purchased` excludes the product from a PT
calculation; missing `items_sold` excludes it from a WWF calculation.

## Dairy-equivalent factors (WWF)

The dairy-equivalent factor is a constant of the methodology, not an
input. See [../methodologies/wwf.md](../methodologies/wwf.md):

- Cheese (hard and soft) → factor 10.
- Other dairy (milk, yoghurt, cream, buttermilk, sour cream, kefir) →
  factor 1.
- Plant dairy alternatives → factor 1 (no conversion).

The factor is applied at calculation time; the raw `weight_kg` is
retained on each row for traceability.

## Precision and rounding

- All intermediate calculations use `Decimal` with 8 decimal places in
  Python and `numeric` in PostgreSQL.
- Final report figures are rounded to 2 decimal places for percentages
  and 1 decimal place for total kilogrammes.
- Unrounded values are stored on `calculation_rows` so downstream
  consumers can re-aggregate without compounding rounding error.

## What never happens

- The system never silently converts `protein_g_per_100ml` to
  `protein_pct` without a density.
- The system never imputes a missing protein value.
- The system never assumes a default `items_purchased` or `items_sold`.
- The system never converts protein content from kJ or kcal.

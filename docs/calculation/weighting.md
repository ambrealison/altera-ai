# Weighting and quantity bases

The two methodologies use different physical quantities. This document
specifies what is allowed per methodology, what is universally
forbidden, and how units flow through the pipeline.

## Protein Tracker — purchase volume

Protein Tracker is a **procurement-side** methodology: it measures
what a company purchased over a reporting period. The per-product
quantity is:

```
volume_kg = weight_per_item_kg * items_purchased
```

Required columns on the upload for PT:

- `weight_per_item_kg` — per-item product weight (kg per item).
- `items_purchased` — number of items purchased over the reporting
  period.
- `protein_pct` — protein content as % of mass (equivalent to grams
  per 100g). Either from the product's ingredients/nutrition
  declaration or from a national reference database such as NEVO
  Online.
- `protein_source` — `label` or `reference_db`.

## WWF — sales weight

WWF is a **sales-side** methodology measured by **product weight as
sold**, in kilogrammes or tonnes. Required columns on the upload for
WWF:

- `weight_per_item_kg` — per-item product weight (kg per item),
  drained weight where applicable.
- `items_sold` — number of items sold over the reporting period.

Per-product weight:

```
weight_kg = weight_per_item_kg * items_sold
```

Dairy products in FG2 apply a multiplicative **dairy-equivalent
factor** at the calculation stage (cheese ×10, other dairy ×1, plant
dairy alternatives ×1); see
[wwf-calculation.md](wwf-calculation.md).

## Running both methodologies on the same upload

A project that enables both methodologies needs:

- Both `items_purchased` (for PT) and `items_sold` (for WWF). These
  are not interchangeable in general; a retailer's purchase volume
  and sales volume diverge by inventory change, waste, and so on.
- `weight_per_item_kg` is shared between the methodologies.
- `protein_pct` and `protein_source` are required for PT only; WWF
  ignores them.

If the upload supplies only one of `items_purchased` / `items_sold`,
the project can only run the methodology whose quantity is present.

## Universally forbidden

The following data is **never** read by Altera AI and is dropped at
the ingestion boundary, regardless of methodology:

- `sales_value`, `revenue`, `margin`, `cost_price`.
- Any per-store performance metric (`store_*`).
- `supplier_id`, `supplier_name`, `contract_terms`.
- Any column starting with `confidential_` or `internal_`.
- Promotion / discount details.

`items_purchased` and `items_sold` are physical quantities required by
the published methodologies; they are **not** commercial-strategy
data. They are stored in the database for calculation but are **never**
included in any prompt sent to an AI provider. See
[../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).

## Mixed-unit uploads

- Within a single upload, `weight_per_item_kg` must be in kilogrammes.
  Mixed units (kg + lb, etc.) cause the upload to be rejected with a
  precise error.
- An upload may carry both `items_purchased` and `items_sold`. They
  may be equal, but the system does not impose that.

## Why we don't use a generic "weighting basis" label

An earlier draft of this document proposed a generic
`weighting_basis_label` (e.g. `count`, `kg_assortment_volume`). The
published methodologies are specific: PT requires purchases-by-weight
and WWF requires sales-by-weight. Generic labels invited ambiguity
about whether the figure used in the calculation matched the
methodology's intent. The fields above are explicit and per-methodology.

## Retail-channel split (WWF)

WWF additionally requires the per-product retail channel — `fresh`,
`grocery_ambient`, or `frozen` — captured as `retail_channel` on each
product row. This does not affect the totals; it is used to facet the
report.

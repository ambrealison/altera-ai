# WWF Step 1 vs Step 2 — current implementation scope

This note states plainly what Altera AI's WWF implementation does and
does **not** do today, so product copy, demos, and roadmap discussions
all describe the same reality. It complements
[`wwf.md`](./wwf.md) (the full methodology spec) and
[`wwf-calculation.md`](../calculation/wwf-calculation.md) (the formulas).

> **Status:** as of Phase Product-UX-D. The app implements **Step 1
> product-level** classification and reporting. **Step 2 ingredient-level**
> decomposition is **not** implemented.

## What the app implements today (Step 1, product-level)

1. **Product-level classification.** Every in-scope product is classified
   into exactly one WWF food group (FG1–FG7) or a system state
   (out-of-scope / unknown), using the deterministic rules + AI classifier
   described in `wwf-classification-rules.md`.
2. **Composite Step 1 buckets.** A product identified as a *composite*
   (a multi-ingredient prepared product, e.g. a ready meal or pizza) is
   counted at its **whole product weight** and assigned to one Step 1
   bucket: `meat_based`, `seafood_based`, `vegetarian`, or `vegan`. The
   product is **not** broken into its constituent ingredients.
3. **FG1–FG7 whole-product mapping.** Non-composite products contribute
   their whole weight to their food group. Within protein-rich groups
   (FG1, plus the FG2/FG3 plant/animal splits) the plant-vs-animal split
   is applied at the product/subgroup level.
4. **PHD comparison.** Per-food-group shares are compared against the
   Planetary Health Diet reference shares, surfaced in the report.
5. **Whole-diet plant/animal split** and the headline in-scope weight.

The weight used throughout is `weight_kg = weight_per_item_kg × items_sold`.

## What the app does NOT implement (Step 2, ingredient-level)

- **Ingredient-level decomposition of own-brand composites.** Step 2 would
  break an own-brand composite product into its recipe ingredients, assign
  each ingredient to a food group, and distribute the product's weight
  across those food groups by ingredient quantity. The app does **not** do
  this: composites are reported at Step 1 (whole-weight bucket) only.
- As a result, a ready meal that is 60% vegetables / 30% grain / 10% beef
  is today counted entirely in its Step 1 bucket (e.g. `meat_based`), not
  split 60/30/10 across FG4 / FG5 / FG1.

## Data required to enable Step 2

Step 2 needs **quantified recipe composition** per own-brand composite —
data the retailer (or its suppliers) must provide. The minimum shape:

| Field | Purpose |
| --- | --- |
| `product_id` | Links the ingredient row back to the composite product. |
| `is_own_brand` | Step 2 applies to own-brand composites; branded products stay product-level unless recipe data exists. |
| ingredient name | Human-readable label for the ingredient. |
| ingredient percentage **or** weight | The quantity used to distribute the product weight across food groups. At least one is required. |
| ingredient food group | The WWF food group (FG1–FG7) the ingredient maps to. May be classified rather than supplied. |
| source / confidence *(optional)* | Provenance and quality of the recipe line, for auditability. |

Without quantified ingredient weights/percentages, a composite cannot be
split — there is no defensible way to attribute its weight across food
groups.

## Why NEVO is insufficient for Step 2

NEVO (the Dutch food composition database) is used in this app for
**nutrition / protein enrichment**, primarily for the Protein Tracker
methodology. It provides **reference food composition** (nutrient values
for generic/standard foods) — it does **not** provide **retailer
recipe-level ingredient weights** for a specific own-brand product.

- NEVO can tell you the protein content of "cooked lentils"; it cannot
  tell you that a given retailer's ready meal contains 120 g of lentils.
- A product's free-text `ingredients_text` may *help classification*
  (identifying that an ingredient is present) but does not provide the
  **quantified** per-ingredient weights Step 2 requires.

Therefore NEVO, by itself, **cannot** produce WWF Step 2 ingredient-level
breakdowns. NEVO must not be described as a Step 2 data source.

## Future implementation outline

A future Step 2 would layer on top of the existing Step 1 pipeline
without changing Step 1 behaviour:

1. **Step 2 data model.** A per-ingredient table keyed by `product_id`
   carrying ingredient name, quantity (percentage or weight), and food
   group, scoped to own-brand composites.
2. **Ingredient classification.** Reuse the food-group classifier to
   assign each ingredient to FG1–FG7 when the food group is not supplied.
3. **Composite own-brand reporting.** For own-brand composites with
   complete recipe data, distribute the product weight across food groups
   by ingredient quantity (Step 2). Report **both** the Step 1 bucket and
   the Step 2 distribution, as the methodology calls for both.
4. **Branded products remain product-level.** Branded composites (and
   own-brand composites lacking recipe data) continue to be reported at
   Step 1 only. Coverage of Step 2 should be surfaced explicitly so a
   partial roll-out is never mistaken for full ingredient-level reporting.

# WWF Planet-Based Diets Retailer Methodology

This document specifies how Altera AI implements the **WWF Planet-Based
Diets Retailer Methodology** as published in *Achieving a Planet-Based
Diet — A methodology for retailers to track progress toward healthy,
sustainable diets* (WWF Food Practice, 2024). The methodology itself is
external; behaviour in code that deviates from this document and the
source PDF is a bug.

> **Implementation version covered:** `wwf` v1.0.0 — tracks the 2024
> retailer methodology.

## What the WWF methodology measures

WWF measures **sales of food products by weight (tonnes)**, mapped to
food groups, with plant-vs-animal splits within the protein-rich
groups. It deliberately does **not** measure protein content; the
source PDF dedicates a section to why. The reasons given are:

1. Holistic health and sustainability focus rather than a single
   macronutrient.
2. Capturing the entire range of a retailer's offering, including
   non-protein-dense foods.
3. Alignment with established dietary models (Planetary Health Diet,
   national dietary guidelines) which use food quantities, not
   protein content.
4. Consistency and comparability across products and food groups.
5. Practicality — retailers manage sales data in physical volume.

> Implementation consequence: the unit of WWF calculation is
> **kilogrammes (or tonnes) of product sold**, not grams of protein.
> The WWF and Protein Tracker outputs are not in the same units and
> cannot be averaged.

The methodology is targeted at retailers in countries where consumption
of animal-based food exceeds health recommendations and planetary
boundaries.

## The seven food groups

Every in-scope product is mapped to exactly one food group; protein-rich
groups carry a further plant/animal classification.

| Food group | Name                                | Notes                              |
|-----------:|-------------------------------------|------------------------------------|
| FG1        | Protein sources                      | Reported with animal/plant subgroups (see below). |
| FG2        | Dairy and dairy alternatives         | Reported in **dairy equivalents** (conversion factors, see below). Plant/animal split applies. |
| FG3        | Fats and oils                        | Plant/animal split applies (animal-based vs plant-based fats). |
| FG4        | Fruits and vegetables                | Fresh, dried, frozen, canned.       |
| FG5        | Grains / cereals                     | Reported with **whole grain vs refined grain** split. |
| FG6        | Tubers and other starchy foods       |                                    |
| FG7        | Snacks high in added fats, salt, sugar | Plant/animal split reported but not the focus of the protein transition. |

### FG1 subgroups

- **Animal-based:** Red meats; Poultry; Processed meats and alternatives
  (sausages, burgers, nuggets); Seafood (fish, shellfish, etc.); Eggs.
- **Plant-based:** Nuts and seeds; Legumes (beans, lentils, chickpeas,
  etc.); Alternative protein sources (tofu, tempeh, seitan, etc.);
  Meat, egg, and seafood alternatives.

### Foods explicitly excluded from reporting

Per the methodology: alcoholic and non-alcoholic beverages (except
dairy-based drinks); tea, coffee, cocoa; herbs and spices; condiments
(barbecue sauce, ketchup, mustard); flavourings; additives; vitamin
supplements; baby formula and baby purees; stock cubes/powders and
liquid broth; novel / unusual proteins; salt.

In Altera AI these resolve to the system state `out_of_scope`. They are
not a methodology category; they are simply excluded from reporting.

## Whole products and composite products

The methodology distinguishes two product types:

- **Whole products** — individual food items primarily sourced from a
  single food group (sausages, chicken breast, fruit yogurt, chickpeas,
  bread, oil, almonds).
- **Composite products** — multi-ingredient items whose principal
  components come from more than one food group (chicken curry meals,
  ham sandwiches, salads with added proteins or grains).

Both whole and composite products carry a `branded` vs `own_brand`
flag, because the available reporting depth differs (see Step 2 below).

> The published WWF methodology does **not** apply a 50/50 default
> split to composites. The 50/50 rule is a Protein Tracker concept.
> This is one of the most common cross-methodology confusions; the
> module never applies a Protein Tracker rule to WWF.

## Weight and dairy equivalents

Sales weight is measured **as sold**. If the product weight is provided
in a drained state (e.g. canned goods), the drained weight is used.

**Dairy products (FG2) are reported in dairy equivalents.** The
methodology applies the following conversion factors:

| Conversion factor | Products                                                                   |
|------------------:|----------------------------------------------------------------------------|
| 1:10              | Hard and soft cheese (cheddar, parmesan, gouda, tilsiter, brie, camembert, cream cheese, ricotta, cottage cheese) |
| 1:1               | Other dairy products (milk, yoghurt, cream, buttermilk, sour cream, kefir) |

For example, 100 kg of cheese is reported as 1,000 kg dairy equivalent.
The conversion is applied at calculation time; it does not change the
underlying product weight stored in `products`.

## Stepwise approach (Step 1 and Step 2)

WWF specifies a **two-step approach** that retailers progress through
as their data quality improves. This is internal to the WWF methodology
and is distinct from the (different) "two ways" PT mentions for
sourcing protein content.

### Protein Transition — Step 1: Composite Product Level

For every composite product (own brand **and** branded), the **whole
product weight** is assigned to one of four composite categories based
on the product as a whole, **not** its ingredients:

- `meat_based` — composite contains meat.
- `seafood_based` — composite contains fish or seafood (and no meat).
- `vegetarian` — composite contains dairy or eggs (and no meat or
  seafood).
- `vegan` — composite is free from animal components.

Worked example from the PDF: a 400g vegan lasagna is recorded as 400g
in `vegan`; a 600g chicken curry with rice is recorded as 600g in
`meat_based` even though the chicken alone weighs less than 600g.

Whole products at Step 1 are categorised into their relevant food group
and (for FG1, FG2, FG3, FG7) into plant-based or animal-based
subgroups.

### Protein Transition — Step 2: Composite Ingredient Level (own brand)

For **own-brand composite products**, retailers break down the
composite into its ingredient weights and assign each ingredient to
FG1 / FG2 (optionally FG3–FG6). Example from the PDF: a vegan lasagna
contributes 70g vegan minced meat substitute to FG1 and 20g vegan
cheese alternative to FG2.

Branded composite products continue to be reported at the Composite
Product Level (Step 1) until ingredient data becomes industry-
available.

#### Step 2 upload API (Phase 24A / hardened Phase 24B)

The companion ingredient JSON is uploaded via:

```
POST /api/v1/projects/{project_id}/wwf-ingredients/upload
Content-Type: multipart/form-data  (field name: "file")
```

The file must be uploaded **after** classification, because the validator
checks that each product has a WWF classification and is marked composite.

**File-level limits (Phase 24B):**

| Limit | Value | HTTP status on breach |
|-------|-------|-----------------------|
| File size | 50 MB | 413 |
| Total ingredient rows (sum of all `ingredients` arrays) | 200,000 | 422 |

**JSON shape validation (Phase 24B):** The top-level value must be a
dict. Each product entry must be a dict, must have an `"ingredients"`
key, `"ingredients"` must be a list, and the list must be non-empty.
Violations are hard errors that abort processing for that product.

**Validation rules applied at upload time:**

| Rule | Severity |
|------|----------|
| `external_product_id` must exist in the project | error |
| Product must have WWF enabled | error |
| Product must have a WWF classification (classify before upload) | error |
| Product must be classified as composite (`wwf_is_composite=true`) | error |
| Product must be own-brand (`is_own_brand=true`) | **warning** (ingredients not stored; product stays at Step 1) |
| `food_group` must be FG1–FG6 (FG7 rejected) | error |
| FG1 requires a valid `subgroup` | error |
| FG2 requires a valid `subgroup` | error |
| FG3 entry missing `subgroup` | **warning** (stored; plant/animal fat split excluded from whole-diet calculation) |
| FG3 `subgroup` must be `"plant_based_fat"` or `"animal_based_fat"` if present | error |
| FG5 `grain_kind` must be `"whole_grain"` or `"refined_grain"` if present | error |
| Duplicate `(food_group, subgroup)` combo within the same product | **warning** (both rows stored) |
| `ingredient_weight_kg_per_item` must be strictly positive | error |
| Sum of ingredient weights > product weight | **warning** (storage allowed) |

**FG3 subgroup and FG5 grain kind (Phase 24B):**

- FG3 ingredients may carry a `"subgroup"` field:
  `"plant_based_fat"` or `"animal_based_fat"`. When present it determines
  the ingredient's contribution to the whole-diet plant/animal split.
  When absent the ingredient is stored but excluded from that split (a
  warning is emitted at upload time, and a coverage caveat is emitted on
  the report). Use when fat source is known.
- FG5 ingredients may carry a `"grain_kind"` field:
  `"whole_grain"` or `"refined_grain"`. Stored for future reporting; not
  yet used in the main calculation.

**Step 2 coverage disclosure (Phase 28A-4):**

Reports include four deterministic caveats to disclose Step 2 completeness:

1. **Denominator**: `"Step 2 ingredient attribution was applied to X of Y own-brand composite product(s)."` — shows the coverage fraction, not just the count.
2. **Own-brand Step 1 only**: `"Z own-brand composite product(s) remain reported at Step 1 only."` — emitted when `Z = Y − X > 0`.
3. **Branded Step 1 only**: `"N branded composite product(s) reported at Step 1 (whole product weight) only."` — always the case per the methodology.
4. **FG3 gap**: `"M FG3 (fats and oils) Step 2 ingredient row(s) had no plant/animal subgroup specified; their weight was excluded from whole-diet plant/animal split totals."` — emitted when stored FG3 ingredients have no `subgroup` field.

The Step 1 composite weight reported in the main result table is always complete and unaffected by Step 2 data gaps.

**Re-upload semantics (Phase 24B):**

When a new upload is accepted (no hard errors), all previously stored
Step 2 ingredients for the project are replaced atomically. The response
carries `"replaced": true` when previous data existed. Invalid uploads
are rejected without touching stored data — the previous state is
preserved.

If there are no hard errors, the ingredients are stored and the response
carries `"stored": true`.  A `GET /projects/{id}/products/{pid}/wwf-ingredients`
endpoint retrieves stored ingredients per product.

### Healthy & Sustainable Diet Shift (optional, either step)

In addition to the protein-transition view (FG1 + FG2), retailers may
optionally extend the analysis to all food groups (FG3–FG7), reporting
the percentage of sales (by weight) per food group. This is then
compared to the **Planetary Health Diet (PHD)** reference proportions:

| Food group              | % of food sales (PHD) | Plant share | Animal share |
|-------------------------|----------------------:|-------------|--------------|
| FG1 Protein-rich foods  | 16%                   | 60%         | 40%          |
| FG2 Dairy foods         | 19%                   | 0%          | 100%         |
| FG3 Fats and oils       | 4%                    | 90%         | 10%          |
| FG4 Fruits and vegetables| 39%                  | 100%        | n/a          |
| FG5 Grains and cereals  | 18%                   | 100%        | n/a          |
| FG6 Tubers / starchy    | 4%                    | 100%        | n/a          |

FG7 sales should be reduced overall; no target proportion is given.

Whole grains (FG5) are reported as a share of total grains; the
methodology references the international whole-grain definition (intact
kernel anatomical components in the same relative proportions) and
defers to country-specific whole-grain definitions where applicable.

## Retail-channel categories

All reporting splits sales across three retail channels: **fresh**,
**grocery / ambient**, **frozen**. These are captured per product in
Altera AI and surfaced as facets in the report; they do not affect the
arithmetic.

## Reported figures

A WWF result block contains:

- **Per food group:** percentage of sales by weight.
- **For FG1 and FG2:** plant-based vs animal-based percentage of sales.
- **For FG1:** the subgroup breakdown (red meat / poultry / fish &
  shellfish / eggs / nuts & seeds / legumes / alternatives).
- **For FG5:** whole-grain share within total grains.
- **Composite products:** overall share of composite in total
  measured sales; breakdown into `meat_based`, `seafood_based`,
  `vegetarian`, `vegan` at the composite product level (always); and,
  when Step 2 data is available for own-brand composites, the same
  food-group breakdown applied to ingredient weights.
- **Healthy & Sustainable Diet Shift:** every food group's actual share
  side-by-side with the PHD reference share.
- **Whole-diet plant/animal split:** a single plant vs animal
  percentage across all in-scope food groups. The methodology
  explicitly notes this single number is *not sufficient* to monitor
  diet healthiness because it does not reflect per-food-group
  proportions; it is therefore surfaced as a context figure, not the
  headline.

The methodology versions, taxonomy version, rules version, and
reporting period are stamped on every result.

## What this module never does

- It never blends with the Protein Tracker outcome.
- It never applies a 50/50 default to composites — that is a Protein
  Tracker rule and is not part of WWF.
- It never measures protein content as the primary unit; the unit of
  measurement is product weight as sold.
- It never reads or transmits commercial data (revenue, margin,
  supplier terms, store-level performance, confidential strategy) to
  any AI provider. Note: the methodology does require sales weight
  (tonnes), which is the unit of measurement, not revenue — it is
  recorded in the database for calculation but never sent to a prompt;
  see [../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).

## References

- *Achieving a Planet-Based Diet — A methodology for retailers to track
  progress toward healthy, sustainable diets*, WWF Food Practice
  (Meyer, Halevy, Huggins, Loken), 2024.
- The Planetary Health Diet (EAT-Lancet Commission), used as the
  reference diet model in the methodology.
- WWF Living Planet Report 2020, Bending the Curve, and related WWF
  publications cited by the methodology.

# Methodology comparison — analyst reference

This document is a **side-by-side reference** for analysts who need to
explain to stakeholders why a single product can carry two very
different classifications, or why the two methodologies cannot be
averaged. It is not a hybrid methodology, and the code does not read
this document to produce any number.

## Headline differences

| Dimension                       | Protein Tracker                                            | WWF                                                                |
|---------------------------------|-----------------------------------------------------------|--------------------------------------------------------------------|
| Unit                             | kg of protein                                              | kg / tonnes of product weight as sold                              |
| Source data                      | Purchases over a defined period                            | Sales over a defined period                                        |
| Categories                       | 4 groups                                                  | 7 food groups; plant/animal subgroups within FG1, FG2, FG3, FG7    |
| Composite handling               | `composite_products` bucket, 50/50 default at group level  | `composite_products` flag; Step 1: whole-weight into Meat-/Seafood-/Vegetarian-/Vegan-based; Step 2 (own-brand only): ingredient-level food-group breakdown |
| Reference target                 | National targets (e.g. NL 50/50 by 2030)                   | Planetary Health Diet; national dietary guidelines                 |
| Whole-grain handling             | Not part of the methodology                                | FG5 reports whole-grain vs refined-grain share                     |
| Dairy reporting                  | Treated as `animal_core` like other animal proteins        | FG2 reported in **dairy equivalents** (cheese ×10, other ×1)       |
| Validation                       | External (GPA & ProVeg) before public claim                | Self-reported                                                      |

## Category mapping

Protein Tracker groups (left) do not map 1:1 to WWF food groups
(right). A few illustrative correspondences:

| Example product            | Protein Tracker group      | WWF                                          |
|----------------------------|----------------------------|----------------------------------------------|
| Lentils (canned)           | `plant_based_core`         | FG1 plant-based: legumes                     |
| Tofu                       | `plant_based_core`         | FG1 plant-based: alternative protein sources |
| Wholemeal bread            | `plant_based_non_core`     | FG5 grains: whole grains                     |
| White rice                 | `plant_based_non_core`     | FG5 grains: refined grains                   |
| Beef                       | `animal_core`              | FG1 animal-based: red meat                   |
| Cheddar cheese             | `animal_core`              | FG2 dairy (with ×10 dairy-equivalent factor) |
| Olive oil                  | `plant_based_non_core`     | FG3 plant-based fats                         |
| Butter                     | `animal_core`              | FG3 animal-based fats                        |
| Frozen chicken curry meal  | `composite_products`       | Composite product → whole-weight into FG-aware "meat-based" (Step 1) or split by ingredients across FG1, FG2, FG5 (Step 2, own-brand only) |
| Vegan lasagna (frozen)     | `plant_based_non_core` *or* `composite_products` depending on category mapping | Composite product → whole-weight into "vegan" (Step 1) or split by ingredients across FG1, FG2, FG5 (Step 2, own-brand only) |
| Coffee beans                | `plant_based_non_core` (per PT PDF appendix) | Excluded from WWF reporting                   |
| Alcoholic beverages         | `plant_based_non_core` (per PT PDF appendix) | Excluded from WWF reporting                   |
| Cleaning products           | system `out_of_scope`      | system `out_of_scope`                        |

## Example: a meat-and-cheese pizza, 500g, branded

- **Protein Tracker:** classified as `composite_products`. The 500g
  product weight × items purchased × protein% gives the product's
  protein contribution; that contribution joins the composite group,
  half plant / half animal.
- **WWF (Step 1):** the 500g whole weight is recorded as a composite
  product in the **meat-based** bucket. It also contributes its whole
  weight to "share of composite in overall sales".
- **WWF (Step 2, own-brand only):** if the recipe is known, the meat
  weight goes to FG1 animal-based; the cheese weight, to FG2 (with
  dairy-equivalent ×10 conversion); the dough weight, to FG5 grains;
  any vegetables, to FG4. If the product is branded and ingredient
  data is unavailable, Step 2 does not apply for this product.

The two methodologies record this same product entirely differently.
Neither output is "wrong"; they answer different questions.

## What stakeholders should take away

- The two methodologies answer related but **non-comparable**
  questions. They are reported in different units and at different
  granularities.
- A retailer can publish one, the other, or both side-by-side. They
  should never be averaged or combined.
- If a stakeholder asks for "one number", pick one methodology, cite
  its version (and reporting period), and stay with it.
- Protein Tracker requires GPA & ProVeg validation before public
  communication; WWF does not require external validation but is
  intended to be reported against the Planetary Health Diet
  benchmark.

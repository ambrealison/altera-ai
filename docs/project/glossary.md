# Glossary

Terms are listed alphabetically. Where a term has a methodology-
specific meaning, the methodology is indicated in brackets.

**Animal core (PT)** — A product made mainly or exclusively from
animal ingredients/proteins: meat, poultry, fish/seafood, eggs,
dairy, animal-based ready meals, pure animal fats.

**Animal-based (WWF, FG1)** — A protein-rich product whose protein
source is of animal origin: red meat, poultry, processed meats and
alternatives, seafood, eggs.

**Assortment** — The set of products a company offers. For Altera AI,
an assortment is captured as the set of products active in a project,
typically sourced from a single upload.

**Branded product (WWF)** — A product manufactured and sold under a
brand owned by a third party distinct from the retailer. Branded
composites are reported at the Composite Product Level (Step 1) until
ingredient data is widely available.

**Composite Ingredient Level (WWF, Step 2)** — Reporting for **own-
brand** composite products that uses the ingredient weights within the
product, distributing them into food groups. Branded composites are
not in scope for Step 2.

**Composite Product Level (WWF, Step 1)** — Reporting that uses the
whole weight of a composite product, assigned to one of `meat_based`,
`seafood_based`, `vegetarian`, `vegan`.

**Composite products (PT)** — Products containing both plant- and
animal-sourced ingredients, or product categories in which individual
items mix plant and animal protein and the data does not yet allow a
per-product split. PT applies a 50/50 default split at the group
level.

**Composite product (WWF)** — A multi-ingredient food item whose
principal components come from more than one food group (chicken
curry, ham sandwich, salad with added proteins).

**Dairy equivalent (WWF, FG2)** — A conversion applied at calculation
time to express dairy products on a common basis. Hard and soft cheese
× 10; other dairy × 1; plant-based dairy alternatives × 1.

**Deterministic rules engine** — The rule-based classifier that runs
before the AI classifier. Reproducible per (rule version, input row).

**Food group (WWF, FG1–FG7)** — Top-level WWF category structure:
FG1 protein sources, FG2 dairy & dairy alternatives, FG3 fats and
oils, FG4 fruits & vegetables, FG5 grains/cereals, FG6 tubers &
starchy foods, FG7 snacks high in added fats / salt / sugar.

**Items purchased** — The number of items of a product purchased by a
company over the reporting period. Used by Protein Tracker. Not the
same as items sold.

**Items sold** — The number of items of a product sold by a retailer
over the reporting period. Used by WWF. Not the same as items
purchased.

**Manual review** — The human-in-the-loop step. All decisions are
logged as immutable events. Manual review is the final authority on
every product's classification.

**Methodology version** — A semantic version of a methodology module.
Stamped on every calculation row.

**Methodology source edition** — A string identifying the published
edition of a methodology that a methodology version implements (e.g.
`GPA & ProVeg Foodservice 2024-08`, `WWF Food Practice 2024`).

**Own-brand product (WWF)** — A product created and marketed by the
retailer, including private-label brands owned by the retailer/group
but not explicitly labelled with the retailer's name. Required for
WWF Step 2 ingredient-level reporting.

**Out of scope** — A product the methodology does not classify
(non-food, methodology-excluded categories such as alcohol and salt
under WWF). System state, not a methodology category.

**Planetary Health Diet (PHD)** — The reference dietary model used by
WWF for its food-group target proportions (FG1 16%, FG2 19%, FG3 4%,
FG4 39%, FG5 18%, FG6 4%).

**Plant-based core (PT)** — Products with only plant-based ingredients
that act as direct alternatives to animal-protein products: pulses,
tofu/tempeh/seitan, plant-based meat/cheese/dairy alternatives,
nuts/seeds, hummus, plant-based drinks.

**Plant-based non-core (PT)** — Products with only plant-based
ingredients but that do not directly contribute to a plant-protein
shift: fruit, vegetables, rice, bread, pasta, plant-based ready
meals/pizzas, plant-based snacks, oils.

**Plant-based (WWF, FG1)** — A protein-rich product whose protein
source is plant: nuts & seeds, legumes, alternative protein sources
(tofu, tempeh, seitan), meat / egg / seafood alternatives.

**Plant dairy alternative (WWF, FG2)** — A plant-based product that
substitutes for dairy (soy milk, oat milk, plant yoghurt, plant-based
cream, etc.). No dairy-equivalent factor (×1).

**Protein-source ratio** — In WWF, the plant-to-animal share computed
within FG1 (and separately within FG2). It is reported as a
**within-food-group** share, by **weight**, not by protein content.

**Protein Tracker group** — One of `plant_based_core`,
`plant_based_non_core`, `composite_products`, `animal_core`.

**Reference DB (PT)** — A national or equivalent food-composition
database used to obtain protein content per product or category where
a product-level declaration is unavailable. NEVO Online is the Dutch
default.

**Reporting period** — The interval over which a project's purchases
(PT) or sales (WWF) are aggregated, typically one fiscal year.

**Retail channel (WWF)** — `fresh`, `grocery_ambient`, or `frozen`.
Faceting dimension; does not change totals.

**Rules version** — Semver of the deterministic rules. Stored on
every calculation row.

**RLS (Row-Level Security)** — PostgreSQL/Supabase mechanism that
restricts row access based on the requesting user's organisation.

**Taxonomy** — The product category tree used by Altera AI to map
retailer/foodservice category strings to methodology-relevant
classifications. Versioned independently of methodologies and rules.

**Taxonomy version** — Semver of the taxonomy. Stored on every
calculation row.

**Unknown** — A classification outcome assigned when neither the
deterministic engine nor the AI classifier could classify a product
with sufficient confidence, and manual review has not yet resolved
it. System state, not a methodology category.

**Validated (PT)** — A PT project state indicating the calculations
have been reviewed by GPA & ProVeg and may be communicated
externally.

**Whole grain (WWF, FG5)** — Grain consisting of the intact, ground,
cracked, or flaked kernel containing the starchy endosperm, germ, and
bran in the same relative proportions as the intact kernel. Used to
split FG5 into whole vs refined.

**Whole product (WWF)** — An individual food item primarily sourced
from a single food group: sausages, chicken breast, fruit yogurt,
chickpeas, bread, oil, almonds.

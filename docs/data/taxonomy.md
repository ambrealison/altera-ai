# Taxonomy

The taxonomy is the bridge between **retailer / foodservice category
strings** (whatever the company happens to call its categories) and
**methodology-relevant classifications**. It serves both Protein
Tracker (4 groups) and WWF (7 food groups with subgroups and composite
buckets).

The taxonomy is a versioned data artefact, not application code. It
lives under `packages/taxonomy/` and is referenced by the
deterministic rules engine and by the AI classifier's prompt as
supporting context.

## Why a taxonomy exists

A retailer may list "Plant-based meals" under "Ready meals >
Vegetarian" or under "World foods > Vegan" or as a top-level
"Plant-based" tab. The deterministic engine cannot rely on retailer
strings alone. The taxonomy maps each retailer string seen during
onboarding (or matched by pattern) to a node in a canonical tree.

The canonical tree is **based on the source PDFs' own category
mappings**:

- For PT, the source's Appendix 1 (Headgroup / Category / Subcategory
  table) is the reference for the four-group assignment.
- For WWF, the source's Appendix A guidance plus the food-group
  structure (FG1–FG7), subgroups within FG1, and composite-bucket
  classification (Meat-/Seafood-/Vegetarian-/Vegan-based for Step 1).

## WWF food group definitions

| Group | Name | Notes |
|-------|------|-------|
| FG1   | Protein foods | Animal proteins (red meat, poultry, processed meats, seafood, eggs) and plant proteins (legumes, alt-protein, nuts/seeds). Subgroup required. |
| FG2   | Dairy and dairy alternatives | Animal dairy (cheese, other dairy) and plant-based dairy alternatives. Subgroup required. |
| FG3   | Fats and oils | Plant oils, plant spreads, animal fats. Subgroup required. |
| FG4   | Fruits and vegetables | Regular (non-starchy) vegetables and fruits. No subgroup. |
| FG5   | Grains and cereals | Whole grain and refined grain. `wwf_fg5_grain_kind` required. |
| FG6   | Starchy vegetables and tubers | Energy-dense root vegetables distinct from FG4: potato, sweet potato, cassava, yam, parsnip, plantain. No subgroup. |
| FG7   | Snacks and discretionary foods | Plant-based snacks and animal-based snacks. `wwf_fg7_snack_kind` required. |

Each canonical node carries:

- A stable id (e.g. `food.pulses.lentils`).
- A human-readable name.
- A default Protein Tracker group (one of the four).
- A default WWF food group, plus any applicable subgroup fields.
- An `excluded_from_wwf` flag for categories the WWF methodology
  excludes (alcoholic beverages, tea/coffee, herbs/spices, condiments,
  flavourings, supplements, baby formula/purees, stock/broth, novel
  proteins, salt).
- An `is_composite_likely` flag for categories whose products tend to
  be composite under WWF.
- Optional language hints used by the AI classifier (e.g. terms that
  signal this node in French or Spanish).

## File layout

```
packages/taxonomy/
  version.txt                 # the single-line semver
  CHANGELOG.md
  tree.yaml                   # the canonical tree
  retailer_mappings/
    <retailer_slug>.yaml      # per-retailer mappings, optional
```

`tree.yaml` is the authoritative source. A retailer mapping file maps
specific retailer strings to canonical node ids.

## Example excerpt of `tree.yaml`

```yaml
- id: food.pulses
  name: Pulses
  pt: plant_based_core
  wwf:
    food_group: FG1
    fg1_subgroup: legumes
  children:
    - id: food.pulses.lentils
      name: Lentils
    - id: food.pulses.beans
      name: Beans

- id: food.meat.beef
  name: Beef
  pt: animal_core
  wwf:
    food_group: FG1
    fg1_subgroup: red_meat

- id: food.dairy.cheese
  name: Cheese
  pt: animal_core
  wwf:
    food_group: FG2
    fg2_kind: dairy_animal
    fg2_dairy_class: cheese

- id: food.beverages.alcohol
  name: Alcoholic beverages
  pt: plant_based_non_core
  wwf:
    excluded_from_wwf: true

- id: food.ready_meals
  name: Ready meals
  # Heterogeneous category. No default; classification per product.
  is_composite_likely: true
```

## Default classification vs. per-product classification

A taxonomy default is a **hint**, not a verdict. The deterministic
rules engine consults the taxonomy node first; rules and AI
classification can still produce a different result if product-level
evidence supports it (e.g. "lentil crisps with bacon flavour" sits
under lentils in a retailer tree but is not a PT `plant_based_core`
item and is a WWF composite, not FG1).

The PT PDF explicitly authorises companies to manually reassign
individual deviating products inside an otherwise-uniform category;
the taxonomy implements this via deterministic rules and manual
review.

## Versioning

The taxonomy carries its own semantic version. A change to a default
on an existing node (PT group or WWF food group) is a major bump (it
changes prior comparable calculations). Adding new nodes is a minor
bump. Wording-only changes are a patch bump.

## Multi-language support

Node names and the `language_hints` field support multiple languages.
This lets the AI classifier see, for example, "carne de res" and
"viande de bœuf" as signals for `food.meat.beef` without leaking any
commercial data.

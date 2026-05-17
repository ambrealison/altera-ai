# Deterministic rules engine

The deterministic engine is a versioned, reproducible classifier. It runs
before the AI classifier. The engine is allowed to say "I don't know" by
producing a pass-through verdict; it is not allowed to produce a
low-confidence result.

## Rule format

Rules live as YAML under `apps/api/altera_api/rules/` and are loaded at
startup. Each rule has:

```yaml
- id: pt.pulses.lentils
  methodology: protein_tracker
  category: plant_core
  match:
    any_of:
      - product_name_contains: ["lentil", "lentils", "lenteja", "lentilles"]
      - taxonomy_node: food.pulses.lentils
  exclude:
    any_of:
      - labels_contains: ["with_meat", "with_bacon"]
      - product_name_contains: ["snack", "crisp", "chip"]
```

The fields:

- `id` — globally unique, stable across versions when the rule's intent
  is unchanged.
- `methodology` — `protein_tracker` or `wwf`. A rule belongs to exactly
  one methodology.
- `category` — the methodology-specific classification to assign on
  match. For PT: a `pt_group` value (`plant_based_core`,
  `plant_based_non_core`, `composite_products`, `animal_core`). For
  WWF: an object containing `wwf_food_group`, `wwf_is_composite`, and
  the food-group-specific sub-fields (e.g. `wwf_fg1_subgroup`,
  `wwf_fg2_kind`, `wwf_composite_step1_bucket`); see the WWF
  classifier output schema in [ai-classifier.md](ai-classifier.md).
- `match` — conditions that must hold for the rule to fire.
- `exclude` — conditions that, if any are true, prevent the rule from
  firing.

Conditions:

- `product_name_contains` — case-insensitive substring match, any of.
- `brand_in` — exact match against a list of brand strings.
- `taxonomy_node` — the product's resolved taxonomy node id, or a
  descendant of it.
- `labels_contains` — any of the listed labels is present.
- `language_in` / `country_in` — restrict the rule's scope.

`match` and `exclude` both support `any_of` and `all_of` nesting.

## Matching algorithm

For a given `(product, methodology)`:

1. **Contradiction check** — before any rules are evaluated, the engine
   calls `_detect_contradictions(ctx)`. If contradictions are found,
   a `PTContradiction` / `WWFContradiction` verdict is returned
   immediately; rule matching is skipped. See
   [Contradiction detection](#contradiction-detection) below.
2. Resolve the product's taxonomy node via the retailer mapping file and
   the canonical tree.
3. Iterate rules for the methodology, in priority order
   (lower-id-numbered files first, with a deterministic tiebreaker).
4. Compute `matches = product satisfies match` and
   `excluded = product satisfies exclude`.
5. If `matches and not excluded`, record a candidate rule.
6. After all rules are evaluated:
   - If zero candidates: pass-through (`source='deterministic'`,
     `category=None`).
   - If one candidate: assign that rule's category, `confidence=1.0`,
     `rule_id=<that rule's id>`.
   - If more than one candidate with the same category: assign that
     category, `rule_id=<concatenated, comma-joined ids>`.
   - If more than one candidate with conflicting categories: route to
     manual review with `reason='rule_collision'`.

This procedure is fully deterministic. The same inputs at the same rules
version always produce the same verdict.

## Contradiction detection

The engine runs a pre-check before rule matching to catch products whose
metadata is internally inconsistent or that clearly fall outside food
classification scope. A contradicted product bypasses the AI classifier
and goes directly to manual review with
`reason='contradiction_detected'`.

The pre-check covers four categories:

### Vegan label + animal ingredient

When a product carries a `vegan` label, the engine scans
`ingredients_text` for animal-derived ingredients: dairy (whole milk,
whey, casein, lactose, …), eggs (egg white, egg yolk, …), honey,
gelatine, lard, tallow, anchovies, fish sauce. One match is sufficient
to flag a contradiction.

### Vegan label + animal retailer category

When a product carries a `vegan` label and its `retailer_category`
contains a known animal keyword (`meat`, `poultry`, `fish`, `seafood`,
`charcuterie`, `deli meat`), a contradiction is flagged. Categories
that also contain `alternative`, `plant`, or `vegan` (e.g.
`Meat Alternatives`) are excluded from this check to avoid false
positives.

### Vegetarian label + meat/fish ingredient

When a product carries a `vegetarian` label, the engine checks for
meat, poultry, and fish ingredients: beef, pork, chicken, turkey, duck,
goose, veal, lamb, venison, rabbit, bacon, ham, salami, pepperoni,
chorizo, gelatine, lard, tallow, fish sauce, anchovies.

### Plant-based claim + whey/casein

When the product name contains `plant-based` / `plant based`, or a
`plant-based` / `plant_based` label is present, the engine checks for
`whey protein`, `whey`, `casein`, `caseinate`, `milk protein` in
ingredients.

### Out-of-scope signals

Products whose name or retailer category contains known non-food or
out-of-scope signals are flagged: pet food (dog/cat/bird/fish/hamster/
rabbit food, dog/cat treats), infant formula, nappies/diapers, laundry
products, dishwasher tablets, cigarettes and tobacco.

These products should not consume AI tokens and have no meaningful
PT/WWF classification.

## Rule files (v0.2.0)

| File | Methodology | Coverage |
|------|-------------|----------|
| `pt/001_animal_core.yaml` | PT | red meat, poultry, seafood, eggs, dairy, cheese, game, processed meats, whey protein |
| `pt/010_plant_core.yaml` | PT | legumes/pulses, tofu/tempeh/seitan, mycoprotein, nuts/seeds, plant supplements |
| `pt/020_plant_non_core.yaml` | PT | plant milks, plant yoghurts, plant cream, plant butter, plant cheese |
| `pt/030_composites.yaml` | PT | pizza, curries/stews, pasta dishes, pies/pastries, sandwiches/wraps, burgers, protein salads, protein soups, sushi/bowls, ready meals |
| `wwf/001_fg1_animal.yaml` | WWF | FG1: red meat, poultry, processed meats, seafood, eggs, meat alternatives |
| `wwf/002_fg1_plant.yaml` | WWF | FG1: legumes, alt proteins (tofu/tempeh/seitan/quorn), nuts and seeds |
| `wwf/010_fg2_dairy.yaml` | WWF | FG2: cheese, other dairy animal, dairy alternatives (plant) |
| `wwf/020_fg3_fats.yaml` | WWF | FG3: plant oils, plant spreads, animal fats |
| `wwf/030_fg4_fruit_veg.yaml` | WWF | FG4: vegetables, fruits (with plural forms) |
| `wwf/040_fg5_grains.yaml` | WWF | FG5: whole grain, refined grain |
| `wwf/050_fg6_starchy.yaml` | WWF | FG6: starchy vegetables and tubers (potato, sweet potato, cassava, yam, parsnip, plantain) |
| `wwf/070_fg7_snacks.yaml` | WWF | FG7: plant-based snacks, animal-based snacks |

All files include EN and FR bilingual keyword coverage.

## Rule versioning

The rules folder carries a single `VERSION` constant in
`rules/__init__.py`. A rule change is one of:

- **Major** — change a rule's `category`, or remove a rule whose id has
  ever fired in production.
- **Minor** — add a new rule, or refine `exclude` so a rule fires less
  often without redirecting matches to a different category.
- **Patch** — rename, comment, or restructure with no behavioural
  effect (verified by re-running the regression fixtures).

## Regression fixtures

Every rule must be accompanied by at least one fixture in
`tests/fixtures/rules/` showing an example product that should match it,
and one showing a near-miss that should not. When a rule changes, the
expected-match and expected-no-match files change with it, in the same
commit.

## What the engine never does

- It never consults sales or commercial data.
- It never produces a confidence between `0` and `1`. It is `1.0` on a
  match, undefined on pass-through.
- It never updates a `manual_review` source classification. Manual review
  is the final authority.

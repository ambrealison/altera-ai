# WWF Planet-Based Diets — classification rules (Altera)

This document is the **source of truth** for how Altera classifies
retailer products against the WWF Planet-Based Diets Retailer
Methodology (2024). It synthesises:

  - The repo's existing methodology notes (`docs/methodologies/wwf.md`).
  - The `WWF Category.csv` reference table (460 rows of canonical
    food categorisations from WWF).
  - The accompanying XLSX (`wwf-planet-based-diets_retailer-
    methodology_food-categorization-and-guidance.xlsx`):
      * Tab 1 — Instructions
      * Tab 2 — Key Definitions
      * Tab 3 — Product Exclusions
      * Tab 5 — Food Categorization List
      * Tab 7 — FAQs / challenging products

The classifier code (`altera_api/ai/batch_prompt.py:_WWF_SYSTEM`,
`altera_api/ai/batch_classifier.py:_coerce_wwf_result`, and the
future `altera_api/ai/wwf_guards.py`) implements the rules below.

> **Phase WWF-A/B/C scope**: this commit ships the methodology doc
> alongside the parser/prompt fix. The deterministic guards
> (`wwf_guards.py`) and quality-eval fixtures land in Phase WWF-D+.

## 1. Overall workflow

1. **Exclude methodology exclusions** first (`out_of_scope`).
2. **Allocate** each remaining product as a **whole product** or a
   **composite product**.
3. **Whole product** → classify to one of FG1–FG7 and pick its
   required subgroup.
4. **Composite product** → classify at product level into one of
   four Step 1 buckets (meat_based / seafood_based / vegetarian /
   vegan) unless ingredient-level data is available.
5. Whole products and composite products are **reported
   separately** in the WWF diet-shift dashboard.

The classification unit is the **product as sold by weight**
(kilograms of product sold). It is NOT protein grams (that's Altera's
Protein Tracker methodology). Do not apply PT 50/50 plant/animal
defaults during WWF classification.

## 2. Composite vs whole product

The WWF methodology defines a composite product as one with
**multiple main ingredients from more than one food group**,
typically ready-to-eat or with minimal preparation. The ingredients
are usually visibly distinguishable in the product.

**Processed product ≠ composite product.** Bread, pasta, cheese,
parmesan, smoked salmon, breaded calamari, sausages — these are all
*whole products* under their respective food group, even though
they are processed.

Examples (whole, not composite):

  - Spaghetti → FG5 refined_grain.
  - Whole-wheat bread → FG5 whole_grain.
  - Parmesan → FG2 dairy_animal cheese.
  - Smoked salmon → FG1 seafood.
  - Sausages → FG1 processed_meats_alternatives.
  - Breaded calamari → FG1 seafood.

Examples (composite):

  - Pizza, lasagne, sandwich, salade composée, quiche, sushi roll,
    pasta carbonara, gnocchi alla sorrentina, kebab, gyoza, calzone,
    flammkuchen, ready-meal bowl, wrap, vegetables-with-cream-or-
    butter, gratin, trail mix.

## 3. Composite Step 1 bucket rules

Bucket precedence (apply in order):

  1. Contains **any meat** (red meat, poultry, processed meats,
     game) → `meat_based`.
  2. Contains **fish or seafood and no meat** → `seafood_based`.
  3. Contains **eggs and/or dairy but no meat or seafood** →
     `vegetarian`.
  4. Contains no meat, seafood, eggs, or dairy → `vegan`.

Even **marginal amounts** of animal-based ingredients count at the
product level. A vegan salad with a single anchovy on top is
`seafood_based`. A vegetarian quiche with a single chicken slice is
`meat_based`.

## 4. Out-of-scope (methodology exclusions)

From the XLSX Tab 3, the following are `out_of_scope`:

  - **Beverages other than dairy / dairy alternatives**: water,
    soda, juice, smoothies, alcohol, tea, coffee, cocoa drinks —
    including milk-powder-containing varieties.
  - **Condiments and sauces**: ketchup, barbecue sauce, salad
    dressings, mayonnaise, mustard, vinegar, vinaigrette.
  - **Herbs, spices, flavourings, additives, salt**.
  - **Vitamins and supplements**.
  - **Baby formula and baby purees**.
  - **Stock cubes, broth, bouillon (liquid or powder)**.
  - **Culinary ingredients**: baking powder, natron, starch, locust
    bean gum.
  - **Novel / unusual proteins**: insects, precision-fermentation
    proteins, cultured meats, microalgae-based proteins.

This out-of-scope list **differs from Protein Tracker**. Do not
reuse PT scope rules wholesale. In particular, condiments (ketchup,
mustard, vinaigrette) are out_of_scope in WWF but kept as
`plant_based_non_core` in PT.

## 5. Food categorization list (XLSX Tab 5 summary)

The XLSX Tab 5 + `WWF Category.csv` provide 460 canonical entries.
Summary of how they map to the WWF AI contract enums:

### FG1 — Protein sources (whole products)

  - `red_meat` — beef, pork, lamb, veal, goat, mutton, ostrich,
    moose, hare, partridge, pheasant, quail, rabbit, venison, wild
    boar.
  - `poultry` — chicken, turkey, duck, goose, snails (yes, snails
    are listed under poultry in the CSV).
  - `processed_meats_alternatives` — bacon, jerky, burger patty,
    corned beef, gammon, ham, jamon iberico, koftas, mortadella,
    patè, prosciutto, sausages (bratwurst, chorizo, frankfurter,
    mettwurst, saucisson sec, black pudding), salami and continental
    cured meats, smoked meats, spam, turkey bacon, jambon, lardons.
  - `seafood` — fresh-water and salt-water fish (basa, cod, herring,
    mackerel, sardines, salmon, sea bass, snapper, tuna, trout,
    anchovy), shellfish (crab, lobster, prawns, shrimp, scallops,
    razor clams, mussels, oysters, clams, octopus, squid, scampi,
    whelks, periwinkles, crayfish, cuttlefish), canned/marinated/
    smoked/breaded fish, caviar, surstromming, kippers.
  - `eggs` — whole eggs, liquid eggs, gelatine (sheet/leaf/block).
  - `legumes` — aduki, black-eyed, black turtle, broad, borlotti,
    butter, cannellini, chickpeas, dried/split peas, fava, flageolet,
    green/garden peas, haricot, kidney, lentils (green, red,
    yellow), marrowfat/mushy peas, mung, navy, pinto, soya/edamame,
    wax, lupin, cowpeas, urad, bambara groundnut, jackfruit (yes,
    jackfruit is FG1 legumes per the CSV — it's used as a meat
    alternative).
  - `nuts_seeds` — almonds, brazil nuts, cashews, chestnuts, chia,
    flax, hazelnuts, hemp, linseeds, macadamias, nigella, peanuts,
    pecans, pistachios, pine nuts, poppy, pumpkin, sesame, sunflower,
    walnuts, nut butters (peanut butter, almond butter), seed pastes
    (tahini). **Coconut flesh** is also FG1 nuts_seeds (the CSV
    explicitly lists it). Coconut MILK is FG2 dairy_alternative_plant.
  - `alternative_protein_sources` — tofu, tempeh, seitan,
    mycoprotein, soy/pea/wheat protein isolate, okara, falafel,
    houmous, soya, plant-based mince. Pasta MADE FROM legumes
    (chickpea pasta, lentil pasta) is FG1 alternative_protein_sources.
  - `meat_egg_seafood_alternatives` — plant-based meat / egg /
    seafood alternative products: vegetable burger, vegetable
    sausage, vegan nuggets, plant-based fish alternative, egg
    replacer.

### FG2 — Dairy and dairy alternatives

  - `dairy_animal` with `wwf_fg2_dairy_class=cheese`: hard, soft,
    fresh, aged, blue, cottage, grilled cheese (halloumi),
    mozzarella sticks, parmesan, brie, camembert, gouda, ricotta,
    burrata, feta.
  - `dairy_animal` with `wwf_fg2_dairy_class=other`: buttermilk,
    single/double/clotted cream, crème fraîche, curd, dairy-based
    dips, evaporated milk, cow's/goat's/sheep's/buffalo milk,
    milkshakes, protein drink, protein powders, sour cream, yoghurt
    (incl. flavoured), kefir, coffee creamer, skyr, quark.
  - `dairy_alternative_plant`: oat milk, soy milk, almond milk,
    cashew milk, hazelnut milk, hemp milk, pea milk, rice milk,
    potato milk, COCONUT MILK, plant cheese, soy-based cheese,
    starch-based cheese substitutes, plant-based protein drinks,
    plant-based yoghurt alternatives, plant-based kefir, plant-based
    coffee creamers, vegan cream cheese spreads, vegan cream / crème
    fraîche, vegan sour cream, plant-based protein powders.
  - **Butter is NOT FG2** — see FG3 below.

### FG3 — Fats and oils

  - `animal_based_fat`: butter (salted, unsalted, clarified), ghee,
    duck fat, goose fat, lard, tallow, blends of animal fats.
  - `plant_based_fat`: avocado, coconut, grapeseed, flaxseed, hemp,
    olive, palm, peanut, sesame, canola, sunflower, rapeseed oil;
    margarine (regular and low-fat); plant-based butter substitutes
    (soy-based, almond-based).

### FG4 — Fruits and vegetables (no subgroup)

  - All fresh / frozen / canned / dried / fermented / marinated
    fruits and vegetables.
  - Sweetcorn, baby corn, corn-on-the-cob → FG4 (immature corn).
  - Mature/dry corn → FG5 (corn flour, polenta, cornflakes).
  - Pears in rum / fruit preserved in syrup, alcohol or water → FG4
    (primary ingredient is fruit). *Source typo note: the XLSX Tab
    7 row 49 says "FG3 (Fruits & Vegetables)" but FG3 in the food-
    group table is Fats & Oils. The methodology team confirmed the
    intent is FG4. We classify per intent.*
  - Mushrooms, root vegetables (carrots, radishes, beetroot, turnips,
    parsnips), leafy greens, mixed fruit salads, vegetable sticks,
    citrus segments, sugar snap peas, stir-fry vegetable mixes.
  - Tomato purée, sauce, and passata are FG4 (unprocessed tomato
    output without added animal/dairy/cereal).
  - **Green/garden peas and broad/fava beans are FG1 legumes**, not
    FG4, even though they look like vegetables. The CSV is explicit.
  - **Mung bean sprouts in cans** are FG1 legumes.

### FG5 — Grains and cereals

  - `whole_grain`: barley (hulled / whole), bulgur, couscous (incl.
    wholegrain), farro, oats (rolled, steel-cut), oatmeal, quinoa,
    rye berries, spelt grains, wheat berries, whole wheat, whole
    bran flakes, muesli with whole rolled oats, wholemeal pasta,
    multi-grain bread, sliced wholegrain bread.
  - `refined_grain`: refined flour, refined pasta (spaghetti, penne,
    macaroni unless wholegrain), white rice (white basmati, jasmine,
    sushi rice), tortillas, noodles (udon, soba, rice, vermicelli,
    glass), corn flour, polenta, semolina, bran flakes, cornflakes,
    plain crackers, rice cakes without coatings, pizza/pasta dough
    (incl. wholegrain only when explicit).
  - Default to `refined_grain` if the name has no wholegrain signal.
  - Mature/dry corn (cornflakes, polenta, corn flour) → FG5
    refined_grain unless explicitly wholegrain.

### FG6 — Tubers and starchy vegetables (no subgroup)

  - Cassava, potatoes (russet, white, purple), sweet potatoes, lotus
    root, taro, yams.
  - **Fries / chips / wedges / Pommes Duchesse / Pommes Noisette /
    unfilled croquettes / rösti classic are FG7**, not FG6 — cooked
    with added fat/salt/sugar.

### FG7 — Snacks high in added fats / salt / sugar

The CSV lists FG7 as "Animal-based OR plant-based" because chocolate,
ice cream, cakes, and similar snacks can be either. The AI contract
asks for an explicit `wwf_fg7_kind`:

  - `animal_based_snack`: chocolate (any milk/butter/cream/honey/
    gelatine implied), ice cream (all flavours), gelato, frozen
    yoghurt, mousse, panna cotta, custards, pudding, sweet pastries
    that contain butter/milk/egg (croissant, pain au chocolat,
    brioche, doughnuts, muffins, cakes, cupcakes, sweet tarts,
    cinnamon rolls, cardamom buns, strudel, éclair, baklava,
    cannoli, pies, pancakes, crêpes, waffles, marshmallows), dulce
    de leche / caramel spread, sweetened nut-cocoa spread (e.g.
    Nutella), ice cream sandwiches.
  - `plant_based_snack`: potato chips/crisps, corn chips, tortilla
    chips, vegetable chips, pretzels, popcorn, sorbet, French fries
    and potato wedges/sides, snack mixes without nuts (crackers +
    pretzels), snack sticks, jam, jelly, sweetened nut-cocoa spread
    if explicitly vegan, honey/maple syrup/agave syrup, preserves,
    fruit compote with added sugar, fruit sauce/curd with added
    sugar, gummy bears, hard sweets, toffees, lollipops, gums &
    jellies, licorice, marzipan, sugary granola / cereals with
    added sugar, plant-based brownies/cookies.
  - **Honey, maple syrup, agave syrup, sugar, jam, jelly** are all
    FG7 (added-sugar items per XLSX Tab 5). Honey is `plant_based_
    snack` here because the bee-honey/animal classification is
    debatable; the CSV column reads "Animal-based or plant-based"
    so either is defensible. We default to `plant_based_snack`
    pending a methodology-team confirmation.

## 6. Key FAQ / challenging-product rules (XLSX Tab 7)

  - **Novel proteins** (insects, cultured meats, precision
    fermentation, microalgae) are out_of_scope.
  - **Smoothies and juice** are out_of_scope (beverages).
  - **All beverages other than dairy / dairy alternatives** are
    out_of_scope.
  - **Corn**: mature/dry corn → FG5 (cornflakes, polenta, corn flour);
    sweetcorn / baby corn / corn-on-the-cob → FG4.
  - **Coconut**: flesh → FG1 nuts_seeds; milk → FG2
    dairy_alternative_plant.
  - **Root vegetables**: carrots, radishes, beetroot, parsnips →
    FG4. Only true tubers (potatoes, sweet potatoes, cassava, taro,
    yam, lotus root) → FG6.
  - **Protein beverages**:
      * If beverage contains dairy or a dairy alternative → in
        scope under FG2.
      * Protein drinks/meals containing a mixture of dairy and
        plant-based proteins → composite.
  - **Butter** → FG3 animal_based_fat, NOT FG2. No dairy-equivalent
    factor applies to butter.
  - **Applesauce with added sugar** → FG7 (sweetened spread).
    Unsweetened applesauce → FG4.
  - **Pears in rum / fruit preserved in syrup, alcohol or water**
    → FG4 (primary ingredient is fruit). *Source typo: XLSX says
    "FG3 (Fruits & Vegetables)" but this is FG4 in the food-group
    table; implement FG4 per intent.*
  - **Spinach with cream / vegetables cooked in butter** →
    composite.
  - **Green / garden peas and broad / fava beans** → FG1 legumes,
    not FG4.
  - **Trail mixes** → composite.
  - **Mung bean sprouts in cans** → FG1 legumes.
  - **Ready-to-eat tuna salads in cans** → composite.
  - **Liver / crackling dumplings** → composite.
  - **Spreads based on margarine with egg / curd cheese / potato**
    → composite.
  - **Cheese in oil / cheese-stuffed peppers** → composite.
  - **Fries** → FG7. **Crisps** → FG7. **Sugary granola / cereals**
    → FG7.
  - **Parmesan** → FG2 dairy_animal cheese (it's a hard cheese).
  - **Herbs / spices** → out_of_scope.
  - **Quark, skyr, cottage cheese**: the CSV places these in FG2
    dairy_animal under "other" (not cheese).

## 7. Altera implementation decisions (not in WWF source)

The WWF Planet-Based Diets retailer methodology does not explicitly
address every product category Altera ingests. Where the source is
silent or ambiguous, we apply the rules below as a documented Altera
decision.

### Pet food

**Pet food is IN-SCOPE food** in the Altera app because it is food
sold by the retailer (and counts toward retailer SKU volume). The
WWF human-diet methodology doesn't explicitly cover pet food in
the XLSX, so this is an Altera implementation decision.

  - Croquettes / pâtée / friandises (cat or dog) → in-scope.
  - Litière, jouet chien/chat, harnais, sacs déjections,
    accessoires animaux → `out_of_scope` (pet accessories, not
    food).
  - **Generic croquettes/pâtée** with no further detail → composite
    (cereal + animal protein mix is the dominant case), low
    confidence + needs_review.
  - **Croquettes chat saumon / pâtée chien bœuf** → either FG1
    seafood/red_meat/poultry (low confidence) when treated as a
    near-whole animal-protein product, OR composite with bucket
    meat_based / seafood_based when ingredients are visibly mixed.
    The classifier defaults to composite + bucket per Step 1 rules
    with low confidence + needs_review.
  - Vegan pet food → plant-based whole or composite per ingredients.
  - **Never `unknown`** for readable pet food names.

### Readable-name fallback

If a readable product name doesn't match any specific rule, the
classifier emits a best-guess category at confidence ≤ 0.69 +
`needs_review=true` rather than `unknown`. `unknown` is reserved
for genuinely unusable names (empty / placeholder / corrupted).
This mirrors the PT behaviour shipped in Phase 36K/K2.

## 8. AI contract (Phase WWF-A onward)

The WWF AI batch prompt asks the model to return the full schema:

```json
{
  "id": "<the id>",
  "wwf_food_group": "FG1|FG2|FG3|FG4|FG5|FG6|FG7|out_of_scope|unknown",
  "wwf_is_composite": true|false,
  "wwf_fg1_subgroup": "<required for FG1, else null>",
  "wwf_fg2_kind": "<required for FG2, else null>",
  "wwf_fg2_dairy_class": "<required when wwf_fg2_kind=dairy_animal, else null>",
  "wwf_fg3_kind": "<required for FG3, else null>",
  "wwf_fg5_grain_kind": "<required for FG5, else null>",
  "wwf_fg7_kind": "<required for FG7, else null>",
  "wwf_composite_step1_bucket": "<required when wwf_is_composite=true, else null>",
  "confidence": 0.0,
  "rationale": "<one short sentence>"
}
```

Pydantic schema (`altera_api/ai/result_schema.py::WWFClassifierResult`)
enforces all required-when / forbidden-when rules. The parser
(`_coerce_wwf_result`) accepts tolerant aliases for each subgroup
field (`fish` → `seafood`, `beef` → `red_meat`, `chicken` →
`poultry`, etc.) so a slightly-off model response still produces a
valid classification.

Prompt version: `batch_wwf_v2` (independent from the PT prompt
version `batch_classifier_v5`).

## 9. Performance & privacy invariants (unchanged from PT)

  - No per-product OpenAI calls — WWF runs through the same
    batched orchestrator as PT.
  - No N+1 in the validation table — the existing Phase 36F-lite
    bulk path works for both methodologies.
  - 10K rows in < 1 h target with `ALTERA_AI_CLASSIFICATION_BATCH_SIZE=40`.
  - The AI prompt payload still goes through
    `assert_payload_allowed` — commercial fields (sales volume,
    price, margin) never reach the model.

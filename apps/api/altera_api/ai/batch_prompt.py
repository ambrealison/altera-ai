"""Phase 34F — Batched AI classification prompts.

The single-product prompt path (``prompt_builder.py`` + ``classifier.py``)
makes one OpenAI call per product, which:

* does not scale beyond ~hundreds of products (1 product = 1 HTTP RTT);
* gives the model too little context to anchor on (no examples in the
  user message, no JSON-mode); and
* produces a high parse-failure rate at gpt-4o-mini's quality tier.

The batched path here packs N products (default 50) into one call and
asks the model to return an array of results. We use JSON mode so the
response is guaranteed-valid JSON, and we include concrete French
examples in the system message so the model recognises the typical
retailer naming patterns.

Privacy rules are unchanged: the per-product payload is still validated
by ``assert_payload_allowed`` before any bytes leave the process. Only
fields in ``ALLOWED_PROMPT_FIELDS`` are sent — commercial fields
(volume, weight, items_purchased, prices, margins, etc.) never appear
in the batch payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.common import Methodology

#: Bumped when the prompt body, instructions, or examples change in a
#: way that should invalidate stored AI provenance / calibration.
#:
#: Phase 34Q v2 — high-coverage food classifier philosophy.
#: Phase 34T v3 — corrected the plant_based_core / plant_based_non_core
#: split.
#: Phase 34U v4 — explicit COMPOSITE coverage of biscuits/cakes/ice
#: cream containing butter/egg/milk + explicit OUT_OF_SCOPE rule for
#: water/soda/coffee/tea.
#: Phase 34V v5 — final edge-case sweep from the 91%-correct test:
#:   - Hygiene / cleaning / pet-food MUST be out_of_scope, not unknown.
#:   - Honey / pure sugar / pure flavourings → out_of_scope (no
#:     meaningful protein).
#:   - "Blinis Moelleux", "Épinards Crème", "Baguettes Viennoises",
#:     bakery products likely containing milk / butter / egg →
#:     composite (the model previously sent these to non_core).
#:   - unknown ONLY for truly unusable names (empty, "Divers",
#:     "Produit 123"). NEVER for non-food (which is out_of_scope).
BATCH_CLASSIFIER_PROMPT_VERSION = "batch_classifier_v5"

#: Phase WWF-A — separate WWF prompt version so bumping the WWF
#: contract doesn't invalidate PT calibration samples (and
#: vice-versa). The PT prompt above stays on ``batch_classifier_v5``.
BATCH_WWF_PROMPT_VERSION = "batch_wwf_v2"

#: Default batch size. Chosen so a typical batch fits comfortably under
#: gpt-4o-mini's context and leaves >2k tokens for the response.
#:
#: Phase 34P — reduced 50 → 25 after a 100-row test showed envelope
#: truncation at batch sizes ≥ 40: the model occasionally hits its
#: max_completion_tokens cap mid-row, producing a half-written object
#: that the tolerant parser cannot recover. Smaller batches mean shorter
#: responses and therefore fewer truncation failures; the trade-off is
#: roughly 2x the number of API round-trips, which is acceptable for a
#: hotfix.
DEFAULT_BATCH_SIZE = 25

#: Phase 34P — even smaller batches for the retry pass. When a batch
#: produces parse failures or missing ids, we re-batch just the failing
#: rows at this size. Tiny batches almost always succeed because the
#: model has enough completion budget for short, well-formed JSON.
RETRY_BATCH_SIZE = 5

_PT_SYSTEM = """\
You classify grocery retailer food products for a French/European
retailer-analytics platform. Almost every row in the input is a food
product and should be considered IN-SCOPE. Choose the best Protein
Tracker category for each row.

CRITICAL RULES — read before classifying:
- Do not use `out_of_scope` unless the product is CLEARLY non-food
  (detergent, batteries, toys, household goods, pet food, hygiene).
- Do not use `unknown` just because you are uncertain. Choose the
  most likely food category and express uncertainty via `confidence`.
- A product can be categorized AND need review at the same time.
  Express that by lowering `confidence`; the system handles review.
- Plant-based meat alternatives ("poulet végétal", "burger végétal",
  "nuggets végétaux façon poulet") are PLANT products even if the
  name contains "poulet" / "boeuf" / "bacon". They are NEVER
  animal_core.

PROTEIN TRACKER TAXONOMY (Phase 34T — strict):

1. `animal_core` — animal proteins are majority or exclusive.
   Includes: meat, poultry, fish, seafood, eggs, milk, cheese,
   yoghurt, butter, cream. French: viande, poulet, bœuf, porc,
   poisson, saumon, thon, œufs, lait, fromage, yaourt, beurre, crème.

2. `plant_based_core` — PROTEIN-RELEVANT plant products ONLY:
   - legumes / pulses (lentilles, pois chiches, haricots, fèves)
   - nuts and seeds (amandes, noix, cajou, graines)
   - tofu / tempeh / seitan
   - plant-based substitutes for meat / milk / eggs (steak végétal,
     burger végétal, vegan cheese, oat/soy/almond milk if treated
     as a dairy substitute)
   This category is NARROW. Do NOT put bread, rice, pasta, fruits,
   vegetables, oils, juices, snacks, or biscuits here.

3. `plant_based_non_core` — other plant foods and plant-derived
   products:
   - bread, rice, pasta, flour, semolina, breakfast cereals
   - fruits and vegetables (pommes, carottes, salade, courgette)
   - potatoes and other starches
   - oils, margarines, plant sauces
   - juices, smoothies, plant drinks NOT treated as milk substitutes
   - chips, biscuits, sweets WITHOUT significant animal protein
   IMPORTANT: this is where most ordinary retailer rows belong.
   Bread / rice / pasta / fruits / vegetables / oils ARE plant-
   based but they are NOT core; they are non_core.

4. `composite_products` — animal AND plant proteins both present
   in the recipe. Common cases:
   - pizza, quiche, lasagne, sandwich poulet, salade poulet césar,
     soupe poulet et légumes, burger végétal & emmental, gratin
   - breaded meat / breaded fish (pané) — cereal coating around
     an animal protein
   - vegetable products coated in cheese / dairy / egg
   - BISCUITS, CAKES, PASTRIES containing butter, eggs, or milk
     (biscuit au beurre, gâteau aux œufs, brioche au beurre,
     pâtisserie au beurre, viennoiserie)
   - ICE CREAM and CREAM-BASED products (glace à la crème,
     crème brûlée, mousse au chocolat)
   - prepared meals with animal + plant ingredients

5. `out_of_scope` — non-human-food OR beverages with negligible
   protein contribution:
   - NON-FOOD: detergent (lessive), toothpaste (dentifrice), paper
     (papier toilette), pet food (croquettes), hygiene (shampooing,
     savon), household, toys, batteries.
   - BEVERAGES with no significant protein under current Protein
     Tracker rules: water (eau), soda, coffee (café), tea (thé),
     alcohol (vin, bière, spiritueux), sport drinks, energy drinks.
   - PURE FLAVOURINGS: salt (sel), sugar (sucre), spices (épices),
     plain vinegar.

   IMPORTANT: do NOT classify coffee, tea, water, or soda as
   plant_based_core or plant_based_non_core. They are out_of_scope
   under the current methodology because their protein contribution
   is negligible.

6. `unknown` — ONLY when the product name is unusable:
   empty/missing product_name, "Produit 123", "Divers", an SKU
   code with no description, unreadable text. If the name is
   interpretable, pick the best category and lower confidence.

   IMPORTANT (Phase 34V): obvious non-food products (Papier
   Toilette, Couches bébé, Dentifrice, Lessive, Croquettes Chat,
   Pâtée Chien, Shampooing, Essuie-Tout, Nettoyant Multi-Usages)
   MUST be ``out_of_scope``, NEVER ``unknown``. The product name
   IS interpretable — it just isn't human food.

   Pure flavourings with no meaningful protein (Miel de Fleurs,
   Sucre, Sel, Poivre, Vinaigre seul) are also ``out_of_scope``,
   not ``unknown``.

   Bakery products likely containing milk / butter / egg
   (Blinis Moelleux, Baguettes Viennoises, Pain au Lait,
   Brioches industrielles) are ``composite_products`` even if
   the recipe isn't spelled out — most retailer viennoiseries
   carry dairy / egg / butter and the safe default is composite.

   Vegetable products served in cream / butter / cheese sauce
   (Épinards à la Crème, Gratin Dauphinois) are
   ``composite_products`` because they carry dairy protein.

Confidence calibration:
- 0.90–0.99 — obvious foods: "Pommes Golden", "Carottes",
  "Tofu nature", "Blanc de Poulet", "Lait demi-écrémé",
  "Pâtes", "Riz Basmati".
- 0.75–0.89 — clear processed: "Chips nature", "Chocolat au lait",
  "Yaourt nature", "Boisson avoine", "Huile d'olive",
  "Pain complet".
- 0.60–0.74 — mixed/processed but classifiable: lasagnes, soupes,
  salades composées, plats préparés, burgers, pizzas.
- 0.40–0.59 — category proposed; please review.
- below 0.40 — only when genuinely unclear. STILL pick the most
  likely category; do NOT fall back to unknown.

Concrete examples (Phase 34T canonical set):

animal_core:
- "Blanc de Poulet Rôti Tranché" → animal_core (0.98)
- "Filets de Saumon Atlantique" → animal_core (0.97)
- "Côte de Bœuf Maturée" → animal_core (0.98)
- "Jambon Blanc Supérieur" → animal_core (0.95)
- "Oeufs Plein Air x12" → animal_core (0.97)
- "Lait Demi-Écrémé UHT" → animal_core (0.97)
- "Yaourt Nature" → animal_core (0.95)
- "Fromage Blanc" → animal_core (0.95)
- "Beurre Doux" → animal_core (0.95)
- "Camembert au Lait Cru" → animal_core (0.96)

plant_based_core (NARROW — protein-relevant plants only):
- "Tofu Nature Bio" → plant_based_core (0.97)
- "Pois Chiches Cuits en Conserve" → plant_based_core (0.97)
- "Lentilles Vertes du Puy IGP" → plant_based_core (0.97)
- "Haricots Rouges" → plant_based_core (0.95)
- "Steak Végétal Soja & Blé" → plant_based_core (0.92)
- "Burger Végétal au Pois" → plant_based_core (0.90)
- "Poulet Végétal" → plant_based_core (0.92)
- "Nuggets Végétaux Façon Poulet" → plant_based_core (0.90)
- "Boisson Avoine Bio" → plant_based_core (0.85)  # milk substitute
- "Lait d'Amande" → plant_based_core (0.88)  # milk substitute
- "Noix de Cajou" → plant_based_core (0.93)
- "Amandes Entières" → plant_based_core (0.93)
- "Tempeh Bio" → plant_based_core (0.95)

plant_based_non_core (CRITICAL — bread/rice/pasta/fruits/veg/oils):
- "Pommes Golden 1.5kg" → plant_based_non_core (0.95)
- "Carottes Sachet 1kg" → plant_based_non_core (0.95)
- "Pommes de Terre" → plant_based_non_core (0.94)
- "Tomates Cerises" → plant_based_non_core (0.95)
- "Pâtes Spaghetti" → plant_based_non_core (0.94)
- "Riz Basmati" → plant_based_non_core (0.94)
- "Pain de Mie" → plant_based_non_core (0.92)
- "Pain Complet Bio" → plant_based_non_core (0.90)
- "Huile d'Olive Vierge Extra" → plant_based_non_core (0.93)
- "Chips Nature" → plant_based_non_core (0.88)
- "Jus d'Orange Pressé" → plant_based_non_core (0.92)
- "Biscuits Sablés" → plant_based_non_core (0.80)
- "Farine de Blé T55" → plant_based_non_core (0.92)
- "Sauce Tomate" → plant_based_non_core (0.85)
- "Margarine Végétale" → plant_based_non_core (0.88)

composite_products (animal + plant in same recipe):
- "Pizza Royale Jambon Champignons" → composite_products (0.94)
- "Lasagnes Bolognaise" → composite_products (0.93)
- "Quiche Lorraine" → composite_products (0.94)
- "Sandwich Poulet Crudités" → composite_products (0.92)
- "Burger Végétal & Emmental" → composite_products (0.88)
- "Salade Poulet César" → composite_products (0.92)
- "Soupe Poulet et Légumes" → composite_products (0.88)
- "Gratin Dauphinois" → composite_products (0.85)
- "Cordon Bleu Volaille Pané" → composite_products (0.90)  # breaded
- "Filet de Poisson Pané" → composite_products (0.90)
- # Biscuits / cakes / pastries WITH dairy or egg → composite:
- "Biscuits au Beurre" → composite_products (0.85)
- "Madeleine au Beurre" → composite_products (0.85)
- "Gâteau au Beurre et Oeufs" → composite_products (0.88)
- "Brioche au Beurre" → composite_products (0.88)
- "Pain au Chocolat" → composite_products (0.82)  # butter + dough
- "Croissant au Beurre" → composite_products (0.85)
- # Ice cream / cream-based desserts → composite:
- "Glace à la Vanille" → composite_products (0.88)  # dairy
- "Crème Brûlée" → composite_products (0.92)
- "Mousse au Chocolat" → composite_products (0.85)
- # Phase 34V — bakery products likely w/ milk/butter/egg → composite:
- "Blinis Moelleux" → composite_products (0.80)
- "Baguettes Viennoises" → composite_products (0.78)
- "Pain au Lait" → composite_products (0.85)
- "Brioche Tranchée" → composite_products (0.85)
- # Vegetables in cream/butter sauce → composite:
- "Épinards à la Crème" → composite_products (0.88)
- "Épinards Branches Crème" → composite_products (0.85)
- "Purée de Pommes de Terre au Lait" → composite_products (0.82)

out_of_scope (non-food, hygiene, pet food, beverages w/o protein):
- "Lessive Liquide 3L" → out_of_scope (0.99)
- "Dentifrice Menthe" → out_of_scope (0.99)
- "Papier Toilette" → out_of_scope (0.99)
- "Essuie-Tout" → out_of_scope (0.99)
- "Couches Bébé Taille 4" → out_of_scope (0.99)
- "Croquettes pour Chien" → out_of_scope (0.97)
- "Croquettes pour Chat" → out_of_scope (0.97)
- "Pâtée Chien" → out_of_scope (0.97)
- "Pâtée Chat" → out_of_scope (0.97)
- "Shampooing Doux" → out_of_scope (0.99)
- "Savon de Marseille" → out_of_scope (0.99)
- "Nettoyant Multi-Usages" → out_of_scope (0.99)
- "Lingettes Désinfectantes" → out_of_scope (0.99)
- # Pure flavourings (Phase 34V — these were going to unknown):
- "Miel de Fleurs" → out_of_scope (0.92)  # negligible protein
- "Confiture de Fraises" → out_of_scope (0.88)
- # Beverages with negligible protein:
- "Eau Minérale Naturelle 1.5L" → out_of_scope (0.97)
- "Coca-Cola 1.5L" → out_of_scope (0.96)  # soda
- "Limonade" → out_of_scope (0.95)
- "Café Moulu Arabica" → out_of_scope (0.95)
- "Thé Vert" → out_of_scope (0.95)
- "Vin Rouge Bordeaux" → out_of_scope (0.96)
- "Bière Blonde" → out_of_scope (0.96)
- # Pure flavourings:
- "Sel Fin de Guérande" → out_of_scope (0.96)
- "Sucre en Poudre" → out_of_scope (0.94)
- "Poivre Noir" → out_of_scope (0.95)

unknown (ALMOST NEVER — only when name is unusable):
- "Produit 123" → unknown (0.10)
- "Divers" → unknown (0.10)
- "" → unknown (0.05)

Response format — RETURN EXACTLY this JSON object, nothing else. Every
field MUST be separated by a comma. Every string MUST be quoted:
{
  "results": [
    {"id":"p1","pt_group":"plant_based_core","confidence":0.95,"rationale":"fruit"},
    {"id":"p2","pt_group":"animal_core","confidence":0.96,"rationale":"chicken"}
  ]
}

Rules:
- Every input id MUST appear exactly once in `results`.
- `pt_group` MUST be one of the allowed values, lower-snake-case.
- `rationale` MUST be at most 8 words. Examples: "vegetable product",
  "chicken product", "plant-based meat alternative", "mixed animal
  and plant", "ready meal".
- DO NOT add fields beyond {id, pt_group, confidence, rationale}.
- DO NOT wrap the JSON in markdown fences or prose.
- ALL field separators MUST be commas. Output must be valid JSON
  parseable by `JSON.parse`.
"""

_WWF_SYSTEM = """\
You classify grocery retailer food products into the WWF Planet-Based
Diets food groups. The unit of analysis is the product as sold by
weight (not protein grams). Almost every row is food and should be
IN-SCOPE.

CRITICAL RULES:
- Choose the best food group; do not use `out_of_scope` / `unknown`
  as a fallback for uncertainty. Reserve `out_of_scope` for
  methodology exclusions or items that are clearly non-food.
- Whole products get a food group AND its required subgroup.
- Composite (multi-ingredient ready-to-eat meals: pizza, lasagne,
  sandwich, salade composée…) set `wwf_is_composite: true` AND
  `wwf_composite_step1_bucket`. Processed bread / cheese / pasta
  is NOT a composite — those are whole products in FG5 / FG2 /
  FG5.
- Do NOT apply Protein Tracker 50/50 plant/animal defaults — WWF
  is not Protein Tracker.

WWF food groups & required subgroups:

- FG1 — Protein sources. Required `wwf_fg1_subgroup`:
    * red_meat       — beef, pork, lamb, veal, goat, mutton, game.
    * poultry        — chicken, turkey, duck, goose.
    * processed_meats_alternatives — ham, bacon, salami, sausage,
                      chorizo, jambon, lardons, jerky.
    * seafood        — all fish (saumon, thon, sardines, cabillaud)
                      and shellfish (crevettes, moules, huîtres).
    * eggs           — whole eggs, liquid egg, omelette base.
    * legumes        — lentilles, pois chiches, haricots, pois,
                      mung beans, edamame, fava beans, lupin.
                      Green/garden peas and broad beans ARE legumes.
    * nuts_seeds     — amandes, noix, cajou, pistache, graines de
                      chia/lin/tournesol/courge/sésame, tahini, nut
                      butter, coconut flesh.
    * alternative_protein_sources — tofu, tempeh, seitan,
                      mycoprotein, pea/soy/wheat protein isolate.
    * meat_egg_seafood_alternatives — plant-based burger / nuggets /
                      sausage / mince / fish-alternative / egg
                      alternative.

- FG2 — Dairy and dairy alternatives. Required `wwf_fg2_kind`:
    * dairy_animal           — milk, yoghurt, kefir, cream, cottage
                              cheese, fromage, parmesan, mozzarella,
                              buttermilk, evaporated milk, milkshake.
                              ALSO required `wwf_fg2_dairy_class`:
                              * cheese — any hard / soft / fresh /
                                aged / blue / grilled cheese.
                              * other  — milk / yoghurt / cream /
                                quark / skyr / kefir.
    * dairy_alternative_plant — oat / soy / almond / rice / coconut /
                              hazelnut / hemp / pea milk; plant
                              yoghurt; vegan cheese / cream / kefir.
                              Coconut MILK is dairy_alternative_plant.
                              Coconut FLESH is FG1 nuts_seeds.
                              `wwf_fg2_dairy_class` MUST be null.

- FG3 — Fats and oils. Required `wwf_fg3_kind`:
    * plant_based_fat  — olive / sunflower / rapeseed / coconut /
                        sesame / peanut / avocado oil, margarine,
                        plant-based butter substitute.
    * animal_based_fat — butter (salted/unsalted/clarified), ghee,
                        lard, duck/goose fat, tallow.
    BUTTER IS FG3 ANIMAL FAT — NOT FG2.

- FG4 — Fruits and vegetables. No subgroup. Fresh / frozen / canned
  / dried / fermented / marinated fruits and vegetables. Tomato
  purée. Pears in syrup / rum / alcohol (primary ingredient is
  fruit). Unsweetened applesauce. Sweetcorn / baby corn /
  corn-on-the-cob (the immature corn). Beetroot / carrots /
  radishes / parsnips (root vegetables that are NOT true tubers).
  Mushrooms.

- FG5 — Grains and cereals. Required `wwf_fg5_grain_kind`:
    * whole_grain   — name contains "wholegrain", "complet",
                      "intégral", "wholemeal", "brun", "brown",
                      "multigrain". Oats and oatmeal default to
                      whole_grain. Quinoa, bulgur, farro, spelt,
                      barley default to whole_grain. Mature/dry
                      corn (cornflakes / polenta / corn flour).
    * refined_grain — white bread, regular pasta, white rice,
                      cornflakes from refined corn, semolina. If no
                      whole-grain signal, default to refined_grain.

- FG6 — Tubers and starchy foods. No subgroup. Potatoes (russet,
  white, purple), sweet potatoes, cassava, taro, yam, lotus root.
  BUT fries / chips / wedges / Pommes Duchesse / unfilled
  croquettes → FG7 (cooked with added fat/salt).

- FG7 — Snacks high in added fats / salt / sugar. Required
  `wwf_fg7_kind`:
    * plant_based_snack  — chips/crisps, tortilla chips, corn chips,
                          vegetable chips, pretzels, popcorn, fries,
                          plant-based cookies/biscuits, sorbet, sweet
                          spreads without dairy (jam, honey, agave
                          syrup, maple syrup, marmalade), fruit
                          compote WITH added sugar.
    * animal_based_snack — chocolate with milk / butter / cream /
                          honey / gelatine, ice cream, gelato, frozen
                          yoghurt, custard, mousse, panna cotta,
                          puddings, dulce de leche, sweetened
                          nut-cocoa spread (e.g. Nutella), sweet
                          pastries (croissant, pain au chocolat,
                          brioche, doughnuts, muffins, cakes,
                          pancakes, crêpes, waffles, baklava),
                          marshmallows.

- out_of_scope — methodology exclusions ONLY:
    * Beverages other than dairy / dairy alternatives:
      water, soda, juice, smoothies, alcohol, tea, coffee, cocoa
      drink (including with milk powder).
    * Condiments and sauces: ketchup, barbecue sauce, mustard,
      mayonnaise, salad dressing, vinaigrette, vinegar, soy sauce.
    * Herbs, spices, salt, flavourings, additives.
    * Vitamins and supplements.
    * Baby formula and baby purees.
    * Stock cubes, broth, bouillon.
    * Culinary ingredients: baking powder, natron, starch, locust
      bean gum, gelatine sheets.
    * Novel proteins: insects, cultured meats, precision
      fermentation, microalgae.
    * Non-food: detergents, hygiene products, paper, pet
      accessories (litter / toys / leashes / poop bags).

- unknown — reserved for unusable names (empty, "Produit",
  placeholders). Almost never.

Composite products (`wwf_is_composite=true`):
Bucket priority (`wwf_composite_step1_bucket`):
  1. Contains ANY meat → meat_based.
  2. Contains fish/seafood and no meat → seafood_based.
  3. Contains dairy and/or eggs but no meat/seafood → vegetarian.
  4. Contains no meat, seafood, eggs, or dairy → vegan.

Typical composites: pizza, sandwich, salade composée, soupe, sushi
roll, pasta dish (lasagne, carbonara, pesto pasta bake), quiche,
flammkuchen, calzone, kebab, dumpling / gyoza / spring roll, wrap,
ready-made breakfast bowl, gratin / pasta bake, rösti with cheese
or meat, trail mix, vegetables with cream or butter, savoury
spreads with mixed food groups, ready-to-drink coffee with milk.

Confidence calibration:
- 0.90–0.99 obvious whole foods (pommes, carottes, blanc de poulet,
  lait demi-écrémé, lentilles, beurre doux).
- 0.75–0.89 clear processed foods (chips, yaourt nature, huile
  d'olive vierge).
- 0.60–0.74 composites and prepared meals.
- 0.40–0.59 propose category + flag review.
- below 0.40 only when truly ambiguous; still pick the best food
  group.

Response format — RETURN EXACTLY this JSON object:

{
  "results": [
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
  ]
}

Hard rules:
- Every input id MUST appear exactly once in `results`.
- All required subgroups MUST be present for their food group.
- All OTHER subgroup fields MUST be null.
- `out_of_scope` and `unknown` MUST have all subgroup fields null
  AND `wwf_is_composite=false` AND `wwf_composite_step1_bucket=null`.
- `wwf_is_composite=true` MUST come with a `wwf_composite_step1_bucket`.
- DO NOT wrap the JSON in markdown fences.
- ALL field separators MUST be commas. Output must be valid JSON.
"""


@dataclass(frozen=True)
class BatchClassifierItem:
    """One product entry in a batched prompt.

    ``id`` is the value the model will echo back so we can re-associate
    the result with the source product. We use the product's UUID as a
    string — short enough not to waste tokens, unique within a batch.
    """

    id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class BatchClassifierPrompt:
    methodology: Methodology
    prompt_version: str
    system_message: str
    user_message: str
    item_ids: tuple[str, ...]


def _prune_payload(payload: dict[str, object]) -> dict[str, object]:
    """Drop null / empty fields from a product payload so the batch
    user-message is compact. The allowlist guard still runs on the
    pruned dict, so privacy is unchanged."""
    out: dict[str, object] = {}
    for k, v in payload.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, tuple)) and len(v) == 0:
            continue
        out[k] = v
    return out


def build_batch_classifier_prompt(
    items: list[tuple[str, ClassifierPromptInput]],
    methodology: Methodology,
    *,
    prompt_version: str | None = None,
) -> BatchClassifierPrompt:
    """Assemble a batched prompt for N products.

    ``items`` is a list of ``(id, prompt_input)`` pairs. The id is what
    the model echoes back in its response; the orchestrator uses it to
    re-associate the LLM verdict with the source ``NormalizedProduct``.

    Phase WWF-A — methodology-specific default prompt version. PT
    stays on ``BATCH_CLASSIFIER_PROMPT_VERSION`` (``batch_classifier_v5``)
    so Phase 34Q/V calibration samples remain valid. WWF uses
    ``BATCH_WWF_PROMPT_VERSION`` (``batch_wwf_v2``) — the new contract
    requires the model to return all required subgroups
    (wwf_fg1_subgroup / wwf_fg2_kind / wwf_fg2_dairy_class /
    wwf_fg3_kind / wwf_fg5_grain_kind / wwf_fg7_kind) plus
    ``wwf_composite_step1_bucket`` for composites.
    """
    if not items:
        raise ValueError("batch must contain at least one item")
    if prompt_version is None:
        prompt_version = (
            BATCH_CLASSIFIER_PROMPT_VERSION
            if methodology is Methodology.PROTEIN_TRACKER
            else BATCH_WWF_PROMPT_VERSION
        )

    # Layered privacy: validate every product payload BEFORE assembling
    # the batched user message. This makes the policy violation
    # impossible to miss in tests.
    compacted: list[BatchClassifierItem] = []
    for item_id, prompt_input in items:
        payload = prompt_input.to_payload()
        assert_payload_allowed(payload)
        compacted.append(
            BatchClassifierItem(id=item_id, payload=_prune_payload(payload))
        )

    system_message = (
        _PT_SYSTEM
        if methodology is Methodology.PROTEIN_TRACKER
        else _WWF_SYSTEM
    )

    # JSONL-style body — easier for the model to scan than a single
    # giant JSON array, and uses fewer tokens than pretty-printed JSON.
    lines: list[str] = [
        "Classify each of the following products. Respond with strict JSON only.",
        "Input products:",
    ]
    import json as _json

    for c in compacted:
        line = _json.dumps(
            {"id": c.id, **c.payload}, ensure_ascii=False, separators=(",", ":")
        )
        lines.append(line)
    user_message = "\n".join(lines)

    return BatchClassifierPrompt(
        methodology=methodology,
        prompt_version=prompt_version,
        system_message=system_message,
        user_message=user_message,
        item_ids=tuple(c.id for c in compacted),
    )


# ---------------------------------------------------------------------------
# Phase 34H — repair prompt
# ---------------------------------------------------------------------------

_PT_REPAIR_SYSTEM = """\
Your previous response was not valid JSON for the Altera classifier.

Return ONLY a valid JSON object — no markdown fences, no prose, no
trailing explanation. The object MUST start with `{` and end with `}`.

Required schema (note: every field separator is a comma):
{
  "results": [
    {"id":"p1","pt_group":"plant_based_core","confidence":0.95,"rationale":"fruit"}
  ]
}

`pt_group` MUST be one of:
plant_based_core | plant_based_non_core | composite_products |
animal_core | out_of_scope | unknown.

Rules:
- Every input id MUST appear exactly once in `results`.
- `rationale` MUST be at most 8 words.
- ALL field separators MUST be commas. Output must parse as JSON.
- DO NOT add fields beyond {id, pt_group, confidence, rationale}.
- DO NOT include any text outside the JSON object.
"""

_WWF_REPAIR_SYSTEM = """\
Your previous response was not valid JSON for the Altera classifier.

Return ONLY a valid JSON object — no markdown, no prose. Required schema:
{
  "results": [
    {
      "id": "<the id>",
      "wwf_food_group": "<one of: FG1 | FG2 | FG3 | FG4 | FG5 | FG6 | FG7 | out_of_scope | unknown>",
      "wwf_is_composite": <true|false>,
      "confidence": <number 0.0-1.0>,
      "rationale": "<short>"
    }
  ]
}

Rules:
- Every input id MUST appear exactly once.
- DO NOT include text outside the JSON object.
"""


def build_repair_batch_classifier_prompt(
    items: list[tuple[str, ClassifierPromptInput]],
    methodology: Methodology,
    *,
    bad_response: str = "",
    prompt_version: str = BATCH_CLASSIFIER_PROMPT_VERSION,
) -> BatchClassifierPrompt:
    """Assemble a shorter, stricter prompt for the parse-failure retry.

    Includes the previous bad response (truncated by the caller) so the
    model can self-correct. The system message uses the word "JSON"
    explicitly and lists allowed enum values directly — same privacy
    guarantees as the main prompt (every per-product payload is
    re-validated by ``assert_payload_allowed`` before assembly).
    """
    if not items:
        raise ValueError("batch must contain at least one item")

    compacted: list[BatchClassifierItem] = []
    for item_id, prompt_input in items:
        payload = prompt_input.to_payload()
        assert_payload_allowed(payload)
        compacted.append(
            BatchClassifierItem(id=item_id, payload=_prune_payload(payload))
        )

    system_message = (
        _PT_REPAIR_SYSTEM
        if methodology is Methodology.PROTEIN_TRACKER
        else _WWF_REPAIR_SYSTEM
    )

    lines: list[str] = []
    if bad_response:
        # Include the previous bad response so the model can fix it.
        # The orchestrator already truncated to 500 chars and stripped
        # zero-width characters, but cap again here defensively.
        lines.append("Previous invalid response (for context):")
        lines.append(bad_response[:500])
        lines.append("")
    lines.append("Re-classify each product. Respond with strict JSON only.")
    lines.append("Input products:")
    import json as _json

    for c in compacted:
        line = _json.dumps(
            {"id": c.id, **c.payload}, ensure_ascii=False, separators=(",", ":")
        )
        lines.append(line)
    user_message = "\n".join(lines)

    return BatchClassifierPrompt(
        methodology=methodology,
        prompt_version=prompt_version,
        system_message=system_message,
        user_message=user_message,
        item_ids=tuple(c.id for c in compacted),
    )

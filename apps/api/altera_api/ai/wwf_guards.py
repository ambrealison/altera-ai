"""Phase WWF-D — deterministic post-classification guards for WWF.

These guards mirror the PT guards (``altera_api.ai.pt_guards``) but
implement the **WWF Planet-Based Diets** methodology rules:

  - product is classified by **sales weight**, not protein grams;
  - whole products get a food group AND its required subgroup;
  - composites set ``wwf_is_composite=true`` AND
    ``wwf_composite_step1_bucket`` (meat → seafood → vegetarian →
    vegan precedence);
  - methodology exclusions (beverages other than dairy, condiments,
    bouillon, baby food, novel proteins, herbs/spices) are
    ``out_of_scope``;
  - pet food is in-scope per Altera's documented decision; pet
    accessories are out_of_scope;
  - readable product names never end up as final ``unknown``.

Each guard is O(1)/regex-local. No OpenAI calls. No retries. When a
guard overrides the model verdict, confidence is clamped to ≤ 0.69
so the row routes to ``needs_review`` — we never silently
auto-accept a guard-corrected category.

The methodology source-of-truth is
``docs/methodologies/wwf-classification-rules.md`` (Phase WWF-B).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)

# Confidence ceiling applied when ANY guard overrides the model's
# verdict (mirrors the PT guard ceiling).
_GUARD_CONFIDENCE_CEILING: Decimal = Decimal("0.69")


# ---------------------------------------------------------------------------
# Tokenisation (accent + ligature-aware substring match)
# ---------------------------------------------------------------------------


def _normalise(s: str) -> str:
    """Lowercase + accent-fold + ligature-expand for substring match.

    Mirrors ``pt_guards._normalise`` exactly so the two modules
    behave consistently on FR product names.
    """
    lowered = s.lower().replace("œ", "oe").replace("æ", "ae")
    nf = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in nf if not unicodedata.combining(c))


def _contains_any_word(haystack: str, needles: tuple[str, ...]) -> bool:
    """True if any needle appears as a whole word in haystack."""
    for n in needles:
        if re.search(rf"\b{re.escape(n)}\b", haystack):
            return True
    return False


def _contains_any_phrase(haystack: str, needles: tuple[str, ...]) -> bool:
    """True if any needle appears as a substring in haystack.

    Used for multi-word patterns (e.g. ``"fruits de mer"``,
    ``"a la creme"``) where ``\\b``-anchored matching is awkward
    because of the internal spaces.
    """
    for n in needles:
        if n in haystack:
            return True
    return False


# ---------------------------------------------------------------------------
# Vocabularies — WWF-specific (do NOT reuse PT scope rules wholesale)
# ---------------------------------------------------------------------------


#: Methodology exclusions per WWF XLSX Tab 3 — out_of_scope.
#: Note the difference from PT: jus / smoothie / nectar are
#: out_of_scope here (beverages), whereas PT keeps them as
#: plant_based_non_core.
_WWF_OOS_BEVERAGE_TOKENS: tuple[str, ...] = (
    "eau",
    "eau minerale",
    "eau gazeuse",
    "eau plate",
    "soda",
    "cola",
    "limonade",
    "tonic",
    "biere",
    "vin",
    "champagne",
    "spiritueux",
    "whisky",
    "vodka",
    "rhum",
    "gin",
    "liqueur",
    "alcool",
    "jus",
    "jus de fruit",
    "jus de fruits",
    "jus d'orange",
    "jus de pomme",
    "jus de raisin",
    "jus de tomate",
    "nectar",
    "smoothie",
    "smoothies",
    "the",       # thé, accent-folded
    "tisane",
    "infusion",
    "cafe",      # café
    "cacao boisson",
    "energy drink",
    "boisson energisante",
    "boisson fruitee",
    "boisson fruits rouges",
    "pur jus",
)


_WWF_OOS_CONDIMENT_TOKENS: tuple[str, ...] = (
    "ketchup",
    "moutarde",
    "mayonnaise",
    "vinaigrette",
    "vinaigre",
    "sauce barbecue",
    "sauce salade",
    "sauce soja",
    "sauce piquante",
    "sauce salsa",
    "salad dressing",
    "tabasco",
    "harissa",
)


_WWF_OOS_HERB_SPICE_TOKENS: tuple[str, ...] = (
    "sel",
    "poivre",
    "epice",
    "epices",
    "herbe",
    "herbes",
    "herbes de provence",
    "thym",
    "romarin",
    "basilic",
    "origan",
    "laurier",
    "persil",
    "ciboulette",
    "menthe seche",
    "menthe sechee",
    "cumin",
    "curcuma",
    "paprika",
    "muscade",
    "cannelle",
    "girofle",
    "gingembre",
    "anis",
    "safran",
    "curry",
    "ras el hanout",
    "garam masala",
    # NOTE: "curry" is NOT here — "curry" by itself in a product
    # name almost always means "Curry de poulet" / "Curry saumon"
    # (composite dishes). Curry powder as a pure spice is rare in
    # retail SKUs.
)


_WWF_OOS_INGREDIENT_TOKENS: tuple[str, ...] = (
    "levure boulangere",
    "levure chimique",
    "bicarbonate",
    "natron",
    "amidon",
    "starch",
    "locust bean gum",
    "fecule",
    "agar agar",
    "gelatine en poudre",
    "gelatine sheets",
    "bouillon",
    "bouillon cube",
    "bouillon volaille",
    "bouillon legumes",
    "stock cube",
    "stock powder",
    "broth",
    "fond de veau",
    "fond de volaille",
    "additif",
    "additifs",
    "arome",
    "aromes",
    "colorant",
    "vitamine",
    "vitamines",
    "supplement",
    "complement alimentaire",
)


_WWF_OOS_BABY_TOKENS: tuple[str, ...] = (
    "lait infantile",
    "lait maternise",
    "lait bebe",
    "lait croissance",
    "biberon",
    "puree bebe",
    "petit pot bebe",
    "petits pots bebe",
    "compote bebe",
    "compotes bebe",
    "formule infantile",
)


_WWF_OOS_NOVEL_PROTEIN_TOKENS: tuple[str, ...] = (
    "insecte",
    "insectes",
    "grillons",
    "vers de farine",
    "cultured meat",
    "viande cultivee",
    "precision fermentation",
    "microalgue",
    "spirulina drink",
)


#: Pet accessories — out_of_scope. Pet FOOD is handled separately
#: (in-scope per Altera decision).
_WWF_PET_ACCESSORY_TOKENS: tuple[str, ...] = (
    "litiere",
    "litiere chat",
    "jouet chien",
    "jouet chat",
    "harnais",
    "laisse chien",
    "gamelle",
    "sacs dejections",
    "sac dejection",
    "panier chien",
    "panier chat",
    "antiparasitaire",
    "anti-puces",
)


#: Generic non-food / household / hygiene — out_of_scope.
_WWF_NON_FOOD_TOKENS: tuple[str, ...] = (
    "lessive",
    "adoucissant",
    "liquide vaisselle",
    "nettoyant",
    "javel",
    "papier toilette",
    "essuie-tout",
    "essuie tout",
    "shampoing",
    "shampooing",
    "gel douche",
    "dentifrice",
    "brosse a dents",
    "deodorant",
    "couches bebe",
    "couches",
    "lingettes bebe",
    "lingettes",
    "savon",
)


#: Pet food — in-scope per Altera decision. Generic petfood defaults
#: to composite; petfood + explicit animal token → FG1 subgroup
#: equivalents; petfood + plant-protein anchor → FG1 plant.
_WWF_PETFOOD_TOKENS: tuple[str, ...] = (
    "croquettes chat",
    "croquettes chien",
    "croquettes poisson",
    "croquettes oiseau",
    "patee chien",
    "patee chat",
    "friandise chien",
    "friandise chat",
    "friandises chien",
    "friandises chat",
    "aliment chien",
    "aliment chat",
    "nourriture chien",
    "nourriture chat",
    "petfood",
    "pet food",
)


#: Dairy / dairy-alternative beverages — IN scope (FG2), even
#: though they look like beverages. Must run BEFORE the generic
#: beverage exclusion.
_WWF_DAIRY_ANIMAL_BEVERAGE_TOKENS: tuple[str, ...] = (
    "lait demi-ecreme",
    "lait demi ecreme",
    "lait ecreme",
    "lait entier",
    "lait pasteurise",
    "lait uht",
    "lait de vache",
    "lait de brebis",
    "lait de chevre",
    "yaourt a boire",
    "yaourts a boire",
    "milkshake",
    "milkshakes",
    "kefir lait",
    "protein drink dairy",
    "protein shake dairy",
)

_WWF_PLANT_MILK_TOKENS: tuple[str, ...] = (
    "boisson avoine",
    "boisson amande",
    "boisson amandes",
    "boisson soja",
    "boisson riz",
    "boisson noisette",
    "boisson noix",
    "boisson noix de coco",
    "boisson coco",
    "boisson chanvre",
    "boisson epeautre",
    "boisson quinoa",
    "lait d'amande",
    "lait amande",
    "lait d'avoine",
    "lait avoine",
    "lait de soja",
    "lait soja",
    "lait de riz",
    "lait riz",
    "lait de coco",
    "lait coco",
    "lait noisette",
    "lait vegetal",
    "almond milk",
    "oat milk",
    "soy milk",
    "rice milk",
    "coconut milk",
)


# ---------------------------------------------------------------------------
# FG1 subgroup vocabularies
# ---------------------------------------------------------------------------


_WWF_FG1_RED_MEAT_TOKENS: tuple[str, ...] = (
    "boeuf",
    "buf",
    "veau",
    "porc",
    "agneau",
    "mouton",
    "lamb",
    "beef",
    "pork",
    "veal",
    "goat",
    "goats",
    "goat meat",
    "chevre viande",
    "venison",
    "gibier",
    "biche",
    "cerf",
    "sanglier",
    "lapin",
    "rabbit",
    # Phase WWF-L — common red-meat cuts and varieties from the WWF
    # Category reference taxonomy.
    "brisket",
    "tenderloin",
    "skirt steak",
    "ribeye",
    "sirloin",
    "wild boar",
    "moose",
    "bison",
    "horse meat",
    "mutton",
    "kangaroo",
)


_WWF_FG1_POULTRY_TOKENS: tuple[str, ...] = (
    "poulet",
    "dinde",
    "canard",
    "oie",
    "volaille",
    "chicken",
    "turkey",
    "duck",
    "goose",
    "pintade",
    "caille",
    "quail",
    "perdrix",
    "faisan",
    # Phase WWF-L — English game-bird vocabulary from the WWF
    # Category reference taxonomy.
    "partridge",
    "pheasant",
    "guinea fowl",
)


_WWF_FG1_PROCESSED_MEAT_TOKENS: tuple[str, ...] = (
    "jambon",
    "ham",
    "bacon",
    "lardon",
    "lardons",
    "saucisse",
    "saucisses",
    "saucisson",
    "salami",
    "chorizo",
    "merguez",
    "mortadelle",
    "pate",
    "rillettes",
    "prosciutto",
    "jamon",
    "smoked meat",
    "smoked meats",
    "viande fumee",
    "burger patty",
    "burger viande",
    "nuggets poulet",
    "charcuterie",
    "corned beef",
    "spam",
    "koftas",
    # Phase WWF-L — additional processed-meat vocabulary from the WWF
    # Category reference taxonomy.
    "pastrami",
    "frankfurter",
    "frankfurters",
    "hot dog",
    "hot dogs",
    "pepperoni",
    "kielbasa",
    "bratwurst",
    "andouille",
    "blood sausage",
    "boudin",
    "haggis",
    "deli meat",
    "deli meats",
    "cured meat",
    "cured meats",
    "cured ham",
    "biltong",
    "jerky",
)


_WWF_FG1_SEAFOOD_TOKENS: tuple[str, ...] = (
    "saumon",
    "thon",
    "cabillaud",
    "merlu",
    "lieu",
    "merlan",
    "sardine",
    "sardines",
    "maquereau",
    "hareng",
    "anchois",
    "truite",
    "morue",
    "bar",
    "dorade",
    "sole",
    "lotte",
    "crevette",
    "crevettes",
    "gambas",
    "moule",
    "moules",
    "huitre",
    "huitres",
    "calamar",
    "calmar",
    "poulpe",
    "homard",
    "crabe",
    "surimi",
    "fish",
    "shellfish",
    "shrimp",
    "prawn",
    "prawns",
    "scallop",
    "scallops",
    "lobster",
    "crab",
    "tuna",
    "salmon",
    "cod",
    "trout",
    "herring",
    "mackerel",
    "anchovy",
    # Phase WWF-L — English plural / additional seafood vocabulary
    # from the WWF Category reference taxonomy.
    "mussels",
    "oysters",
    "octopus",
    "squid",
    "cuttlefish",
    "fermented seafood",
    "breaded calamari",
    "smoked salmon",
    "anchovies",
    "sardines",
    "scallops",
    "shrimps",
    "prawns",
    "crabs",
    "lobsters",
    "clams",
    "monkfish",
    "haddock",
    "halibut",
    "snapper",
    "tilapia",
    "carp",
    "pollack",
    "pollock",
    "swordfish",
    "skate",
    "perch",
    "pike",
    "sea bass",
    "sea bream",
    # Phase WWF-M
    "crayfish",
    "crawfish",
)


_WWF_FG1_EGG_TOKENS: tuple[str, ...] = (
    "oeuf",
    "oeufs",
    "egg",
    "eggs",
    "blanc d'oeuf",
    "jaune d'oeuf",
    "omelette base",
)


_WWF_FG1_LEGUME_TOKENS: tuple[str, ...] = (
    "lentil",
    "lentils",
    "lentille",
    "lentilles",
    "pois chiche",
    "pois chiches",
    "chickpea",
    "chickpeas",
    "haricot",
    "haricots",
    "haricot rouge",
    "haricots rouges",
    "haricot blanc",
    "haricots blancs",
    "haricot noir",
    "haricots noirs",
    "haricot azuki",
    "pois casse",
    "pois casses",
    "petits pois",  # XLSX Tab 7 — green peas are FG1 legumes
    "green peas",
    "split peas",
    "fave",
    "faves",
    "feve",
    "feves",
    "fava",
    "fava beans",
    "broad beans",
    "flageolet",
    "flageolets",
    "edamame",
    "soja jaune",
    "soja vert",
    "soya bean",
    "soya beans",
    "soy bean",
    "soy beans",
    "soybeans",
    "mungo",
    "mung",
    "mung bean",
    "mung beans",
    "lupin",
    "lupini",
    "cowpea",
    "cowpeas",
    "urad",
    "jackfruit",
    # Phase WWF-L — English bean varieties from the WWF Category
    # reference taxonomy.
    "aduki beans",
    "adzuki beans",
    "black-eyed beans",
    "black eyed peas",
    "black turtle beans",
    "borlotti beans",
    "butter beans",
    "cannellini beans",
    "kidney beans",
    "navy beans",
    "pinto beans",
    "white beans",
    "haricot beans",
    "great northern beans",
    "calypso beans",
    "anasazi beans",
    "fava bean",
    "broad bean",
    "yard-long beans",
    "rice beans",
    "tepary beans",
    "winged beans",
    "hyacinth beans",
    "moth beans",
    "horse gram",
    "bambara groundnut",
    # Phase WWF-M — singular bean variants the CSV uses.
    "navy bean",
    "wax beans",
    "wax bean",
    "pinto bean",
    "kidney bean",
)


_WWF_FG1_NUTS_SEEDS_TOKENS: tuple[str, ...] = (
    "noix",
    "noix de cajou",
    "cajou",
    "noisette",
    "noisettes",
    "amande",
    "amandes",
    "cacahuete",
    "cacahuetes",
    "pistache",
    "pistaches",
    "pignon",
    "pignons",
    "noix de macadamia",
    "graine",
    "graines",
    "graine de chia",
    "graines de chia",
    "chia",
    "lin",
    "graines de lin",
    "sesame",
    "graines de sesame",
    "tournesol",
    "graines de tournesol",
    "graines de courge",
    "tahini",
    "beurre de cacahuete",
    "almond butter",
    "peanut butter",
    "coconut flesh",
    "chair de coco",
    # Phase WWF-M — English plural / singular forms of common nuts &
    # seeds from the WWF Category reference taxonomy.
    "almond",
    "almonds",
    "brazilnut",
    "brazilnuts",
    "brazil nut",
    "brazil nuts",
    "cashew",
    "cashews",
    "chestnut",
    "chestnuts",
    "flaxseed",
    "flaxseeds",
    "flax seed",
    "flax seeds",
    "hazelnut",
    "hazelnuts",
    "hemp seed",
    "hemp seeds",
    "hempseed",
    "hempseeds",
    "linseed",
    "linseeds",
    "macadamia",
    "macadamias",
    "peanut",
    "peanuts",
    "pecan",
    "pecans",
    "pistachio",
    "pistachios",
    "pine nut",
    "pine nuts",
    "pinenut",
    "pinenuts",
    "poppy seed",
    "poppy seeds",
    "sunflower seed",
    "sunflower seeds",
    "walnut",
    "walnuts",
    "nigella seed",
    "nigella seeds",
    "pumpkin seed",
    "pumpkin seeds",
    "sesame seed",
    "sesame seeds",
    "coconut",
)


_WWF_FG1_ALT_PROTEIN_TOKENS: tuple[str, ...] = (
    "tofu",
    "tempeh",
    "seitan",
    "mycoprotein",
    "okara",
    "falafel",
    "houmous",
    "hummus",
    "proteine de soja",
    "soja texture",
)


_WWF_FG1_MEAT_ALT_TOKENS: tuple[str, ...] = (
    "steak vegetal",
    "burger vegetal",
    "burger vegan",
    "nuggets vegetal",
    "nuggets vegetaux",
    "escalope vegetale",
    "boulette vegetale",
    "boulettes vegetales",
    "saucisse vegetale",
    "hache vegetal",
    "emince vegetal",
    "plant-based burger",
    "plant-based mince",
    "plant-based fish",
    "egg alternative",
    "egg replacer",
    # Phase WWF-M
    "vegetable burger",
    "vegetable sausage",
    "vegetable nugget",
    "vegetable nuggets",
    "meat alternative",
    "meat alternatives",
    "meat substitute",
    "meat substitutes",
    "veggie burger",
    "veggie burgers",
    "veggie sausage",
    "veggie sausages",
    "soy-based",
    "soya-based",
    "soy protein-based",
)


# ---------------------------------------------------------------------------
# FG2 vocabularies
# ---------------------------------------------------------------------------


_WWF_FG2_CHEESE_TOKENS: tuple[str, ...] = (
    "fromage",
    "fromages",
    "camembert",
    "brie",
    "comte",
    "gouda",
    "mozzarella",
    "parmesan",
    "emmental",
    "cheddar",
    "gruyere",
    "feta",
    "ricotta",
    "burrata",
    "halloumi",
    "raclette",
    "munster",
    "reblochon",
    "saint nectaire",
    "tomme",
    "pecorino",
    "manchego",
    "chevre",
    "blue cheese",
    "bleu",
    "fourme",
    "cottage cheese",
    "cream cheese",
    "philadelphia",
    # Phase WWF-M — common English / generic cheese descriptors.
    "cheese",
    "cheeses",
    "aged cheese",
    "hard cheese",
    "soft cheese",
    "fresh cheese",
    "blue-veined cheese",
)


_WWF_FG2_OTHER_DAIRY_TOKENS: tuple[str, ...] = (
    "lait",
    "lait demi-ecreme",
    "lait ecreme",
    "lait entier",
    "yaourt",
    "yaourts",
    "yogurt",
    "yoghurt",
    "kefir",
    "creme",
    "creme fraiche",
    "creme epaisse",
    "creme liquide",
    "creme legere",
    "buttermilk",
    "babeurre",
    "petit suisse",
    "quark",
    "skyr",
    "milkshake",
    "fromage blanc",
    "fromage frais",
    "sour cream",
    "cottage",
    # Phase WWF-M — English vocabulary from the WWF Category
    # reference taxonomy.
    "milk",
    "milks",
    "cow's milk",
    "cows milk",
    "sheep's milk",
    "sheeps milk",
    "buffalo milk",
    "goat's milk",
    "goats milk",
    "evaporated milk",
    "condensed milk",
    "powdered milk",
    "milk powder",
    "single cream",
    "double cream",
    "clotted cream",
    "whipped cream",
    "heavy cream",
    "half-and-half",
    "cream",
    "coffee creamer",
)


_WWF_FG2_PLANT_DAIRY_TOKENS: tuple[str, ...] = (
    "boisson avoine",
    "boisson amande",
    "boisson amandes",
    "boisson soja",
    "boisson riz",
    "boisson noisette",
    "boisson coco",
    "boisson chanvre",
    "lait d'amande",
    "lait amande",
    "lait d'avoine",
    "lait avoine",
    "lait de soja",
    "lait soja",
    "lait de riz",
    "lait riz",
    "lait de coco",
    "lait coco",
    "lait vegetal",
    "yaourt vegetal",
    "yaourts vegetaux",
    "yaourt soja",
    "yaourt amande",
    "yaourt coco",
    "fromage vegan",
    "vegan cheese",
    "cream cheese vegan",
    "creme vegetale",
    "almond milk",
    "oat milk",
    "soy milk",
    "rice milk",
    "coconut milk",
    "plant cheese",
    "plant yogurt",
    # Phase WWF-M
    "plant-based",
    "plant based",
    "plant-based cheese",
    "plant-based hard cheese",
    "plant-based soft cheese",
    "soy-based cheese",
    "soya-based cheese",
    "plant-based coffee creamer",
    "plant-based coffee creamers",
    "plant-based yoghurt",
    "plant-based yogurt",
    "vegan yogurt",
    "vegan yoghurt",
    "vegan butter",
    "vegan cream",
    "non-dairy",
    "non dairy",
)


# ---------------------------------------------------------------------------
# FG3 vocabularies
# ---------------------------------------------------------------------------


_WWF_FG3_ANIMAL_FAT_TOKENS: tuple[str, ...] = (
    "beurre",
    "beurre doux",
    "beurre sale",
    "ghee",
    "lard",
    "saindoux",
    "graisse de canard",
    "graisse d'oie",
    "duck fat",
    "goose fat",
    "tallow",
    "tallow beef",
    "suif",
)


_WWF_FG3_PLANT_FAT_TOKENS: tuple[str, ...] = (
    "huile",
    "huile d'olive",
    "huile olive",
    "huile de tournesol",
    "huile tournesol",
    "huile de colza",
    "huile colza",
    "huile de coco",
    "huile coco",
    "huile de sesame",
    "huile sesame",
    "huile d'arachide",
    "huile arachide",
    "huile avocat",
    "huile d'avocat",
    "huile de pepins de raisin",
    "huile de lin",
    "huile de chanvre",
    "huile palme",
    "huile de palme",
    "margarine",
    "margarine vegetale",
    "plant butter",
    "vegan butter",
    # Phase WWF-M — English oil vocabulary. Bare "oil" alone is
    # ambiguous (engine oil!) but compound forms are safe.
    "olive oil",
    "sunflower oil",
    "rapeseed oil",
    "canola oil",
    "coconut oil",
    "sesame oil",
    "peanut oil",
    "groundnut oil",
    "palm oil",
    "avocado oil",
    "grapeseed oil",
    "flaxseed oil",
    "hemp seed oil",
    "hempseed oil",
    "vegetable oil",
    "soybean oil",
    "soya oil",
    "corn oil",
    "safflower oil",
    "walnut oil",
    "almond oil",
    "cooking oil",
)


# ---------------------------------------------------------------------------
# FG4 / FG5 / FG6 / FG7 vocabularies
# ---------------------------------------------------------------------------


#: FG4 fruits & vegetables — explicit tokens for the guard to
#: detect when the model misrouted a fruit/veg name elsewhere.
#: ``compote sucree`` and ``applesauce sweetened`` are handled
#: separately (→ FG7 plant_snack per XLSX FAQ).
_WWF_FG4_TOKENS: tuple[str, ...] = (
    # Sweetcorn family — FG4 per XLSX (mature/dry corn → FG5).
    "mais doux",
    "sweetcorn",
    "baby corn",
    "corn on the cob",
    "corn-on-the-cob",
    "epi de mais",
    # Root vegetables → FG4 (only true tubers → FG6).
    "carotte",
    "carottes",
    "radis",
    "betterave",
    "betteraves",
    "navet",
    "navets",
    "panais",
    # Common fruits.
    "pomme",
    "pommes",
    "poire",
    "poires",
    "banane",
    "bananes",
    "orange",
    "oranges",
    "fraise",
    "fraises",
    "framboise",
    "framboises",
    "myrtille",
    "myrtilles",
    "raisin",
    "raisins",
    "kiwi",
    "mangue",
    "ananas",
    "peche",
    "abricot",
    "prune",
    "cerise",
    "cerises",
    "melon",
    # Common vegetables.
    "tomate",
    "tomates",
    "courgette",
    "courgettes",
    "aubergine",
    "concombre",
    "poivron",
    "salade verte",
    "salade composee",
    "epinard",
    "epinards",
    "brocoli",
    "brocolis",
    "chou",
    "chou-fleur",
    "champignon",
    "champignons",
    "oignon",
    "oignons",
    "ail",
    "poireau",
    "poireaux",
    # Fruits in syrup / preserved fruit (XLSX Tab 7 — FG4 because
    # primary ingredient is fruit).
    "poires au sirop",
    "poires au rhum",
    "fruits au sirop",
    "fruits in syrup",
    "compote sans sucre",
    "applesauce",
    "applesauce unsweetened",
    # Tomato purée / sauce / passata (FG4, not condiment).
    "tomato puree",
    "tomato sauce",
    "passata",
    "coulis de tomate",
    # Phase WWF-L — English fruit + vegetable vocabulary from the WWF
    # Category reference taxonomy.
    "apple",
    "apples",
    "apricot",
    "apricots",
    "banana",
    "bananas",
    "blackberry",
    "blackberries",
    "blueberry",
    "blueberries",
    "cherry",
    "cherries",
    "clementine",
    "clementines",
    "cranberry",
    "cranberries",
    "dates",
    "dragon fruit",
    "fig",
    "figs",
    "grape",
    "grapes",
    "grapefruit",
    "guava",
    "kiwi",
    "kiwis",
    "lemon",
    "lemons",
    "lime",
    "limes",
    "lychee",
    "mango",
    "mangoes",
    "melon",
    "melons",
    "nectarine",
    "nectarines",
    "olive",
    "olives",
    "orange",
    "oranges",
    "papaya",
    "passion fruit",
    "peach",
    "peaches",
    "pear",
    "pears",
    "persimmon",
    "pineapple",
    "plum",
    "plums",
    "pomegranate",
    "raspberry",
    "raspberries",
    "rhubarb",
    "strawberry",
    "strawberries",
    "tangerine",
    "watermelon",
    # Vegetables.
    "artichoke",
    "artichokes",
    "asparagus",
    "aubergine",
    "eggplant",
    "beet",
    "beetroot",
    "bell pepper",
    "bell peppers",
    "bok choy",
    "broccoli",
    "brussels sprouts",
    "cabbage",
    "carrot",
    "carrots",
    "cauliflower",
    "celery",
    "chard",
    "courgette",
    "cucumber",
    "endive",
    "fennel",
    "garlic",
    "ginger",
    "green beans",
    "kale",
    "leek",
    "leeks",
    "lettuce",
    "mushroom",
    "mushrooms",
    "okra",
    "onion",
    "onions",
    "parsnip",
    "parsnips",
    "pepper",
    "peppers",
    "pumpkin",
    "radish",
    "radishes",
    "rocket",
    "arugula",
    "shallot",
    "spinach",
    "squash",
    "swiss chard",
    "tomato",
    "tomatoes",
    "turnip",
    "watercress",
    "zucchini",
    "sweetcorn",
    "sweet corn",
    # Phase WWF-M — additional fruit / vegetable vocabulary from
    # the WWF Category reference taxonomy (mismatch-CSV-driven).
    "kumquat",
    "kumquats",
    "mandarin",
    "mandarins",
    "mangosteen",
    "pommelo",
    "pomelo",
    "avocado",
    "avocados",
    "brussel sprouts",
    "brussel sprout",
    "brocolli",
    "broccolis",
    "chicory",
    "cress",
    "green beans",
    "garden beans",
    "haricot vert",
    "haricots verts",
    "leafy greens",
    "leafy greans",
    "kohlrabi",
    "pumpkins",
    "radicchio",
    "romanesco",
    "shallots",
    "currants",
    "currant",
    "prunes",
    "prune",
    "sultanas",
    "sultana",
    "raisins",
    "raisin",
    "kimchi",
    "sauerkraut",
    "citrus segments",
    "sugar snap peas",
    "snow peas",
    "stir fry vegetables",
    "stir-fry vegetables",
    "vegetable sticks",
    "ocra",
    "okra",
    "pak choi",
    "pak-choi",
    "bok choy",
    "frozen berries",
    "frozen fruits",
    "frozen vegetables",
    "dried fruit",
    "dried fruits",
    "dried apricot",
    "dried apricots",
    "raisins secs",
    "abricots secs",
)


_WWF_FG5_WHOLEGRAIN_TOKENS: tuple[str, ...] = (
    "complet",
    "complete",
    "completes",
    "wholegrain",
    "whole grain",
    "whole wheat",
    "wholemeal",
    "integral",
    "integrale",
    "integrales",
    "brun",
    "brown",
    "multigrain",
    "multi-grain",
    "ble complet",
    "riz complet",
    "pain complet",
    "pates completes",
    "oats",
    "oat",
    "oatmeal",
    "avoine",
    "muesli avoine",
    "quinoa",
    "bulgur",
    "boulgour",
    "epeautre",
    "spelt",
    "farro",
    "barley",
    "orge",
    "rye",
    "seigle",
    # Phase WWF-L — additional whole-grain vocabulary.
    "buckwheat",
    "sarrasin",
    "amaranth",
    "amarante",
    "millet",
    "teff",
    "freekeh",
    "wild rice",
    "brown rice",
)


_WWF_FG5_REFINED_DEFAULT_TOKENS: tuple[str, ...] = (
    "riz blanc",
    "riz basmati",
    "riz jasmin",
    "riz long",
    "pain blanc",
    "baguette",
    "spaghetti",
    "penne",
    "tagliatelle",
    "macaroni",
    "fusilli",
    "farine blanche",
    "farine ble",
    "semoule",
    "couscous",
    "cornflakes",
    "polenta",
    "corn flour",
    "tortilla",
    "tortillas",
    "noodle",
    "noodles",
    "udon",
    "vermicelle",
)


_WWF_FG6_TUBER_TOKENS: tuple[str, ...] = (
    "pomme de terre",
    "pommes de terre",
    "patate",
    "patates",
    "patate douce",
    "patates douces",
    "sweet potato",
    "sweet potatoes",
    "potato",
    "potatoes",
    "cassava",
    "manioc",
    "taro",
    "yam",
    "yams",
    "igname",
    "lotus root",
    # Phase WWF-M
    "jicama",
    "jerusalem artichoke",
    "topinambour",
    "tapioca",
    "yucca",
)


_WWF_FG7_PLANT_SNACK_TOKENS: tuple[str, ...] = (
    "chips",
    "crisps",
    "tortilla chips",
    "nachos",
    "popcorn",
    "pop corn",
    "pretzel",
    "pretzels",
    "bretzel",
    "tuile",
    "tuiles",
    "frite",
    "frites",
    "french fries",
    "pommes duchesse",
    "pommes noisette",
    "rosti classic",
    "sorbet",
    "confiture",
    "marmelade",
    "gelee",
    "miel",
    "honey",
    "agave",
    "sirop d'erable",
    "maple syrup",
    "sirop d'agave",
    "compote sucree",
    "compote avec sucre",
    "granola sucre",
    "muesli sucre",
    "cereales sucrees",
    "sucre",
    "sucre blanc",
    "sucre roux",
    "biscuit aperitif",
    "biscuits aperitif",
    "biscuit vegan",
    "chocolat noir",
)


_WWF_FG7_ANIMAL_SNACK_TOKENS: tuple[str, ...] = (
    "chocolat au lait",
    "chocolat lait",
    "tablette lait",
    "ice cream",
    "creme glacee",
    "glace",
    "gelato",
    "frozen yogurt",
    "mousse au chocolat",
    "mousse",
    "panna cotta",
    "custard",
    "creme dessert",
    "pudding",
    "dulce de leche",
    "nutella",
    "pate a tartiner chocolat",
    "croissant",
    "croissants",
    "pain au chocolat",
    "pain aux raisins",
    "brioche",
    "brioches",
    "doughnut",
    "donut",
    "donuts",
    "muffin",
    "muffins",
    "gateau",
    "gateaux",
    "cake",
    "cakes",
    "cupcake",
    "cupcakes",
    "madeleine",
    "madeleines",
    "financier",
    "financiers",
    "pancake",
    "pancakes",
    "crepe",
    "crepes",
    "waffle",
    "gaufre",
    "gaufres",
    "marshmallow",
    "marshmallows",
    "biscuit beurre",
    "biscuits beurre",
    "sable",
    "sables",
    # Phase WWF-L — additional animal-snack vocabulary.
    "doughnut",
    "doughnuts",
    "waffles",
    "ice cream sandwich",
    "ice cream sandwiches",
    "ice cream cake",
    "ice cream cakes",
)


# ---------------------------------------------------------------------------
# Composite detection vocabularies
# ---------------------------------------------------------------------------


_WWF_COMPOSITE_DISH_TOKENS: tuple[str, ...] = (
    "pizza",
    "pizzas",
    "lasagne",
    "lasagnes",
    "lasagna",
    "cannelloni",
    "ravioli",
    "raviolis",
    "tortellini",
    "gnocchi alla",
    "carbonara",
    "bolognaise",
    "bolognese",
    "amatriciana",
    "salad",
    "salads",
    "salade",
    "salades",
    "salade composee",
    "salade cesar",
    "salade nicoise",
    "salade poulet",
    "salade tofu",
    "salade quinoa",
    "salade pasta",
    "salade pâtes",
    "sandwich",
    "sandwiches",
    "wrap",
    "wraps",
    "kebab",
    "burrito",
    "fajita",
    "enchilada",
    "tortilla farcie",
    "calzone",
    "quiche",
    "quiches",
    "flammkuchen",
    "tarte flambee",
    "gyoza",
    "spring roll",
    "rouleau de printemps",
    "dumpling",
    "dumplings",
    "ravioles",
    "soupe",
    "veloute",
    "gaspacho",
    "potage",
    "bouillabaisse",
    "paella",
    "risotto",
    "tajine",
    "tagine",
    "couscous royal",
    "cassoulet",
    "blanquette",
    "bourguignon",
    "hachis parmentier",
    "parmentier",
    "tartiflette",
    "raclette plat",
    "fondue",
    "poke bowl",
    "buddha bowl",
    "ramen",
    "pho",
    "curry",
    "chili",
    "stew",
    "ragout",
    "ragout",
    "moussaka",
    "gratin",
    "pasta bake",
    "ready meal",
    "plat cuisine",
    "preparation culinaire",
    "trail mix",
    "melange aperitif",
    "feuillete",
    "vol au vent",
    "bouchee a la reine",
    "tourte",
    # Self-evident animal composites — no explicit anchor word but
    # the dish name itself implies meat.
    "blanquette",
    "bourguignon",
    "parmentier",
    "tartiflette",
    "choucroute",
    # Seafood self-evident composites.
    "fruits de mer",
    "frutti di mare",
    # Vegetarian self-evident composites.
    "margherita",
    "a la creme",
)


#: Self-evident animal composites — dishes whose names imply meat
#: even without an explicit FG1 anchor token. Used by
#: ``_composite_bucket_for`` to upgrade vegan → meat_based.
_WWF_SELF_EVIDENT_ANIMAL_COMPOSITES: tuple[str, ...] = (
    "cassoulet",
    "blanquette",
    "bourguignon",
    "bolognaise",
    "bolognese",
    "carbonara",
    "parmentier",
    "tartiflette",
    "choucroute",
    "amatriciana",
    "merguez",
    "hachis parmentier",
)


#: Self-evident seafood composites — dishes whose names imply
#: seafood. Used by ``_composite_bucket_for``.
_WWF_SELF_EVIDENT_SEAFOOD_COMPOSITES: tuple[str, ...] = (
    "fruits de mer",
    "frutti di mare",
    "bouillabaisse",
)


#: Self-evident vegetarian (contains cheese/cream/egg) composites.
_WWF_SELF_EVIDENT_VEGETARIAN_COMPOSITES: tuple[str, ...] = (
    "margherita",
    "quatre fromages",
    "4 fromages",
    "four cheese",
    "a la creme",
    "a la creme fraiche",
    "gratin dauphinois",
    "raclette",
    "tartiflette",  # also meat (lardons) but listed in animal too
    "fondue savoyarde",
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WWFGuardOverride:
    """Returned when a guard fires.

    The batch classifier uses this to substitute the corrected
    classification, clamp confidence to ≤ 0.69 (route to review),
    and emit a sample-error line with the rule id.
    """

    rule: str
    new_classification: WWFProductClassification


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_wwf_guards(
    product_name: str,
    classification: WWFProductClassification,
) -> WWFGuardOverride | None:
    """Apply the Phase WWF-D guards in priority order.

    Returns ``None`` when no guard fires (the caller keeps the
    original verdict). Returns a :class:`WWFGuardOverride`
    otherwise — the caller substitutes
    ``override.new_classification`` and marks the row for review.
    """
    name = _normalise(product_name)
    food_group = classification.wwf_food_group

    # Guard 1a — pet accessories → out_of_scope. Must run BEFORE
    # the dairy beverage guard so accessory names don't accidentally
    # match a food token.
    if _contains_any_word(name, _WWF_PET_ACCESSORY_TOKENS):
        if food_group is not WWFFoodGroup.OUT_OF_SCOPE:
            return _to_out_of_scope(
                classification,
                rule="wwf_pet_accessory_out_of_scope",
            )

    # Guard 1b — pet FOOD (in-scope, Altera decision). Generic
    # petfood → composite; petfood + animal anchor → FG1 subgroup;
    # petfood + plant-protein anchor → FG1 alt_protein.
    if _contains_any_word(name, _WWF_PETFOOD_TOKENS):
        if _contains_any_word(name, _WWF_FG1_ALT_PROTEIN_TOKENS):
            return _to_fg1(
                classification,
                WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
                rule="wwf_petfood_plant_protein",
            )
        if _contains_any_word(name, _WWF_FG1_SEAFOOD_TOKENS):
            return _to_composite_or_fg1(
                classification,
                WWFFG1Subgroup.SEAFOOD,
                bucket=WWFCompositeStep1Bucket.SEAFOOD_BASED,
                rule="wwf_petfood_seafood",
            )
        if _contains_any_word(
            name, _WWF_FG1_RED_MEAT_TOKENS
        ) or _contains_any_word(name, _WWF_FG1_POULTRY_TOKENS):
            return _to_composite_or_fg1(
                classification,
                WWFFG1Subgroup.RED_MEAT
                if _contains_any_word(name, _WWF_FG1_RED_MEAT_TOKENS)
                else WWFFG1Subgroup.POULTRY,
                bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                rule="wwf_petfood_animal",
            )
        # Generic petfood with no animal/plant anchor → composite
        # with low confidence.
        return _to_composite(
            classification,
            bucket=WWFCompositeStep1Bucket.MEAT_BASED,
            rule="wwf_petfood_generic_composite",
        )

    # Guard 2a — dairy / dairy-alternative beverages stay in FG2.
    # MUST run before the generic beverage exclusion below.
    if _contains_any_word(name, _WWF_PLANT_MILK_TOKENS):
        return _to_fg2_plant_dairy(
            classification, rule="wwf_plant_milk_substitute_fg2"
        )
    if _contains_any_word(name, _WWF_DAIRY_ANIMAL_BEVERAGE_TOKENS):
        return _to_fg2_dairy_animal_other(
            classification, rule="wwf_dairy_animal_beverage_fg2"
        )

    # Guard 3 — composite dish detection. MUST run before
    # ingredient-specific guards so "Pizza Margherita" / "Lasagnes
    # Bolognaise" / "Cassoulet" / "Quiche Fromage" route to
    # composite + correct bucket instead of FG1/FG2/FG3 single-
    # ingredient match.
    if _contains_any_word(name, _WWF_COMPOSITE_DISH_TOKENS):
        bucket = _composite_bucket_for(name)
        if (
            not classification.wwf_is_composite
            or classification.composite_step1_bucket is not bucket
        ):
            return _to_composite(
                classification,
                bucket=bucket,
                rule=f"wwf_composite_{bucket.value}",
            )

    # Guard 4 — FG7 snacks. MUST run before FG2 dairy and FG3 fats
    # so "Chocolat au Lait" / "Tablette Lait" / "Croissants au
    # Beurre" / "Sorbet Framboise" / "Confiture Abricot" land in
    # FG7 (snack) rather than FG2 (dairy) / FG3 (animal fat) /
    # FG4 (fruit).
    if _contains_any_word(name, _WWF_FG7_ANIMAL_SNACK_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG7
            or classification.fg7_snack_kind
            is not WWFFG7SnackKind.ANIMAL_BASED_SNACK
        ):
            return _to_fg7_animal_snack(
                classification, rule="wwf_fg7_animal_snack"
            )
    if _contains_any_word(name, _WWF_FG7_PLANT_SNACK_TOKENS):
        # Unsweetened compote / "sans sucre" — FG4 instead of FG7.
        # We skip the FG7 plant_snack guard so the FG4 guard
        # downstream can pick it up.
        unsweetened_exclusion = _contains_any_phrase(
            name,
            (
                "sans sucre",
                "sans sucres",
                "no sugar",
                "unsweetened",
                "sugar-free",
                "sugar free",
                "0% sucre",
            ),
        )
        if not unsweetened_exclusion and (
            food_group is not WWFFoodGroup.FG7
            or classification.fg7_snack_kind
            is not WWFFG7SnackKind.PLANT_BASED_SNACK
        ):
            return _to_fg7_plant_snack(
                classification, rule="wwf_fg7_plant_snack"
            )

    # Guard 5a — bouillon/stock/broth MUST be OOS BEFORE the FG1
    # priority guard, because "Bouillon Cube Volaille" contains the
    # poultry token "volaille" but is methodology-excluded.
    if _contains_any_phrase(
        name,
        (
            "bouillon",
            "stock cube",
            "stock powder",
            "broth",
            "fond de veau",
            "fond de volaille",
        ),
    ):
        if food_group is not WWFFoodGroup.OUT_OF_SCOPE:
            return _to_out_of_scope(
                classification, rule="wwf_scope_exclusion_bouillon"
            )

    # Phase WWF-M — FG3 animal-fat anchor MUST win over FG1 animal
    # anchors when both fire. Examples:
    #   "Duck fat"    → FG3 animal_fat (NOT FG1 poultry)
    #   "Goose fat"   → FG3 animal_fat (NOT FG1 poultry)
    #   "Tallow"      → FG3 animal_fat (the token is FG3-specific)
    # We do the check before the FG1-priority guard below.
    if _contains_any_word(name, _WWF_FG3_ANIMAL_FAT_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG3
            or classification.fg3_subgroup
            is not WWFFG3Subgroup.ANIMAL_BASED_FAT
        ):
            return _to_fg3_animal_fat(
                classification, rule="wwf_fg3_animal_fat"
            )

    # Phase WWF-M — FG2 dairy anchors win over FG1 red_meat for
    # "<animal>'s milk" / "<animal>'s yogurt" / "<animal>'s cheese".
    # The CSV ships "Cow's milk", "Sheep's milk", "Buffalo milk",
    # "Goat's milk", "Goat cheese" — all of which contain an FG1
    # animal token but are unambiguously FG2.
    if _contains_any_phrase(
        name,
        (
            "'s milk",
            "s milk",
            " milk",
            "milk powder",
            "powdered milk",
            "evaporated milk",
            "condensed milk",
            "'s yogurt",
            "'s yoghurt",
            "'s cheese",
            " cheese",
        ),
    ):
        if _contains_any_word(name, _WWF_FG2_OTHER_DAIRY_TOKENS):
            if (
                food_group is not WWFFoodGroup.FG2
                or classification.fg2_subgroup
                is not WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
            ):
                return _to_fg2_dairy_animal_other(
                    classification, rule="wwf_fg2_dairy_anchor_priority"
                )
        if _contains_any_word(name, _WWF_FG2_CHEESE_TOKENS):
            if (
                food_group is not WWFFoodGroup.FG2
                or classification.fg2_subgroup
                is not WWFFG2Subgroup.CHEESE
            ):
                return _to_fg2_cheese(
                    classification, rule="wwf_fg2_cheese_anchor_priority"
                )

    # Phase WWF-M — fruit/vegetable "salad" must win over the
    # composite "salad" detection so "Green salad" and "Mixed fruit
    # salads" land FG4 not FG1-composite.
    if _contains_any_phrase(
        name,
        (
            "green salad",
            "mixed salad",
            "garden salad",
            "fruit salad",
            "fruit salads",
            "salade de fruits",
            "salade verte",
        ),
    ):
        if food_group is not WWFFoodGroup.FG4:
            return _to_fg4(classification, rule="wwf_fg4_salad")

    # Phase WWF-M — "Plant-based <X> alternative/substitute" should
    # route by the X anchor when present (mince → FG1, butter → FG3
    # plant fat, etc.). Only when the X is a dairy term does
    # plant-based-cheese/yogurt/milk fire.
    if _contains_any_phrase(name, ("plant-based mince", "plant based mince")):
        return _to_fg1(
            classification,
            WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES,
            rule="wwf_plant_based_mince",
        )
    if _contains_any_phrase(
        name, ("plant-based butter", "plant based butter", "vegan butter")
    ):
        return _to_fg3_plant_fat(
            classification, rule="wwf_plant_based_butter"
        )

    # Guard 5 — FG1 priority before FG3 plant fat. Names like
    # "Sardines à l'Huile" / "Thon à l'huile" contain "huile" but
    # are FG1 seafood, not FG3.
    fg1_pair_early = _detect_fg1(name)
    if fg1_pair_early is not None and fg1_pair_early in (
        WWFFG1Subgroup.SEAFOOD,
        WWFFG1Subgroup.RED_MEAT,
        WWFFG1Subgroup.POULTRY,
        WWFFG1Subgroup.PROCESSED_MEATS_ALTERNATIVES,
        WWFFG1Subgroup.EGGS,
    ):
        if (
            food_group is not WWFFoodGroup.FG1
            or classification.fg1_subgroup is not fg1_pair_early
        ):
            return _to_fg1(
                classification,
                fg1_pair_early,
                rule=f"wwf_fg1_{fg1_pair_early.value}",
            )

    # Guard 6 — methodology exclusions.
    if (
        _contains_any_word(name, _WWF_NON_FOOD_TOKENS)
        or _contains_any_word(name, _WWF_OOS_BEVERAGE_TOKENS)
        or _contains_any_word(name, _WWF_OOS_CONDIMENT_TOKENS)
        or _contains_any_word(name, _WWF_OOS_HERB_SPICE_TOKENS)
        or _contains_any_word(name, _WWF_OOS_INGREDIENT_TOKENS)
        or _contains_any_word(name, _WWF_OOS_BABY_TOKENS)
        or _contains_any_word(name, _WWF_OOS_NOVEL_PROTEIN_TOKENS)
    ):
        if food_group is not WWFFoodGroup.OUT_OF_SCOPE:
            return _to_out_of_scope(
                classification, rule="wwf_scope_exclusion"
            )

    # Guard 7 — FG3 fat split (BUTTER IS FG3, NOT FG2). Must run
    # BEFORE FG2 dairy check so "Beurre Doux" doesn't end up as
    # FG2 cheese.
    if _contains_any_word(name, _WWF_FG3_ANIMAL_FAT_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG3
            or classification.fg3_subgroup
            is not WWFFG3Subgroup.ANIMAL_BASED_FAT
        ):
            return _to_fg3_animal_fat(
                classification, rule="wwf_fg3_animal_fat"
            )

    if _contains_any_word(name, _WWF_FG3_PLANT_FAT_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG3
            or classification.fg3_subgroup
            is not WWFFG3Subgroup.PLANT_BASED_FAT
        ):
            return _to_fg3_plant_fat(
                classification, rule="wwf_fg3_plant_fat"
            )

    # Guard 5 — FG2 dairy (cheese vs other vs plant).
    if _contains_any_word(name, _WWF_FG2_PLANT_DAIRY_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG2
            or classification.fg2_subgroup
            is not WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT
        ):
            return _to_fg2_plant_dairy(
                classification, rule="wwf_fg2_plant_dairy"
            )

    if _contains_any_word(name, _WWF_FG2_CHEESE_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG2
            or classification.fg2_subgroup is not WWFFG2Subgroup.CHEESE
        ):
            return _to_fg2_cheese(
                classification, rule="wwf_fg2_dairy_animal_cheese"
            )

    if _contains_any_word(name, _WWF_FG2_OTHER_DAIRY_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG2
            or classification.fg2_subgroup
            is not WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
        ):
            return _to_fg2_dairy_animal_other(
                classification, rule="wwf_fg2_dairy_animal_other"
            )

    # Guard 6 — FG6 tubers (must run before FG4 because some
    # tuber names contain vegetable-shaped substrings).
    if _contains_any_word(name, _WWF_FG6_TUBER_TOKENS):
        # ...unless fries/chips/wedges → FG7 plant_snack.
        if _contains_any_word(
            name,
            ("frite", "frites", "chips", "wedges", "rosti"),
        ):
            return _to_fg7_plant_snack(
                classification, rule="wwf_fries_fg7_plant_snack"
            )
        if food_group is not WWFFoodGroup.FG6:
            return _to_fg6(classification, rule="wwf_fg6_tubers")

    # Guard 7 — FG1 subgroup mapping.
    fg1_pair = _detect_fg1(name)
    if fg1_pair is not None:
        subgroup = fg1_pair
        if (
            food_group is not WWFFoodGroup.FG1
            or classification.fg1_subgroup is not subgroup
        ):
            return _to_fg1(
                classification, subgroup, rule=f"wwf_fg1_{subgroup.value}"
            )

    # Guard 7.5 — FG4 fruits/vegetables (sweetcorn, baby corn,
    # fruits in syrup, applesauce-without-sugar, generic fruits/
    # vegetables that the model may have mis-routed).
    if _contains_any_word(name, _WWF_FG4_TOKENS):
        # Applesauce with added sugar → FG7 plant snack (XLSX FAQ).
        if _contains_any_word(
            name, ("compote sucree", "compote avec sucre", "applesauce sweetened")
        ):
            return _to_fg7_plant_snack(
                classification,
                rule="wwf_fg7_sweetened_applesauce",
            )
        if food_group is not WWFFoodGroup.FG4:
            return _to_fg4(classification, rule="wwf_fg4_fruits_veg")

    # Guard 8 — FG5 grain split.
    if _contains_any_word(name, _WWF_FG5_WHOLEGRAIN_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG5
            or classification.fg5_grain_kind
            is not WWFFG5GrainKind.WHOLE_GRAIN
        ):
            return _to_fg5(
                classification,
                WWFFG5GrainKind.WHOLE_GRAIN,
                rule="wwf_fg5_whole_grain",
            )
    if _contains_any_word(name, _WWF_FG5_REFINED_DEFAULT_TOKENS):
        if (
            food_group is not WWFFoodGroup.FG5
            or classification.fg5_grain_kind
            is not WWFFG5GrainKind.REFINED_GRAIN
        ):
            return _to_fg5(
                classification,
                WWFFG5GrainKind.REFINED_GRAIN,
                rule="wwf_fg5_refined_grain",
            )

    return None


# ---------------------------------------------------------------------------
# Readable-name fallback (Phase WWF-D guard #11)
# ---------------------------------------------------------------------------


def classify_wwf_readable_fallback(
    product_name: str,
) -> tuple[
    WWFFoodGroup,
    bool,  # is_composite
    WWFFG1Subgroup | None,
    WWFFG2Subgroup | None,
    WWFFG3Subgroup | None,
    WWFFG5GrainKind | None,
    WWFFG7SnackKind | None,
    WWFCompositeStep1Bucket | None,
    str,  # rule_id
] | None:
    """Phase WWF-D — readable-name fallback for WWF.

    Returns a fully-formed tuple describing the best-guess WWF
    classification for a readable product name. The batch classifier
    builds a ``WWFProductClassification`` from this when the model
    returned ``unknown`` (or the row was about to land as
    parse-failed) and the name is not a placeholder.

    Returns ``None`` when no family matches — the caller falls back
    to the legacy parse-failed path.
    """
    name = _normalise(product_name)
    if not name.strip():
        return None

    # 1. Pet accessories → out_of_scope.
    if _contains_any_word(name, _WWF_PET_ACCESSORY_TOKENS):
        return _readable_oos("wwf_readable_fallback_pet_accessory")

    # 2. Pet FOOD (in-scope).
    if _contains_any_word(name, _WWF_PETFOOD_TOKENS):
        if _contains_any_word(name, _WWF_FG1_ALT_PROTEIN_TOKENS):
            return _readable_fg1(
                WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
                "wwf_readable_fallback_petfood_plant_protein",
            )
        if _contains_any_word(name, _WWF_FG1_SEAFOOD_TOKENS):
            return _readable_composite(
                WWFCompositeStep1Bucket.SEAFOOD_BASED,
                "wwf_readable_fallback_petfood_seafood",
            )
        if _contains_any_word(
            name, _WWF_FG1_RED_MEAT_TOKENS
        ) or _contains_any_word(name, _WWF_FG1_POULTRY_TOKENS):
            return _readable_composite(
                WWFCompositeStep1Bucket.MEAT_BASED,
                "wwf_readable_fallback_petfood_animal",
            )
        return _readable_composite(
            WWFCompositeStep1Bucket.MEAT_BASED,
            "wwf_readable_fallback_petfood_generic",
        )

    # 3. Dairy / plant-milk beverages → FG2.
    if _contains_any_word(name, _WWF_PLANT_MILK_TOKENS):
        return _readable_fg2_plant_dairy(
            "wwf_readable_fallback_plant_milk"
        )
    if _contains_any_word(name, _WWF_DAIRY_ANIMAL_BEVERAGE_TOKENS):
        return _readable_fg2_dairy_animal_other(
            "wwf_readable_fallback_dairy_beverage"
        )

    # 4. Methodology exclusions → out_of_scope.
    if (
        _contains_any_word(name, _WWF_NON_FOOD_TOKENS)
        or _contains_any_word(name, _WWF_OOS_BEVERAGE_TOKENS)
        or _contains_any_word(name, _WWF_OOS_CONDIMENT_TOKENS)
        or _contains_any_word(name, _WWF_OOS_HERB_SPICE_TOKENS)
        or _contains_any_word(name, _WWF_OOS_INGREDIENT_TOKENS)
        or _contains_any_word(name, _WWF_OOS_BABY_TOKENS)
        or _contains_any_word(name, _WWF_OOS_NOVEL_PROTEIN_TOKENS)
    ):
        return _readable_oos("wwf_readable_fallback_oos")

    # 5. FG3 fats (butter is FG3, NOT FG2).
    if _contains_any_word(name, _WWF_FG3_ANIMAL_FAT_TOKENS):
        return _readable_fg3(
            WWFFG3Subgroup.ANIMAL_BASED_FAT,
            "wwf_readable_fallback_fg3_animal_fat",
        )
    if _contains_any_word(name, _WWF_FG3_PLANT_FAT_TOKENS):
        return _readable_fg3(
            WWFFG3Subgroup.PLANT_BASED_FAT,
            "wwf_readable_fallback_fg3_plant_fat",
        )

    # 6. Sweet bakery → composite (vegetarian default) or FG7
    # animal_snack depending on tokens.
    if _contains_any_word(name, _WWF_FG7_ANIMAL_SNACK_TOKENS):
        return _readable_fg7_animal_snack(
            "wwf_readable_fallback_fg7_animal_snack"
        )

    # 7. Composite dishes.
    if _contains_any_word(name, _WWF_COMPOSITE_DISH_TOKENS):
        bucket = _composite_bucket_for(name)
        return _readable_composite(
            bucket, f"wwf_readable_fallback_composite_{bucket.value}"
        )

    # 8. FG6 tubers (but fries → FG7 plant_snack).
    if _contains_any_word(name, _WWF_FG6_TUBER_TOKENS):
        if _contains_any_word(
            name,
            ("frite", "frites", "chips", "wedges", "rosti"),
        ):
            return _readable_fg7_plant_snack(
                "wwf_readable_fallback_fg7_fries"
            )
        return _readable_fg6("wwf_readable_fallback_fg6_tubers")

    # 9. FG7 plant snacks (chips/sorbet/sugary spreads).
    if _contains_any_word(name, _WWF_FG7_PLANT_SNACK_TOKENS):
        return _readable_fg7_plant_snack(
            "wwf_readable_fallback_fg7_plant_snack"
        )

    # 10. FG2 dairy.
    if _contains_any_word(name, _WWF_FG2_PLANT_DAIRY_TOKENS):
        return _readable_fg2_plant_dairy(
            "wwf_readable_fallback_fg2_plant_dairy"
        )
    if _contains_any_word(name, _WWF_FG2_CHEESE_TOKENS):
        return _readable_fg2_cheese(
            "wwf_readable_fallback_fg2_cheese"
        )
    if _contains_any_word(name, _WWF_FG2_OTHER_DAIRY_TOKENS):
        return _readable_fg2_dairy_animal_other(
            "wwf_readable_fallback_fg2_other_dairy"
        )

    # 11. FG1 protein sources.
    fg1 = _detect_fg1(name)
    if fg1 is not None:
        return _readable_fg1(
            fg1, f"wwf_readable_fallback_fg1_{fg1.value}"
        )

    # 12. FG5 grains.
    if _contains_any_word(name, _WWF_FG5_WHOLEGRAIN_TOKENS):
        return _readable_fg5(
            WWFFG5GrainKind.WHOLE_GRAIN,
            "wwf_readable_fallback_fg5_whole_grain",
        )
    if _contains_any_word(name, _WWF_FG5_REFINED_DEFAULT_TOKENS):
        return _readable_fg5(
            WWFFG5GrainKind.REFINED_GRAIN,
            "wwf_readable_fallback_fg5_refined_grain",
        )

    return None


# ---------------------------------------------------------------------------
# Internal helpers — guard overrides
# ---------------------------------------------------------------------------


def _detect_fg1(name: str) -> WWFFG1Subgroup | None:
    """Return the most specific FG1 subgroup for ``name``, or None."""
    # Order matters — meat alternatives must come BEFORE plain
    # meat tokens (a "burger végétal" mustn't be detected as
    # processed_meats).
    if _contains_any_word(name, _WWF_FG1_MEAT_ALT_TOKENS):
        return WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES
    if _contains_any_word(name, _WWF_FG1_ALT_PROTEIN_TOKENS):
        return WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES
    if _contains_any_word(name, _WWF_FG1_LEGUME_TOKENS):
        return WWFFG1Subgroup.LEGUMES
    if _contains_any_word(name, _WWF_FG1_NUTS_SEEDS_TOKENS):
        return WWFFG1Subgroup.NUTS_SEEDS
    if _contains_any_word(name, _WWF_FG1_PROCESSED_MEAT_TOKENS):
        return WWFFG1Subgroup.PROCESSED_MEATS_ALTERNATIVES
    if _contains_any_word(name, _WWF_FG1_SEAFOOD_TOKENS):
        return WWFFG1Subgroup.SEAFOOD
    if _contains_any_word(name, _WWF_FG1_POULTRY_TOKENS):
        return WWFFG1Subgroup.POULTRY
    if _contains_any_word(name, _WWF_FG1_RED_MEAT_TOKENS):
        return WWFFG1Subgroup.RED_MEAT
    if _contains_any_word(name, _WWF_FG1_EGG_TOKENS):
        return WWFFG1Subgroup.EGGS
    return None


def _composite_bucket_for(name: str) -> WWFCompositeStep1Bucket:
    """Composite Step 1 bucket precedence (meat → seafood →
    vegetarian → vegan)."""
    if (
        _contains_any_word(name, _WWF_FG1_RED_MEAT_TOKENS)
        or _contains_any_word(name, _WWF_FG1_POULTRY_TOKENS)
        or _contains_any_word(name, _WWF_FG1_PROCESSED_MEAT_TOKENS)
        or _contains_any_phrase(name, _WWF_SELF_EVIDENT_ANIMAL_COMPOSITES)
    ):
        return WWFCompositeStep1Bucket.MEAT_BASED
    if _contains_any_word(
        name, _WWF_FG1_SEAFOOD_TOKENS
    ) or _contains_any_phrase(name, _WWF_SELF_EVIDENT_SEAFOOD_COMPOSITES):
        return WWFCompositeStep1Bucket.SEAFOOD_BASED
    if (
        _contains_any_word(name, _WWF_FG1_EGG_TOKENS)
        or _contains_any_word(name, _WWF_FG2_CHEESE_TOKENS)
        or _contains_any_word(name, _WWF_FG2_OTHER_DAIRY_TOKENS)
        or _contains_any_word(name, _WWF_FG3_ANIMAL_FAT_TOKENS)
        or _contains_any_phrase(name, _WWF_SELF_EVIDENT_VEGETARIAN_COMPOSITES)
    ):
        return WWFCompositeStep1Bucket.VEGETARIAN
    return WWFCompositeStep1Bucket.VEGAN


def _replace(
    original: WWFProductClassification,
    *,
    food_group: WWFFoodGroup,
    is_composite: bool = False,
    fg1: WWFFG1Subgroup | None = None,
    fg2: WWFFG2Subgroup | None = None,
    fg3: WWFFG3Subgroup | None = None,
    fg5: WWFFG5GrainKind | None = None,
    fg7: WWFFG7SnackKind | None = None,
    bucket: WWFCompositeStep1Bucket | None = None,
) -> WWFProductClassification:
    """Build a corrected classification with confidence clamped to
    ≤ 0.69 so the row routes to review."""
    new_confidence = min(original.confidence, _GUARD_CONFIDENCE_CEILING)
    return original.model_copy(
        update={
            "wwf_food_group": food_group,
            "wwf_is_composite": is_composite,
            "fg1_subgroup": fg1,
            "fg2_subgroup": fg2,
            "fg3_subgroup": fg3,
            "fg5_grain_kind": fg5,
            "fg7_snack_kind": fg7,
            "composite_step1_bucket": bucket,
            "confidence": new_confidence,
        }
    )


def _to_out_of_scope(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls, food_group=WWFFoodGroup.OUT_OF_SCOPE
        ),
    )


def _to_fg1(
    cls: WWFProductClassification,
    subgroup: WWFFG1Subgroup,
    *,
    rule: str,
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls, food_group=WWFFoodGroup.FG1, fg1=subgroup
        ),
    )


def _to_fg2_cheese(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls, food_group=WWFFoodGroup.FG2, fg2=WWFFG2Subgroup.CHEESE
        ),
    )


def _to_fg2_dairy_animal_other(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG2,
            fg2=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL,
        ),
    )


def _to_fg2_plant_dairy(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG2,
            fg2=WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT,
        ),
    )


def _to_fg3_animal_fat(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG3,
            fg3=WWFFG3Subgroup.ANIMAL_BASED_FAT,
        ),
    )


def _to_fg3_plant_fat(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG3,
            fg3=WWFFG3Subgroup.PLANT_BASED_FAT,
        ),
    )


def _to_fg4(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(cls, food_group=WWFFoodGroup.FG4),
    )


def _to_fg5(
    cls: WWFProductClassification,
    kind: WWFFG5GrainKind,
    *,
    rule: str,
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls, food_group=WWFFoodGroup.FG5, fg5=kind
        ),
    )


def _to_fg6(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(cls, food_group=WWFFoodGroup.FG6),
    )


def _to_fg7_plant_snack(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG7,
            fg7=WWFFG7SnackKind.PLANT_BASED_SNACK,
        ),
    )


def _to_fg7_animal_snack(
    cls: WWFProductClassification, *, rule: str
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG7,
            fg7=WWFFG7SnackKind.ANIMAL_BASED_SNACK,
        ),
    )


def _to_composite(
    cls: WWFProductClassification,
    *,
    bucket: WWFCompositeStep1Bucket,
    rule: str,
) -> WWFGuardOverride:
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=cls.wwf_food_group
            if cls.wwf_food_group.is_methodology_group
            else WWFFoodGroup.FG1,
            is_composite=True,
            bucket=bucket,
        ),
    )


def _to_composite_or_fg1(
    cls: WWFProductClassification,
    fg1: WWFFG1Subgroup,
    *,
    bucket: WWFCompositeStep1Bucket,
    rule: str,
) -> WWFGuardOverride:
    """Petfood with an animal anchor — composite with explicit bucket
    AND FG1 subgroup. We default to composite because pet food is
    nearly always mixed animal+cereal."""
    return WWFGuardOverride(
        rule=rule,
        new_classification=_replace(
            cls,
            food_group=WWFFoodGroup.FG1,
            fg1=fg1,
            is_composite=True,
            bucket=bucket,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers — readable fallback tuples
# ---------------------------------------------------------------------------


def _readable_oos(rule: str) -> tuple:
    return (
        WWFFoodGroup.OUT_OF_SCOPE,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg1(subgroup: WWFFG1Subgroup, rule: str) -> tuple:
    return (
        WWFFoodGroup.FG1,
        False,
        subgroup,
        None,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg2_cheese(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG2,
        False,
        None,
        WWFFG2Subgroup.CHEESE,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg2_dairy_animal_other(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG2,
        False,
        None,
        WWFFG2Subgroup.OTHER_DAIRY_ANIMAL,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg2_plant_dairy(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG2,
        False,
        None,
        WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg3(subgroup: WWFFG3Subgroup, rule: str) -> tuple:
    return (
        WWFFoodGroup.FG3,
        False,
        None,
        None,
        subgroup,
        None,
        None,
        None,
        rule,
    )


def _readable_fg5(kind: WWFFG5GrainKind, rule: str) -> tuple:
    return (
        WWFFoodGroup.FG5,
        False,
        None,
        None,
        None,
        kind,
        None,
        None,
        rule,
    )


def _readable_fg6(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG6,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        rule,
    )


def _readable_fg7_plant_snack(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG7,
        False,
        None,
        None,
        None,
        None,
        WWFFG7SnackKind.PLANT_BASED_SNACK,
        None,
        rule,
    )


def _readable_fg7_animal_snack(rule: str) -> tuple:
    return (
        WWFFoodGroup.FG7,
        False,
        None,
        None,
        None,
        None,
        WWFFG7SnackKind.ANIMAL_BASED_SNACK,
        None,
        rule,
    )


def _readable_composite(
    bucket: WWFCompositeStep1Bucket, rule: str
) -> tuple:
    # Composites attach to FG1 by convention (where the animal/
    # plant protein lives) — the dashboard groups them by bucket
    # anyway.
    return (
        WWFFoodGroup.FG1,
        True,
        None,
        None,
        None,
        None,
        None,
        bucket,
        rule,
    )

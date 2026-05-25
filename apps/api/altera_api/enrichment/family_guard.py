"""Phase 34V — Product family compatibility guard for NEVO matching.

Production data showed NEVO's fuzzy-token matcher producing high-
coverage but low-precision matches: 81% coverage with only ~40%
precision. Bad matches included:

  Corn Flakes Nature        → Chicken schnitzel breaded w corn flakes
  Thon Entier               → Crispbread Cracottes naturel
  Vinaigre de Cidre         → Brawn, pork pickled in vinegar
  Margarine Doux            → Egg whole chicken fried in margarine
  Blanc de Poulet           → Egg white chicken raw
  Madeleines                → Coquilles scallop shell
  Crème fraîche             → Crackers cream
  Nettoyant Multi-Usages    → Rice multi-grain raw
  Essuie-Tout               → Mange-tout raw

The common pattern: the retailer and NEVO candidate share an
isolated word ("corn flakes", "vinegar", "cream", "tout") but live
in completely different food families. Inserting these as nutrition
references is *worse than missing data* because the calculation
proceeds on wrong numbers.

This module:

1. Defines a coarse :class:`FoodFamily` enum.
2. Provides :func:`product_family` — a keyword-based classifier that
   buckets a product name into one of those families.
3. Provides :func:`is_family_compatible` — the rule that decides
   whether a retailer-product family and a NEVO-candidate family are
   close enough to accept the match.

The guard is intentionally simple (substring matching against a
curated French/English vocabulary) so it's auditable, fast, and
fully testable. A future iteration can layer an embedding-based
similarity check on top, but the keyword guard alone catches the
worst false positives.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache


class FoodFamily(StrEnum):
    """Coarse product family. Used for NEVO match compatibility.

    Members are deliberately broader than the Protein Tracker
    taxonomy — they describe what kind of *food* the product is,
    not how the methodology buckets its protein.
    """

    NON_FOOD = "non_food"
    BEVERAGE = "beverage"  # water, soda, coffee, tea, alcohol
    MEAT = "meat"          # beef, pork, poultry, processed meat
    FISH = "fish"          # fish, shellfish, seafood
    EGG = "egg"
    DAIRY = "dairy"        # milk, cheese, yoghurt, butter, cream
    LEGUME = "legume"      # lentils, chickpeas, beans, peas
    NUTS_SEEDS = "nuts_seeds"
    TOFU_SUBSTITUTE = "tofu_substitute"  # tofu, tempeh, plant meat
    CEREAL_BREAD_PASTA = "cereal_bread_pasta"
    FRUIT_VEG = "fruit_veg"
    OIL_FAT = "oil_fat"
    SWEET_BAKERY = "sweet_bakery"  # chocolate, biscuits, cakes, ice cream
    CONDIMENT = "condiment"  # salt, sugar, spices, vinegar, plain sauces
    PREPARED_MEAL = "prepared_meal"  # pizza, lasagne, quiche, ready meal
    UNKNOWN_FOOD = "unknown_food"


# ---------------------------------------------------------------------------
# Keyword vocabularies (French first, English second).
#
# Order matters: the classifier walks families in priority order and
# returns the first hit. Strong signals like NON_FOOD must be checked
# BEFORE softer ones (a hygiene product whose name contains "fraise"
# should still come out as NON_FOOD).
# ---------------------------------------------------------------------------

#: Tokens that mark a product as clearly non-food. Match-anywhere
#: substring. We deliberately include single common words like
#: "lessive" / "papier" / "couches" because those alone are decisive.
_NON_FOOD_TOKENS: tuple[str, ...] = (
    # FR hygiene / household
    "lessive", "adoucissant", "javel", "dentifrice", "brosse à dents",
    "shampooing", "shampoing", "gel douche", "savon",
    "papier toilette", "papier-toilette", "essuie-tout", "essuie tout",
    "papier", "couches", "couche-culotte", "tampon", "serviette hygiénique",
    "nettoyant", "détergent", "detergent",
    "désodorisant", "deodorant", "déodorant",
    "mouchoir", "coton", "rasoir", "razor",
    "litière", "litiere",
    # Pet food
    "croquettes", "pâtée chien", "patee chien", "pâtée chat", "patee chat",
    "nourriture chien", "nourriture chat",
    "petfood", "pet food",
    # Misc non-food
    "pile", "ampoule", "jouet", "lampe",
    # EN
    "detergent", "soap", "toothpaste", "shampoo", "battery", "battery",
    "pet food", "cat food", "dog food", "toilet paper", "diaper",
)

#: Beverages that contribute no meaningful protein under current
#: Protein Tracker scope. Water, soda, alcohol, hot drinks.
_BEVERAGE_TOKENS: tuple[str, ...] = (
    "eau plate", "eau gazeuse", "eau minérale", "eau minerale",
    "eau de source", "eau",
    "coca", "soda", "limonade", "soft drink",
    "café", "cafe", "thé", "the ", "tisane", "infusion",
    "vin", "champagne", "bière", "biere", "spiritueux", "whisky",
    "vodka", "rhum", "gin", "alcool",
    "jus" + " ",  # we handle jus carefully below
    "water", "coffee", "wine", "beer",
)

_MEAT_TOKENS: tuple[str, ...] = (
    "poulet", "dinde", "canard", "pintade", "volaille",
    "bœuf", "boeuf", "veau", "porc", "agneau", "mouton", "gibier",
    "lapin", "cheval",
    "viande", "carne",
    "jambon", "saucisse", "saucisson", "chorizo", "lard", "lardon",
    "bacon", "andouille", "boudin", "charcuterie", "rillette",
    "pâté", "pate de campagne", "pâté de campagne",
    "merguez", "kebab", "rôti", "roti",
    "steak", "filet mignon", "cordon bleu", "nuggets de poulet",
    # EN
    "beef", "pork", "lamb", "chicken", "turkey", "duck", "veal",
    "ham", "bacon", "sausage", "meat",
)

_FISH_TOKENS: tuple[str, ...] = (
    "saumon", "thon", "cabillaud", "merlu", "lieu", "sardine",
    "maquereau", "hareng", "anchois", "truite", "bar", "dorade",
    "morue", "sole", "lotte",
    "crevette", "moule", "huître", "huitre", "coquille saint-jacques",
    "homard", "langoustine", "crabe", "calmar", "encornet",
    "poisson", "fruits de mer", "fruits-de-mer",
    "tarama", "surimi",
    # EN
    "salmon", "tuna", "cod", "shrimp", "prawn", "fish",
    "seafood", "oyster", "mussel",
)

_EGG_TOKENS: tuple[str, ...] = (
    "œuf", "oeuf", "œufs", "oeufs",
    # EN
    "egg", "eggs", "egg white", "egg yolk",
)

_DAIRY_TOKENS: tuple[str, ...] = (
    "lait demi-écrémé", "lait écrémé", "lait entier", "lait demi écrémé",
    "lait",
    "fromage blanc", "fromage frais", "fromage râpé", "fromage rape",
    "fromage",
    "yaourt", "yoghourt", "skyr", "fromage à tartiner",
    "camembert", "brie", "comté", "comte", "gruyère", "gruyere",
    "emmental", "mozzarella", "feta", "chèvre", "chevre",
    "raclette", "reblochon", "munster", "roquefort",
    "beurre demi-sel", "beurre doux", "beurre",
    "crème fraîche", "creme fraiche", "crème liquide", "creme liquide",
    "crème", "creme",
    # EN
    "milk", "cheese", "yogurt", "yoghurt", "butter", "cream",
)

_LEGUME_TOKENS: tuple[str, ...] = (
    "lentille", "pois chiche", "pois cassé", "pois casse",
    "haricot rouge", "haricot blanc", "haricot noir", "haricot",
    "fève", "feve", "flageolet", "edamame",
    # EN
    "lentil", "chickpea", "kidney bean", "black bean", "white bean",
    "bean",
)

_NUTS_SEEDS_TOKENS: tuple[str, ...] = (
    "amande", "noisette", "noix de cajou", "cajou", "noix de pécan",
    "pecan", "pistache", "macadamia", "noix du brésil", "noix",
    "graine de tournesol", "graine de courge", "graine de chia",
    "graine de lin", "graine", "sésame", "sesame", "lin",
    # EN
    "almond", "cashew", "walnut", "hazelnut", "pistachio",
    "peanut", "seed", "sesame", "chia",
)

_TOFU_SUBSTITUTE_TOKENS: tuple[str, ...] = (
    "tofu", "tempeh", "seitan",
    "steak végétal", "steak vegetal",
    "burger végétal", "burger vegetal",
    "nuggets végétaux", "nuggets vegetaux",
    "poulet végétal", "poulet vegetal",
    "boisson soja", "boisson avoine", "boisson amande", "boisson riz",
    "lait d'amande", "lait d amande", "lait de soja", "lait d'avoine",
    "lait de coco",
    "yaourt soja", "yaourt végétal", "yaourt vegetal",
    "fromage végétal", "fromage vegetal", "vegan cheese",
    "soja",
    # EN
    "plant-based", "vegan", "vegetarian", "meatless",
    "soy", "oat milk", "almond milk", "soy milk",
)

_CEREAL_BREAD_PASTA_TOKENS: tuple[str, ...] = (
    "pain", "baguette", "pain de mie", "pain complet", "pain au lait",
    "biscotte", "cracotte", "crispbread",
    "pâte", "pâtes", "pates", "spaghetti", "tagliatelle", "macaroni",
    "ravioli", "tortellini", "lasagne", "lasagnes",
    "riz", "basmati", "thaï", "thai", "complet",
    "couscous", "boulgour", "quinoa", "épeautre", "epautre",
    "blé", "blé dur", "ble", "orge", "avoine", "muesli", "céréales",
    "cereales", "céréale", "cereale", "corn flakes", "cornflakes",
    "flocons", "granola",
    "farine", "semoule",
    # EN
    "bread", "pasta", "rice", "wheat", "cereal", "flour", "noodle",
    "oat", "barley", "rye",
)

_FRUIT_VEG_TOKENS: tuple[str, ...] = (
    "pomme", "poire", "banane", "fraise", "framboise", "myrtille",
    "orange", "clémentine", "clementine", "mandarine", "citron",
    "raisin", "abricot", "pêche", "peche", "kiwi", "mangue", "ananas",
    "melon", "pastèque", "pasteque", "cerise", "prune",
    "carotte", "tomate", "concombre", "courgette", "aubergine",
    "poivron", "champignon", "oignon", "ail",
    "salade", "épinard", "epinard", "haricot vert", "petit pois",
    "brocoli", "chou", "chou-fleur", "chou fleur", "asperge",
    "betterave", "radis", "navet", "céleri", "celeri",
    "pomme de terre", "patate", "patate douce", "manioc",
    "mange-tout", "mange tout",
    # EN
    "apple", "carrot", "tomato", "onion", "potato", "salad", "fruit",
    "vegetable", "mushroom", "broccoli", "cabbage",
)

_OIL_FAT_TOKENS: tuple[str, ...] = (
    "huile d'olive", "huile d olive", "huile de tournesol",
    "huile de colza", "huile d'arachide", "huile de coco", "huile",
    "margarine",
    # EN
    "oil", "olive oil",
)

_SWEET_BAKERY_TOKENS: tuple[str, ...] = (
    "chocolat", "praline", "pralin", "ganache",
    "biscuit", "cookie", "gâteau", "gateau", "madeleine",
    "tarte", "tartelette", "éclair", "eclair", "religieuse",
    "viennoiserie", "brioche", "croissant", "pain au chocolat",
    "pain aux raisins", "kouign-amann",
    "glace", "sorbet", "crème glacée", "creme glacee",
    "bonbon", "confiserie", "nougat", "caramel", "barre",
    "pâtisserie", "patisserie",
    "blinis",
    # EN
    "chocolate", "biscuit", "cake", "ice cream", "candy",
)

_CONDIMENT_TOKENS: tuple[str, ...] = (
    "sel", "fleur de sel", "poivre", "sucre", "miel", "sirop",
    "confiture", "marmelade", "gelée", "gelee",
    "vinaigre", "moutarde", "ketchup", "mayonnaise", "vinaigrette",
    "sauce", "épice", "épices", "epice", "epices",
    "thym", "romarin", "basilic", "persil", "ciboulette",
    "bouillon",
    # EN
    "salt", "pepper", "sugar", "honey", "jam", "vinegar",
    "mustard", "ketchup", "mayonnaise", "spice", "broth",
)

_PREPARED_MEAL_TOKENS: tuple[str, ...] = (
    "pizza", "quiche", "lasagne", "lasagnes", "gratin",
    "sandwich", "wrap", "kebab", "sushi",
    "salade composée", "salade composee",
    "soupe", "potage", "velouté", "veloute", "minestrone",
    "tajine", "couscous royal", "paella", "risotto",
    "plat préparé", "plat prepare", "plat cuisiné", "plat cuisine",
    # EN
    "pizza", "quiche", "lasagna", "soup", "sandwich",
)


#: Ordered list of (FoodFamily, tokens) — first match wins. NON_FOOD
#: and BEVERAGE come first so hygiene/petfood/drinks aren't shadowed
#: by an incidental food token.
_FAMILY_RULES: tuple[tuple[FoodFamily, tuple[str, ...]], ...] = (
    (FoodFamily.NON_FOOD, _NON_FOOD_TOKENS),
    (FoodFamily.BEVERAGE, _BEVERAGE_TOKENS),
    # Composite + prepared dishes BEFORE single-ingredient families so
    # "salade poulet césar" is detected as prepared, not meat.
    (FoodFamily.PREPARED_MEAL, _PREPARED_MEAL_TOKENS),
    (FoodFamily.SWEET_BAKERY, _SWEET_BAKERY_TOKENS),
    (FoodFamily.TOFU_SUBSTITUTE, _TOFU_SUBSTITUTE_TOKENS),
    (FoodFamily.MEAT, _MEAT_TOKENS),
    (FoodFamily.FISH, _FISH_TOKENS),
    (FoodFamily.EGG, _EGG_TOKENS),
    (FoodFamily.DAIRY, _DAIRY_TOKENS),
    (FoodFamily.LEGUME, _LEGUME_TOKENS),
    (FoodFamily.NUTS_SEEDS, _NUTS_SEEDS_TOKENS),
    (FoodFamily.CEREAL_BREAD_PASTA, _CEREAL_BREAD_PASTA_TOKENS),
    (FoodFamily.OIL_FAT, _OIL_FAT_TOKENS),
    (FoodFamily.FRUIT_VEG, _FRUIT_VEG_TOKENS),
    (FoodFamily.CONDIMENT, _CONDIMENT_TOKENS),
)


@lru_cache(maxsize=1)
def _compiled_rules() -> tuple[tuple[FoodFamily, re.Pattern[str]], ...]:
    """Compile family rules to a regex with word-boundary anchors.

    Substring matching alone was too permissive — "vin" matched
    "vinaigre", "the" (tea) matched "leather", "soja" matched
    "sojaproducts"-style brand prefixes. Word-boundary regex
    eliminates those false positives while staying fast (one
    compiled regex per family, alternation).
    """
    compiled: list[tuple[FoodFamily, re.Pattern[str]]] = []
    for family, tokens in _FAMILY_RULES:
        # Sort by length descending so longer phrases win over their
        # prefixes ("pâté de campagne" before "pâté").
        escaped = sorted(
            (re.escape(t.lower()) for t in tokens),
            key=lambda s: (-len(s), s),
        )
        # Use lookarounds (not \b) because \b doesn't fire on
        # accented characters or hyphenated tokens in Python's re.
        # We treat a "word" as anything not in [a-z0-9éèêëàâäîïôöùûüç].
        # Outside the token, the next char must NOT be a word char.
        # Allow an optional plural 's' so "pomme" matches "pommes",
        # "lentille" matches "lentilles", etc.
        pattern = (
            r"(?<![a-z0-9éèêëàâäîïôöùûüçœ])(?:"
            + "|".join(escaped)
            + r")s?(?![a-z0-9éèêëàâäîïôöùûüçœ])"
        )
        compiled.append((family, re.compile(pattern)))
    return tuple(compiled)


def product_family(name: str | None) -> FoodFamily:
    """Bucket a product name into one of the FoodFamily values.

    Walks ``_FAMILY_RULES`` in declaration order and returns the
    first regex-match. Word-boundary regex ensures "vin" inside
    "vinaigre" doesn't trigger BEVERAGE.

    Returns ``UNKNOWN_FOOD`` if no rule matches.
    """
    if not name:
        return FoodFamily.UNKNOWN_FOOD
    lowered = name.lower()
    for family, regex in _compiled_rules():
        if regex.search(lowered):
            return family
    return FoodFamily.UNKNOWN_FOOD


#: NEVO-side food_group labels are coarse English category names
#: like "Vegetables", "Meat, poultry & seafood", "Bread, breakfast
#: cereals, etc.", "Dairy products". We map those to FoodFamily for
#: a symmetric compatibility check.
_NEVO_GROUP_KEYWORDS: tuple[tuple[FoodFamily, tuple[str, ...]], ...] = (
    (
        FoodFamily.NON_FOOD,
        ("non-food", "non food", "household", "hygiene"),
    ),
    (
        FoodFamily.BEVERAGE,
        (
            "beverage", "drink", "water", "coffee", "tea",
            "alcohol", "soft drink",
        ),
    ),
    (
        FoodFamily.MEAT,
        ("meat", "poultry", "processed meat", "sausage", "charcuterie"),
    ),
    (
        FoodFamily.FISH,
        ("fish", "seafood", "shellfish"),
    ),
    (
        FoodFamily.EGG,
        ("egg",),
    ),
    (
        FoodFamily.DAIRY,
        ("dairy", "milk", "cheese", "yogurt", "yoghurt", "butter"),
    ),
    (
        FoodFamily.LEGUME,
        ("legume", "pulse", "lentil", "bean", "chickpea"),
    ),
    (
        FoodFamily.NUTS_SEEDS,
        ("nut", "seed"),
    ),
    (
        FoodFamily.TOFU_SUBSTITUTE,
        ("tofu", "tempeh", "soy", "plant-based", "vegan"),
    ),
    (
        FoodFamily.CEREAL_BREAD_PASTA,
        (
            "bread", "cereal", "pasta", "rice", "flour",
            "breakfast", "crispbread",
        ),
    ),
    (
        FoodFamily.OIL_FAT,
        ("oil", "fat", "margarine"),
    ),
    (
        FoodFamily.FRUIT_VEG,
        ("fruit", "vegetable"),
    ),
    (
        FoodFamily.SWEET_BAKERY,
        ("sugar", "sweet", "biscuit", "cake", "chocolate", "candy", "dessert"),
    ),
    (
        FoodFamily.CONDIMENT,
        ("condiment", "sauce", "spice", "herb", "vinegar"),
    ),
    (
        FoodFamily.PREPARED_MEAL,
        ("composite", "prepared", "ready-to-eat", "dish"),
    ),
)


def nevo_candidate_family(food_group: str | None, food_name: str | None) -> FoodFamily:
    """Bucket a NEVO candidate by its food_group + name into a FoodFamily.

    NEVO 2025's food_group column is a short English label. We first
    try to map that, then fall back to ``product_family`` on the
    English/Dutch name in case the food_group is ambiguous.
    """
    if food_group:
        lowered = food_group.lower()
        for family, tokens in _NEVO_GROUP_KEYWORDS:
            for token in tokens:
                if token in lowered:
                    return family
    # Fall back to a name-based heuristic.
    return product_family(food_name)


#: Compatibility matrix. Two families are compatible when the right
#: column contains the left family. The matrix is intentionally
#: conservative — we only accept matches that are clearly in the
#: same food family, plus a few documented adjacencies (a plant
#: substitute can match its target animal family if the names align;
#: nuts/seeds can match legumes since they're often pooled in NEVO's
#: protein-source group).
_COMPATIBLE: dict[FoodFamily, frozenset[FoodFamily]] = {
    FoodFamily.NON_FOOD: frozenset({FoodFamily.NON_FOOD}),
    FoodFamily.BEVERAGE: frozenset({FoodFamily.BEVERAGE}),
    FoodFamily.MEAT: frozenset({FoodFamily.MEAT, FoodFamily.PREPARED_MEAL}),
    FoodFamily.FISH: frozenset({FoodFamily.FISH, FoodFamily.PREPARED_MEAL}),
    FoodFamily.EGG: frozenset(
        {FoodFamily.EGG, FoodFamily.PREPARED_MEAL}
    ),
    FoodFamily.DAIRY: frozenset(
        {FoodFamily.DAIRY, FoodFamily.SWEET_BAKERY}
    ),
    FoodFamily.LEGUME: frozenset(
        {FoodFamily.LEGUME, FoodFamily.NUTS_SEEDS}
    ),
    FoodFamily.NUTS_SEEDS: frozenset(
        {FoodFamily.NUTS_SEEDS, FoodFamily.LEGUME}
    ),
    FoodFamily.TOFU_SUBSTITUTE: frozenset(
        {
            FoodFamily.TOFU_SUBSTITUTE,
            FoodFamily.LEGUME,
            FoodFamily.DAIRY,  # plant milk often grouped with dairy in NEVO
        }
    ),
    FoodFamily.CEREAL_BREAD_PASTA: frozenset(
        {FoodFamily.CEREAL_BREAD_PASTA, FoodFamily.SWEET_BAKERY}
    ),
    FoodFamily.FRUIT_VEG: frozenset(
        {FoodFamily.FRUIT_VEG, FoodFamily.PREPARED_MEAL}
    ),
    FoodFamily.OIL_FAT: frozenset({FoodFamily.OIL_FAT}),
    FoodFamily.SWEET_BAKERY: frozenset(
        {
            FoodFamily.SWEET_BAKERY,
            FoodFamily.DAIRY,
            FoodFamily.CEREAL_BREAD_PASTA,
        }
    ),
    FoodFamily.CONDIMENT: frozenset({FoodFamily.CONDIMENT}),
    FoodFamily.PREPARED_MEAL: frozenset(
        {
            FoodFamily.PREPARED_MEAL,
            FoodFamily.MEAT,
            FoodFamily.FISH,
            FoodFamily.EGG,
            FoodFamily.DAIRY,
            FoodFamily.CEREAL_BREAD_PASTA,
            FoodFamily.FRUIT_VEG,
        }
    ),
    # UNKNOWN_FOOD is intentionally permissive — we don't know enough
    # to reject. The downstream confidence threshold then decides.
    FoodFamily.UNKNOWN_FOOD: frozenset(FoodFamily),
}


def is_family_compatible(
    product: FoodFamily, candidate: FoodFamily
) -> bool:
    """True if a retailer product family can be matched by a NEVO
    candidate family.

    The relation is intentionally asymmetric — see ``_COMPATIBLE`` for
    the per-family allow-list. NON_FOOD never matches anything food;
    food never matches NON_FOOD.
    """
    if product is FoodFamily.NON_FOOD or candidate is FoodFamily.NON_FOOD:
        return product is FoodFamily.NON_FOOD and candidate is FoodFamily.NON_FOOD
    return candidate in _COMPATIBLE.get(product, frozenset())

"""Phase Quality-V2-A/B — Protein Tracker V2 deterministic rules.

Offline rule engine only — NOT production-wired. Production keeps using
the V1 guards; this package is reached only by the evaluator + tests.

Core PT principle (Quality-V2-B)
--------------------------------
Protein Tracker classifies by **protein origin**:
- ``animal_core``        — the product's protein is animal.
- ``plant_based_core``   — the product's protein is plant (legumes,
  soy, tofu, seitan, plant-meat alternatives, …).
- ``plant_based_non_core`` — plant foods that are not a protein anchor
  (fruit, veg, grains, snacks, sweets, oils, condiments).
- ``composite_products`` — an animal protein **and** a plant component
  together (a prepared dish with meat/fish/egg/dairy + a plant base, or
  a product carrying both an animal and a plant protein term).

``composite`` does NOT simply mean "multi-ingredient": a vegan dish
(falafel wrap, chickpea curry, bean burger) has no animal protein, so
it is ``plant_based_core``, not composite.

Rules are ordered; the first match wins. Unambiguous single-family
products auto-accept (high confidence, no review); prepared dishes and
broad fallbacks route to review (lower confidence) so a wrong guess is
never silently auto-accepted.
"""

from __future__ import annotations

import re
import unicodedata

from altera_api.classification_v2.models import RuleResult
from altera_api.classification_v2.rule_engine import ProductInput, Rule

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " " + _NON_ALNUM.sub(" ", s.lower()).strip() + " "


def _haystack(p: ProductInput) -> str:
    return _norm(
        " ".join(
            x for x in (p.product_name, p.retailer_category, p.ingredients_text) if x
        )
    )


def _has(hay: str, tokens: tuple[str, ...]) -> bool:
    return any(f" {t} " in hay for t in tokens)


def _has_phrase(hay: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {ph} " in hay for ph in phrases)


# ---------------------------------------------------------------------------
# Token vocabularies (normalised: lowercase, no accents)
# ---------------------------------------------------------------------------
_PET_ACCESSORY_TOKENS = (
    "litiere", "jouet", "jouets", "harnais", "laisse", "collier", "gamelle",
    "panier", "griffoir", "aquarium", "cage", "niche",
)
_PETFOOD_TOKENS = (
    "croquette", "croquettes", "patee", "pet food", "petfood",
    "friandise chien", "friandise chat", "aliment chien", "aliment chat",
    "pour chien", "pour chat",
)

_MEAT_TOKENS = (
    "boeuf", "porc", "poulet", "dinde", "jambon", "lardon", "lardons", "viande",
    "agneau", "veau", "canard", "chorizo", "merguez", "saucisse", "saucisses",
    "saucisson", "bacon",
    "beef", "pork", "chicken", "turkey", "ham", "meat", "sausage",
)
_FISH_TOKENS = (
    "poisson", "saumon", "thon", "crevette", "crevettes", "sardine", "sardines",
    "maquereau", "anchois", "hareng", "cabillaud", "colin", "merlu", "moules",
    "crabe", "homard", "calamar", "surimi",
    "fish", "salmon", "tuna", "shrimp", "prawn", "seafood", "cod",
)
_EGG_TOKENS = ("oeuf", "oeufs", "egg", "eggs")
_DAIRY_TOKENS = (
    "fromage", "lait", "beurre", "creme", "yaourt", "mozzarella", "parmesan",
    "cheddar", "comte", "emmental", "feta", "ricotta", "mascarpone",
    "cheese", "milk", "cream", "yoghurt", "yogurt",
)

_PLANT_PROTEIN_TOKENS = (
    "lentille", "lentilles", "pois chiche", "pois chiches", "chiche", "chiches",
    "haricot", "haricots", "tofu", "tempeh", "seitan", "edamame", "falafel",
    "falafels", "houmous", "hummus", "feve", "feves", "soja",
    "lentil", "lentils", "chickpea", "chickpeas", "bean", "beans",
    "pea protein", "soy protein", "soya",
)
# Plant-based meat/sausage/burger alternatives (clear plant protein).
_PLANT_MEAT_ALT_PHRASES = (
    "steak vegetal", "steak vegetale", "burger vegetal", "burger vegetalien",
    "saucisse vegetale", "nuggets vegetaux", "boulettes vegetales",
    "emince vegetal", "hache vegetal", "vegan burger", "veggie burger",
    "plant based burger", "proteine de soja", "proteine de pois",
)

_NON_CORE_TOKENS = (
    # fruit / veg
    "pomme", "pommes", "banane", "bananes", "fraise", "fraises", "carotte",
    "carottes", "tomate", "tomates", "courgette", "courgettes", "salade verte",
    "epinard", "epinards", "brocoli", "champignon", "champignons", "mangue",
    "poivron", "concombre", "fruit", "fruits", "legume", "legumes",
    # grains / bakery
    "riz", "pates", "pasta", "rice", "pain", "bread", "farine", "cereales",
    "cereale", "muesli", "granola", "avoine", "oats", "ble", "quinoa",
    "boulgour", "semoule", "biscotte",
    # snacks / sweets / fats / condiments
    "chips", "chocolat", "chocolate", "biscuit", "biscuits", "cookie", "cookies",
    "bonbon", "bonbons", "gateau", "gateaux", "cake", "barre", "barres",
    "snack", "snacks", "crackers", "huile", "oil", "vinaigre", "ketchup",
    "moutarde", "confiture", "miel", "sucre", "sirop", "epice", "epices",
    "jus", "soda", "the", "cafe",
)
_DISH_TOKENS = (
    "pizza", "quiche", "lasagne", "lasagnes", "curry", "sushi", "sushis",
    "sandwich", "wrap", "bowl", "gratin", "risotto", "paella", "tajine",
    "gnocchi", "ravioli", "raviolis", "soupe", "salade", "burrito", "tacos",
    "nem", "samoussa", "hachis", "poke", "buddha bowl",
)
_KNOWN_ANIMAL_DISH_PHRASES = (
    "quiche lorraine", "lasagne bolognaise", "lasagnes bolognaise",
    "hachis parmentier", "chili con carne", "boeuf bourguignon", "cassoulet",
    "pot au feu", "blanquette de veau", "carbonara", "croque monsieur",
    "fish and chips",
)
_VEGAN_CUE = (
    "vegan", "vegane", "vegetal", "vegetale", "vegetalien", "sin carne", "veggie",
)


def _has_animal(hay: str) -> bool:
    """Animal protein present. A vegan/plant cue cancels a *dairy* token
    (e.g. 'fromage vegetal', 'yaourt vegetal' are plant alternatives),
    but never cancels an explicit meat/fish term."""
    hard_animal = _has(hay, _MEAT_TOKENS) or _has(hay, _FISH_TOKENS)
    soft_animal = _has(hay, _EGG_TOKENS) or _has(hay, _DAIRY_TOKENS)
    if hard_animal:
        return True
    if soft_animal and _has(hay, _VEGAN_CUE):
        return False
    return soft_animal


# Known vegan protein dishes whose name lacks an explicit legume token
# but which are unambiguously plant-protein (meat-replacement dishes).
_VEGAN_PROTEIN_PHRASES = ("sin carne", "chili sin carne", "veggie chili")


def _has_plant_protein(hay: str) -> bool:
    return (
        _has(hay, _PLANT_PROTEIN_TOKENS)
        or _has_phrase(hay, _PLANT_MEAT_ALT_PHRASES)
        or _has_phrase(hay, _VEGAN_PROTEIN_PHRASES)
    )


# ---------------------------------------------------------------------------
# Rules (ordered; first match wins)
# ---------------------------------------------------------------------------
def rule_pet_accessory_oos(p: ProductInput) -> RuleResult:
    """Pet ACCESSORIES (litter, toys, leash …) → out_of_scope."""
    hay = _haystack(p)
    if _has(hay, _PET_ACCESSORY_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="pt_pet_accessory_oos_v1",
            confidence=0.95,
            classification={"pt_group": "out_of_scope"},
            rationale="Pet accessory (non-food) → out of scope.",
        )
    return RuleResult.no_match("pt_pet_accessory_oos_v1")


def rule_pet_food(p: ProductInput) -> RuleResult:
    """Pet FOOD is in scope (it is food): classify by protein origin."""
    hay = _haystack(p)
    if not _has(hay, _PETFOOD_TOKENS):
        return RuleResult.no_match("pt_pet_food_v1")
    if _has_animal(hay) and not _has_plant_protein(hay):
        grp, conf = "animal_core", 0.9
    elif _has_plant_protein(hay) and not _has_animal(hay):
        grp, conf = "plant_based_core", 0.88
    else:
        grp, conf = "composite_products", 0.7
    return RuleResult(
        matched=True,
        rule_id="pt_pet_food_v1",
        confidence=conf,
        review_required=conf < 0.9,
        classification={"pt_group": grp},
        rationale=f"Pet food (in scope) classified by protein → {grp}.",
    )


def rule_composite_animal_plant(p: ProductInput) -> RuleResult:
    """Animal protein + plant component → composite_products.

    Fires when (a) a known animal-dish phrase, (b) a prepared-dish token
    together with an animal protein, or (c) both an animal and a plant
    protein term appear. Prepared dishes route to review."""
    hay = _haystack(p)
    known = _has_phrase(hay, _KNOWN_ANIMAL_DISH_PHRASES)
    dish_animal = _has(hay, _DISH_TOKENS) and _has_animal(hay)
    both_protein = _has_animal(hay) and _has_plant_protein(hay)
    if known or dish_animal or both_protein:
        return RuleResult(
            matched=True,
            rule_id="pt_composite_animal_plant_v1",
            confidence=0.85,
            review_required=True,
            classification={"pt_group": "composite_products"},
            rationale="Animal protein with a plant component → composite.",
        )
    return RuleResult.no_match("pt_composite_animal_plant_v1")


def rule_animal_core(p: ProductInput) -> RuleResult:
    """Animal protein, no plant protein anchor → animal_core."""
    hay = _haystack(p)
    if _has_animal(hay) and not _has_plant_protein(hay):
        is_dish = _has(hay, _DISH_TOKENS)
        return RuleResult(
            matched=True,
            rule_id="pt_animal_core_v1",
            confidence=0.85 if is_dish else 0.95,
            review_required=is_dish,
            classification={"pt_group": "animal_core"},
            rationale="Animal protein product → animal_core.",
        )
    return RuleResult.no_match("pt_animal_core_v1")


def rule_plant_core(p: ProductInput) -> RuleResult:
    """Plant protein anchor, no animal → plant_based_core.

    A plain plant-protein product (lentils, tofu, veggie burger)
    auto-accepts; a multi-ingredient vegan *dish* routes to review but
    is still plant_based_core (NOT composite — there is no animal)."""
    hay = _haystack(p)
    if _has_plant_protein(hay) and not _has_animal(hay):
        is_dish = _has(hay, _DISH_TOKENS) or _has(hay, _VEGAN_CUE)
        return RuleResult(
            matched=True,
            rule_id="pt_plant_core_v1",
            confidence=0.85 if is_dish else 0.92,
            review_required=is_dish,
            classification={"pt_group": "plant_based_core"},
            rationale="Central plant protein and no animal term → plant core.",
        )
    return RuleResult.no_match("pt_plant_core_v1")


def rule_plant_non_core(p: ProductInput) -> RuleResult:
    """Plant food that is not a protein anchor (fruit/veg/grain/snack/
    sweet/oil/condiment); also a vegan dish without a protein anchor."""
    hay = _haystack(p)
    if _has_animal(hay):
        return RuleResult.no_match("pt_plant_non_core_v1")
    if _has(hay, _NON_CORE_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="pt_plant_non_core_v1",
            confidence=0.9,
            classification={"pt_group": "plant_based_non_core"},
            rationale="Plant food without a protein anchor → plant non-core.",
        )
    if _has(hay, _VEGAN_CUE) or _has(hay, _DISH_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="pt_plant_non_core_v1",
            confidence=0.7,
            review_required=True,
            classification={"pt_group": "plant_based_non_core"},
            rationale="Vegan/dish without a central protein → plant non-core.",
        )
    return RuleResult.no_match("pt_plant_non_core_v1")


def rule_readable_fallback(p: ProductInput) -> RuleResult:
    """Last resort for a readable name nothing else matched: land in
    plant_based_non_core for review — never an unknown/abstain."""
    if not _norm(p.product_name).strip():
        return RuleResult.no_match("pt_readable_fallback_v1")
    return RuleResult(
        matched=True,
        rule_id="pt_readable_fallback_v1",
        confidence=0.4,
        review_required=True,
        classification={"pt_group": "plant_based_non_core"},
        rationale="Readable name, no family rule fired → review.",
    )


PT_RULES: list[Rule] = [
    rule_pet_accessory_oos,
    rule_pet_food,
    rule_composite_animal_plant,
    rule_animal_core,
    rule_plant_core,
    rule_plant_non_core,
    rule_readable_fallback,
]

"""Phase Quality-V2-A/B — WWF Planet-Based Diets V2 deterministic rules.

Offline rule engine only — NOT production-wired.

WWF classifies product weight into food groups FG1–FG7 and flags
product-level composites for Step 1 (the app implements Step 1
product-level, not Step 2 ingredient-level).

Rule order is deliberate (first match wins):
  1. product-level composite prepared dishes (+ Step-1 bucket)
  2. precedence fixes that must beat the generic group rules:
       plant cheese → FG2 alt; bakery/biscuit → FG7; muesli/cereal →
       FG5; peanut butter → FG3 plant fat; seafood (incl. in oil) → FG1
  3. FG1 protein foods
  4. FG2 dairy + plant alternatives
  5. FG3 fats/oils
  6. FG4 fruit/veg
  7. FG5 grains
  8. FG6 tubers
  9. FG7 snacks
 10. readable fallback (review) — never an abstain on a readable name

Step-1 bucket precedence: meat → seafood → vegetarian (egg/dairy) →
vegan.
"""

from __future__ import annotations

from altera_api.classification_v2.models import RuleResult
from altera_api.classification_v2.pt_rules import _has, _has_phrase, _haystack
from altera_api.classification_v2.rule_engine import ProductInput, Rule

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
_MEAT_TOKENS = (
    "boeuf", "porc", "poulet", "dinde", "jambon", "lardon", "lardons", "viande",
    "agneau", "veau", "canard", "chorizo", "merguez", "saucisse", "saucisses",
    "bacon", "carne", "beef", "pork", "chicken", "turkey", "ham", "meat",
)
_SEAFOOD_TOKENS = (
    "poisson", "saumon", "thon", "sardine", "sardines", "maquereau", "anchois",
    "hareng", "cabillaud", "colin", "crevette", "crevettes", "moules", "crabe",
    "surimi", "fish", "salmon", "tuna", "shrimp", "seafood", "fruits de mer",
)
_EGG_TOKENS = ("oeuf", "oeufs", "egg", "eggs")
_ANIMAL_DAIRY_TOKENS = (
    "fromage", "lait", "creme", "yaourt", "beurre", "mozzarella", "parmesan",
    "cheddar", "comte", "emmental", "feta", "ricotta", "cheese", "milk", "cream",
    "yoghurt", "yogurt",
)
_LEGUME_TOKENS = (
    "lentille", "lentilles", "pois chiche", "pois chiches", "haricot", "haricots",
    "feve", "feves", "edamame", "lentil", "lentils", "chickpea", "chickpeas",
    "bean", "beans",
)
_NUTS_SEEDS_TOKENS = (
    "amande", "amandes", "noix", "noisette", "noisettes", "cajou", "pistache",
    "graine", "graines", "tournesol", "sesame", "chia", "lin", "nut", "nuts",
    "almond", "almonds", "seed", "seeds", "cashew", "hazelnut",
)
_ALT_PROTEIN_TOKENS = (
    "tofu", "tempeh", "seitan", "soja", "soya", "proteine de soja",
    "proteine de pois", "steak vegetal", "burger vegetal", "saucisse vegetale",
    "nuggets vegetaux", "boulettes vegetales", "meat alternative",
    "plant based", "veggie burger",
)
_PLANT_MILK_PHRASES = (
    "lait vegetal", "boisson vegetale", "boisson avoine", "boisson amande",
    "boisson soja", "lait d amande", "lait de soja", "lait d avoine",
    "oat drink", "almond drink", "soy milk", "plant milk", "boisson coco",
)
_PLANT_DAIRY_ALT_PHRASES = (
    "fromage vegetal", "fromage vegan", "fromage vegetalien", "yaourt vegetal",
    "yaourt soja", "yaourt vegan", "creme vegetale", "vegan cheese", "soy yoghurt",
)
_PLANT_OIL_TOKENS = (
    "huile", "oil", "margarine", "huile d olive", "huile de tournesol",
    "huile de colza",
)
_FRUIT_VEG_TOKENS = (
    "pomme", "pommes", "banane", "bananes", "fraise", "fraises", "mangue",
    "carotte", "carottes", "tomate", "tomates", "courgette", "courgettes",
    "brocoli", "epinard", "epinards", "champignon", "champignons", "poivron",
    "concombre", "salade verte", "ratatouille", "compote", "fruit", "fruits",
    "legume", "legumes", "petits pois", "haricots verts", "myrtille", "myrtilles",
    "framboise", "framboises",
    # English
    "apple", "apples", "banana", "carrot", "carrots", "tomato", "tomatoes",
    "spinach", "mushroom", "mushrooms", "strawberry", "berries", "vegetable",
    "vegetables", "courgette", "broccoli",
)
_GRAIN_TOKENS = (
    "riz", "pates", "pasta", "rice", "pain", "bread", "farine", "ble",
    "cereales", "cereale", "muesli", "granola", "avoine", "oats", "quinoa",
    "boulgour", "semoule", "biscotte", "flocons",
)
_WHOLE_GRAIN_CUES = ("complet", "complete", "complets", "completes", "whole", "integral")
_TUBER_TOKENS = (
    "pomme de terre", "pommes de terre", "patate", "patates", "patate douce",
    "manioc", "igname", "potato", "potatoes", "sweet potato", "cassava", "yam",
)
_SNACK_TOKENS = (
    "biscuit", "biscuits", "sable", "sables", "cookie", "cookies", "gateau",
    "gateaux", "cake", "madeleine", "viennoiserie", "chocolat", "chocolate",
    "bonbon", "bonbons", "chips", "crisps", "crackers", "glace", "ice cream",
    "donut", "donuts", "barre chocolatee", "gaufre", "gaufres",
)
_ANIMAL_SNACK_CUES = ("beurre", "lait", "creme", "butter", "milk", "cream")

_STRONG_DISH_TOKENS = (
    "pizza", "quiche", "lasagne", "lasagnes", "curry", "sushi", "sushis",
    "gratin", "wrap", "burrito", "tajine", "paella", "risotto", "gnocchi",
    "ravioli", "raviolis", "cannelloni", "tarte salee", "cake sale",
    "croque monsieur", "hachis parmentier", "nem", "samoussa", "tacos",
)
_WEAK_DISH_TOKENS = (
    "salade", "soupe", "sandwich", "bowl", "poke", "buddha bowl", "tarte",
    "tourte", "omelette",
)
_PLANT_CHEESE_PHRASES = _PLANT_DAIRY_ALT_PHRASES
_PEANUT_BUTTER_PHRASES = (
    "beurre de cacahuete", "beurre de cacahuetes", "peanut butter", "beurre cacahuete",
)


def _has_meat(hay: str) -> bool:
    return _has(hay, _MEAT_TOKENS)


def _has_seafood(hay: str) -> bool:
    return _has(hay, _SEAFOOD_TOKENS)


def _has_animal_protein(hay: str) -> bool:
    return (
        _has_meat(hay)
        or _has_seafood(hay)
        or _has(hay, _EGG_TOKENS)
        or _has(hay, _ANIMAL_DAIRY_TOKENS)
    )


def _bucket(hay: str) -> str:
    if _has_meat(hay):
        return "meat_based"
    if _has_seafood(hay):
        return "seafood_based"
    if _has(hay, _EGG_TOKENS) or _has(hay, _ANIMAL_DAIRY_TOKENS):
        return "vegetarian"
    return "vegan"


def _fg(food_group: str, *, composite=False, bucket=None, conf=0.9, review=False,
        rule_id="", rationale="", **subgroups) -> RuleResult:
    cls: dict = {"wwf_food_group": food_group, "wwf_is_composite": composite}
    if bucket is not None:
        cls["wwf_composite_step1_bucket"] = bucket
    cls.update({k: v for k, v in subgroups.items() if v is not None})
    return RuleResult(
        matched=True, rule_id=rule_id, confidence=conf,
        review_required=review, classification=cls, rationale=rationale,
    )


# ---------------------------------------------------------------------------
# 1. Composite prepared dishes
# ---------------------------------------------------------------------------
def rule_prepared_dish_composite(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    is_strong = _has(hay, _STRONG_DISH_TOKENS)
    is_weak = _has(hay, _WEAK_DISH_TOKENS)
    has_protein = _has_animal_protein(hay) or _has(hay, _LEGUME_TOKENS) or _has(hay, _ALT_PROTEIN_TOKENS)
    # A weak "dish" word (salade/soupe) is only composite WITH a protein;
    # otherwise it is a simple fruit/veg dish handled by FG4.
    if is_strong or (is_weak and has_protein):
        bucket = _bucket(hay)
        return _fg(
            "FG1", composite=True, bucket=bucket, conf=0.82, review=True,
            rule_id="wwf_prepared_dish_composite_v1",
            rationale=f"Prepared multi-group dish → composite ({bucket}).",
        )
    return RuleResult.no_match("wwf_prepared_dish_composite_v1")


# ---------------------------------------------------------------------------
# 2. Precedence fixes (must beat the generic FG rules below)
# ---------------------------------------------------------------------------
def rule_plant_cheese_fg2(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has_phrase(hay, _PLANT_CHEESE_PHRASES):
        return _fg(
            "FG2", conf=0.88, rule_id="wwf_plant_cheese_fg2_v1",
            fg2_subgroup="dairy_alternative_plant",
            rationale="Plant-based dairy alternative → FG2 plant alternative.",
        )
    return RuleResult.no_match("wwf_plant_cheese_fg2_v1")


def rule_plant_drink_fg2(p: ProductInput) -> RuleResult:
    """Plant milk / oat-almond-soy drink → FG2 plant alternative — must
    beat FG1 nuts ('lait d'amande') and FG5 cereals ('boisson avoine')."""
    hay = _haystack(p)
    if _has_phrase(hay, _PLANT_MILK_PHRASES):
        return _fg(
            "FG2", conf=0.88, rule_id="wwf_plant_drink_fg2_v1",
            fg2_subgroup="dairy_alternative_plant",
            rationale="Plant drink → FG2 plant alternative.",
        )
    return RuleResult.no_match("wwf_plant_drink_fg2_v1")


def rule_bakery_snack_fg7(p: ProductInput) -> RuleResult:
    """Biscuit/cookie/cake/viennoiserie + frozen desserts → FG7 snack —
    butter/milk/cream in the name is an ingredient, not the food group
    (beats FG2/FG3)."""
    hay = _haystack(p)
    bakery = ("biscuit", "biscuits", "sable", "sables", "cookie", "cookies",
              "gateau", "gateaux", "cake", "madeleine", "viennoiserie", "gaufre",
              "gaufres", "brioche", "glace", "glaces", "ice cream", "sorbet",
              "creme glacee", "donut", "donuts")
    if _has(hay, bakery):
        kind = "animal_based_snack" if _has(hay, _ANIMAL_SNACK_CUES) else "plant_based_snack"
        return _fg(
            "FG7", conf=0.85, rule_id="wwf_bakery_snack_fg7_v1",
            fg7_snack_kind=kind,
            rationale="Bakery snack → FG7 (butter/milk are ingredients).",
        )
    return RuleResult.no_match("wwf_bakery_snack_fg7_v1")


def rule_cereal_muesli_fg5(p: ProductInput) -> RuleResult:
    """Muesli/granola/cereal/oats → FG5 grains — beats FG1 nuts/seeds for
    'graines'/'noix' that appear in a cereal product."""
    hay = _haystack(p)
    if _has(hay, ("muesli", "granola", "cereales", "cereale", "avoine", "oats", "flocons")):
        grain = "whole_grain" if _has(hay, _WHOLE_GRAIN_CUES) or _has(hay, ("avoine", "oats")) else "refined_grain"
        return _fg(
            "FG5", conf=0.88, rule_id="wwf_cereal_muesli_fg5_v1",
            fg5_grain_kind=grain,
            rationale="Breakfast cereal/muesli → FG5 grains.",
        )
    return RuleResult.no_match("wwf_cereal_muesli_fg5_v1")


def rule_peanut_butter_fg3(p: ProductInput) -> RuleResult:
    """Peanut butter / beurre de cacahuète → FG3 plant fat — NOT animal
    fat (the word 'beurre'/'butter' must not trigger FG3 animal)."""
    hay = _haystack(p)
    if _has_phrase(hay, _PEANUT_BUTTER_PHRASES):
        return _fg(
            "FG3", conf=0.85, rule_id="wwf_peanut_butter_fg3_v1",
            fg3_subgroup="plant_based_fat",
            rationale="Peanut butter → FG3 plant-based fat (not animal fat).",
        )
    return RuleResult.no_match("wwf_peanut_butter_fg3_v1")


def rule_seafood_fg1(p: ProductInput) -> RuleResult:
    """Fish/seafood (incl. 'à l'huile') → FG1 seafood — beats FG3 oil."""
    hay = _haystack(p)
    if _has_seafood(hay):
        return _fg(
            "FG1", conf=0.9, rule_id="wwf_seafood_fg1_v1", fg1_subgroup="seafood",
            rationale="Seafood (any oil is packing medium) → FG1 seafood.",
        )
    return RuleResult.no_match("wwf_seafood_fg1_v1")


# ---------------------------------------------------------------------------
# 3. FG1 protein foods
# ---------------------------------------------------------------------------
def rule_fg1_protein(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has_meat(hay):
        sub = "processed_meats_alternatives" if _has(
            hay, ("jambon", "lardon", "lardons", "chorizo", "saucisse", "saucisses",
                  "bacon", "ham")
        ) else ("poultry" if _has(hay, ("poulet", "dinde", "chicken", "turkey")) else "red_meat")
        return _fg("FG1", conf=0.9, rule_id="wwf_fg1_meat_v1", fg1_subgroup=sub,
                   rationale="Meat product → FG1.")
    if _has(hay, _EGG_TOKENS):
        return _fg("FG1", conf=0.9, rule_id="wwf_fg1_egg_v1", fg1_subgroup="eggs",
                   rationale="Eggs → FG1.")
    if _has(hay, _ALT_PROTEIN_TOKENS):
        return _fg("FG1", conf=0.88, rule_id="wwf_fg1_altprotein_v1",
                   fg1_subgroup="alternative_protein_sources",
                   rationale="Plant protein alternative → FG1.")
    if _has(hay, _LEGUME_TOKENS):
        return _fg("FG1", conf=0.9, rule_id="wwf_fg1_legume_v1", fg1_subgroup="legumes",
                   rationale="Legumes → FG1.")
    if _has(hay, _NUTS_SEEDS_TOKENS):
        return _fg("FG1", conf=0.88, rule_id="wwf_fg1_nuts_v1", fg1_subgroup="nuts_seeds",
                   rationale="Nuts/seeds → FG1.")
    return RuleResult.no_match("wwf_fg1_protein_v1")


# ---------------------------------------------------------------------------
# 4. FG2 dairy + plant alternatives
# ---------------------------------------------------------------------------
def rule_fg2_dairy(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has_phrase(hay, _PLANT_MILK_PHRASES):
        return _fg("FG2", conf=0.88, rule_id="wwf_fg2_plant_drink_v1",
                   fg2_subgroup="dairy_alternative_plant",
                   rationale="Plant drink → FG2 plant alternative.")
    if _has(hay, ("fromage", "cheese", "mozzarella", "parmesan", "cheddar", "comte",
                  "emmental", "feta", "ricotta")):
        return _fg("FG2", conf=0.9, rule_id="wwf_fg2_cheese_v1", fg2_subgroup="cheese",
                   rationale="Cheese → FG2.")
    if _has(hay, ("lait", "yaourt", "creme", "milk", "yoghurt", "yogurt", "cream")):
        return _fg("FG2", conf=0.9, rule_id="wwf_fg2_dairy_v1",
                   fg2_subgroup="other_dairy_animal",
                   rationale="Dairy → FG2.")
    return RuleResult.no_match("wwf_fg2_dairy_v1")


# ---------------------------------------------------------------------------
# 5. FG3 fats / oils
# ---------------------------------------------------------------------------
def rule_fg3_fats(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has(hay, ("beurre", "butter")) and not _has_phrase(hay, _PEANUT_BUTTER_PHRASES):
        return _fg("FG3", conf=0.85, rule_id="wwf_fg3_butter_v1",
                   fg3_subgroup="animal_based_fat",
                   rationale="Butter / animal fat → FG3 animal fat.")
    if _has(hay, _PLANT_OIL_TOKENS):
        return _fg("FG3", conf=0.88, rule_id="wwf_fg3_oil_v1",
                   fg3_subgroup="plant_based_fat",
                   rationale="Plant oil / margarine → FG3 plant fat.")
    return RuleResult.no_match("wwf_fg3_fats_v1")


# ---------------------------------------------------------------------------
# 6. FG4 fruit / veg
# ---------------------------------------------------------------------------
def rule_fg4_fruit_veg(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has(hay, _FRUIT_VEG_TOKENS):
        return _fg("FG4", conf=0.9, rule_id="wwf_fg4_fruit_veg_v1",
                   rationale="Fruit/vegetable → FG4.")
    return RuleResult.no_match("wwf_fg4_fruit_veg_v1")


# ---------------------------------------------------------------------------
# 7. FG5 grains
# ---------------------------------------------------------------------------
def rule_fg5_grains(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has(hay, _GRAIN_TOKENS):
        grain = "whole_grain" if _has(hay, _WHOLE_GRAIN_CUES) else "refined_grain"
        return _fg("FG5", conf=0.9, rule_id="wwf_fg5_grain_v1", fg5_grain_kind=grain,
                   rationale="Grain product → FG5.")
    return RuleResult.no_match("wwf_fg5_grains_v1")


# ---------------------------------------------------------------------------
# 8. FG6 tubers
# ---------------------------------------------------------------------------
def rule_fg6_tubers(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has(hay, _TUBER_TOKENS):
        return _fg("FG6", conf=0.9, rule_id="wwf_fg6_tuber_v1",
                   rationale="Tuber/starchy root → FG6.")
    return RuleResult.no_match("wwf_fg6_tubers_v1")


# ---------------------------------------------------------------------------
# 9. FG7 snacks
# ---------------------------------------------------------------------------
def rule_fg7_snacks(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if _has(hay, _SNACK_TOKENS):
        kind = "animal_based_snack" if _has(hay, _ANIMAL_SNACK_CUES) else "plant_based_snack"
        return _fg("FG7", conf=0.88, rule_id="wwf_fg7_snack_v1", fg7_snack_kind=kind,
                   rationale="Snack / sweet → FG7.")
    return RuleResult.no_match("wwf_fg7_snacks_v1")


# ---------------------------------------------------------------------------
# 10. Readable fallback (never abstain on a readable name)
# ---------------------------------------------------------------------------
def rule_readable_fallback(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    if not hay.strip():
        return RuleResult.no_match("wwf_readable_fallback_v1")
    # Default unusable-but-readable names to FG4 for review (most common
    # ambiguous case is a vegetable/produce name), low confidence.
    return _fg("FG4", conf=0.4, review=True, rule_id="wwf_readable_fallback_v1",
               rationale="Readable name, no family rule fired → review.")


WWF_RULES: list[Rule] = [
    rule_prepared_dish_composite,
    rule_plant_cheese_fg2,
    rule_plant_drink_fg2,
    rule_bakery_snack_fg7,
    rule_cereal_muesli_fg5,
    rule_peanut_butter_fg3,
    rule_seafood_fg1,
    rule_fg1_protein,
    rule_fg2_dairy,
    rule_fg3_fats,
    rule_fg6_tubers,   # tubers before fruit/veg ('pommes de terre' ≠ apple)
    rule_fg4_fruit_veg,
    rule_fg5_grains,
    rule_fg7_snacks,
    rule_readable_fallback,
]

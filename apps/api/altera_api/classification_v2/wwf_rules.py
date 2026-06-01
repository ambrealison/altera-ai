"""Phase Quality-V2-A — minimal WWF V2 rules (proof-of-architecture).

Five deterministic rules covering known WWF traps: simple fruit/veg
→ FG4, prepared dishes → composite bucket, fish-in-oil stays FG1
seafood (not FG3 fat), biscuit/cake-with-butter stays FG7 (not FG3
fat), and plant-based cheese → FG2 plant alternative. NOT exhaustive
and NOT production-wired.
"""

from __future__ import annotations

from altera_api.classification_v2.models import RuleResult
from altera_api.classification_v2.pt_rules import _has, _haystack
from altera_api.classification_v2.rule_engine import ProductInput, Rule

_FG4_TOKENS = (
    "carotte", "carottes", "epinard", "epinards", "brocoli", "brocolis",
    "champignon", "champignons", "mangue", "tomate", "tomates", "pomme",
    "pommes", "banane", "bananes", "courgette", "courgettes", "poivron",
    "salade", "fraise", "fraises",
)
_SEAFOOD_TOKENS = (
    "sardine", "sardines", "thon", "saumon", "maquereau", "anchois",
    "hareng", "poisson",
)
_BISCUIT_TOKENS = (
    "biscuit", "biscuits", "sable", "sables", "cookie", "cookies", "gateau",
    "cake", "madeleine", "viennoiserie",
)
_PLANT_CHEESE_TOKENS = (
    "fromage vegetal", "fromage vegan", "vegan cheese", "fromage vegetalien",
)
_COMPOSITE_DISH_TOKENS = (
    "curry", "quiche", "chili", "sushi", "sushis", "bowl", "pizza", "wrap",
    "sandwich", "lasagne", "lasagnes", "gratin", "tarte", "burrito",
)
_MEAT_TOKENS = (
    "poulet", "boeuf", "porc", "jambon", "lardon", "viande", "carne",
    "dinde", "agneau",
)


def rule_fg4_simple_fruit_veg(p: ProductInput) -> RuleResult:
    hay = _haystack(p)
    # Composite dishes take precedence (handled by a later/earlier rule
    # ordering); a *simple* fruit/veg name has no dish token.
    if _has(hay, _COMPOSITE_DISH_TOKENS):
        return RuleResult.no_match("wwf_fg4_simple_fruit_veg_v1")
    if _has(hay, _FG4_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="wwf_fg4_simple_fruit_veg_v1",
            confidence=0.9,
            classification={"wwf_food_group": "FG4", "wwf_is_composite": False},
            rationale="Simple fruit/vegetable product.",
        )
    return RuleResult.no_match("wwf_fg4_simple_fruit_veg_v1")


def rule_plant_cheese_fg2(p: ProductInput) -> RuleResult:
    """Plant-based cheese → FG2 dairy alternative (beats cheese/FG3)."""
    hay = _haystack(p)
    if _has(hay, _PLANT_CHEESE_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="wwf_plant_cheese_fg2_v1",
            confidence=0.88,
            classification={
                "wwf_food_group": "FG2",
                "wwf_is_composite": False,
                "fg2_subgroup": "dairy_alternative_plant",
            },
            rationale="Plant-based cheese → FG2 plant dairy alternative.",
        )
    return RuleResult.no_match("wwf_plant_cheese_fg2_v1")


def rule_seafood_in_oil_fg1(p: ProductInput) -> RuleResult:
    """Fish/seafood with oil stays FG1 seafood, not FG3 plant fat."""
    hay = _haystack(p)
    if _has(hay, _SEAFOOD_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="wwf_seafood_in_oil_fg1_v1",
            confidence=0.88,
            classification={
                "wwf_food_group": "FG1",
                "wwf_is_composite": False,
                "fg1_subgroup": "seafood",
            },
            rationale="Seafood product (oil is packing medium, not the food).",
        )
    return RuleResult.no_match("wwf_seafood_in_oil_fg1_v1")


def rule_biscuit_butter_fg7(p: ProductInput) -> RuleResult:
    """Biscuit/cookie/cake with butter stays FG7 snack, not FG3 fat."""
    hay = _haystack(p)
    if _has(hay, _BISCUIT_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="wwf_biscuit_butter_fg7_v1",
            confidence=0.86,
            classification={
                "wwf_food_group": "FG7",
                "wwf_is_composite": False,
                "fg7_snack_kind": "animal_based_snack",
            },
            rationale="Bakery snack — butter is an ingredient, not the food.",
        )
    return RuleResult.no_match("wwf_biscuit_butter_fg7_v1")


def rule_prepared_dish_composite(p: ProductInput) -> RuleResult:
    """Prepared multi-group dish → composite with a Step-1 bucket
    (meat → seafood → vegetarian → vegan precedence)."""
    hay = _haystack(p)
    if not _has(hay, _COMPOSITE_DISH_TOKENS):
        return RuleResult.no_match("wwf_prepared_dish_composite_v1")
    if _has(hay, _MEAT_TOKENS):
        bucket = "meat_based"
    elif _has(hay, _SEAFOOD_TOKENS):
        bucket = "seafood_based"
    elif _has(hay, ("fromage", "lait", "oeuf", "oeufs", "creme", "beurre")):
        bucket = "vegetarian"
    else:
        bucket = "vegan"
    return RuleResult(
        matched=True,
        rule_id="wwf_prepared_dish_composite_v1",
        confidence=0.82,
        review_required=True,
        classification={
            "wwf_food_group": "FG1",
            "wwf_is_composite": True,
            "wwf_composite_step1_bucket": bucket,
        },
        rationale=f"Prepared multi-group dish → composite ({bucket}).",
    )


# Priority: plant-cheese + seafood + biscuit specifics first (they beat
# the generic composite/FG4 rules), then prepared-dish composite, then
# simple fruit/veg.
WWF_RULES: list[Rule] = [
    rule_plant_cheese_fg2,
    rule_seafood_in_oil_fg1,
    rule_biscuit_butter_fg7,
    rule_prepared_dish_composite,
    rule_fg4_simple_fruit_veg,
]

"""Phase Quality-V2-A — minimal Protein Tracker V2 rules.

Proof-of-architecture only: three deterministic rules that cover the
known hard cases (vegan composite demotion, plant-core promotion,
snack/cereal). NOT exhaustive and NOT production-wired. The full V2
ruleset is built in later phases against the evaluation harness.
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


_ANIMAL_TOKENS = (
    "boeuf", "porc", "poulet", "dinde", "jambon", "lardon", "viande",
    "poisson", "saumon", "thon", "crevette", "bacon", "agneau", "veau",
    "canard", "chorizo", "merguez",
)
_DAIRY_EGG_TOKENS = (
    "fromage", "lait", "beurre", "creme", "oeuf", "oeufs", "yaourt",
    "mozzarella", "parmesan", "cheddar",
)
_PLANT_PROTEIN_TOKENS = (
    "lentille", "lentilles", "pois chiche", "pois chiches", "chiche",
    "chiches", "haricot", "haricots", "tofu", "tempeh", "seitan",
    "edamame", "falafel", "falafels", "houmous", "hummus", "feve", "feves",
    "soja",
)
_SNACK_CEREAL_TOKENS = (
    "muesli", "granola", "chips", "chocolat", "biscuit", "biscuits",
    "barre", "barres", "cereales", "cereale", "avoine",
)
_VEGAN_CUE = ("vegan", "vegane", "vegetal", "vegetale", "vegetalien", "sin carne")


def rule_composite_animal_plant(p: ProductInput) -> RuleResult:
    """Animal protein + plant protein in one dish → composite."""
    hay = _haystack(p)
    has_animal = _has(hay, _ANIMAL_TOKENS) or _has(hay, _DAIRY_EGG_TOKENS)
    has_plant = _has(hay, _PLANT_PROTEIN_TOKENS)
    if has_animal and has_plant:
        return RuleResult(
            matched=True,
            rule_id="pt_composite_animal_plant_v1",
            confidence=0.9,
            classification={"pt_group": "composite_products"},
            rationale="Contains both animal and plant protein sources.",
        )
    return RuleResult.no_match("pt_composite_animal_plant_v1")


def rule_plant_core_legume_dish(p: ProductInput) -> RuleResult:
    """Central legume/tofu/falafel/hummus and no animal → plant_core."""
    hay = _haystack(p)
    has_animal = _has(hay, _ANIMAL_TOKENS) or _has(hay, _DAIRY_EGG_TOKENS)
    if has_animal:
        return RuleResult.no_match("pt_plant_core_legume_dish_v1")
    if _has(hay, _PLANT_PROTEIN_TOKENS):
        return RuleResult(
            matched=True,
            rule_id="pt_plant_core_legume_dish_v1",
            confidence=0.88,
            review_required=True,
            classification={"pt_group": "plant_based_core"},
            rationale="Central plant-protein anchor and no animal terms.",
        )
    return RuleResult.no_match("pt_plant_core_legume_dish_v1")


def rule_snack_cereal_non_core(p: ProductInput) -> RuleResult:
    """Snack / cereal / muesli / chips / chocolate / granola →
    plant_based_non_core (unless a central plant protein already
    promoted it via an earlier rule)."""
    hay = _haystack(p)
    if _has(hay, _ANIMAL_TOKENS):
        return RuleResult.no_match("pt_snack_cereal_non_core_v1")
    if _has(hay, _SNACK_CEREAL_TOKENS) or _has(hay, _VEGAN_CUE):
        return RuleResult(
            matched=True,
            rule_id="pt_snack_cereal_non_core_v1",
            confidence=0.8,
            review_required=True,
            classification={"pt_group": "plant_based_non_core"},
            rationale="Snack/cereal or vegan dish without central protein.",
        )
    return RuleResult.no_match("pt_snack_cereal_non_core_v1")


# Priority order: composite first (animal+plant), then plant-core
# promotion, then the broad snack/cereal/vegan demotion.
PT_RULES: list[Rule] = [
    rule_composite_animal_plant,
    rule_plant_core_legume_dish,
    rule_snack_cereal_non_core,
]

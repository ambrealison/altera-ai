"""Phase 36I — deterministic post-classification guards for Protein Tracker.

A 150-product audit on production data showed ~76–80% precision, with
four dominant error classes:

  1. Fruits / vegetables / soups / coulis / confitures classified as
     ``plant_based_core``. The PT taxonomy reserves ``plant_based_core``
     for protein-rich plants (legumes, tofu, tempeh, seitan, nuts,
     seeds, explicit plant protein substitutes). A coulis of mango or
     a velouté of tomato is plant-based BUT not protein-rich, so it
     belongs in ``plant_based_non_core``.

  2. Beverages classified inconsistently. The rule:
       * ``out_of_scope``: tea, coffee, soda, lemonade, water, energy
         drinks, alcohol (no nutritional protein contribution).
       * ``plant_based_non_core``: fruit juice, smoothie, fruity
         drink, nectar.
       * Plant-milk drinks (boisson avoine / soja / amande / riz):
         keep whatever Phase 34T/V decided (NOT overridden here).

  3. Sweet bakery and chocolate routinely classified as
     ``plant_based_non_core`` or ``animal_core`` when they're almost
     certainly composites (butter, milk, eggs likely present).
     ``Sablés noisette``, ``Croissants maïs``, ``Tablette lait``,
     ``Chocolat au lait`` → ``composite_products``.

  4. Prepared meals with an animal token (curry saumon, parmentier
     saumon, poêlée saumon, soupe poulet légumes, cassoulet …)
     classified as ``animal_core``. ``animal_core`` is reserved for
     SIMPLE animal foods (raw meat, raw fish, eggs, plain dairy).
     Anything that's a prepared dish belongs in
     ``composite_products``.

This module exposes a single entry point — :func:`apply_pt_guards` —
that the batch classifier calls AFTER it has materialised a
:class:`ProteinTrackerProductClassification`. The function applies
the four guards in order; the first one that fires wins. The
returned classification has its category corrected, its confidence
clamped to ≤ 0.69 (so the orchestrator routes the row to
``needs_review``), and its rationale extended with the guard reason.

The guards are intentionally conservative — they only fire on
unambiguous patterns. When in doubt the model's verdict is preserved
verbatim. Existing good behaviour (lentilles → plant_based_core,
sardines → animal_core, tofu nature → plant_based_core, etc.) is
covered by the non-regression tests in
``tests/api/test_phase36i_pt_guards.py``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)

# Confidence ceiling applied when ANY guard overrides the model's
# verdict. The batch classifier auto-accepts at >= 0.70 (Phase 34Q),
# so 0.69 deterministically routes the row to ``needs_review``. We
# never silently auto-accept a guard-corrected verdict.
_GUARD_CONFIDENCE_CEILING: Decimal = Decimal("0.69")


# ---------------------------------------------------------------------------
# Tokenisation helpers (accent-folded, boundary-aware substring scan)
# ---------------------------------------------------------------------------


def _normalise(s: str) -> str:
    """Lowercase + accent-fold for substring matching."""
    nf = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in nf if not unicodedata.combining(c))


def _contains_any_word(haystack: str, needles: tuple[str, ...]) -> bool:
    """True if any needle appears as a whole word in ``haystack``.

    The haystack is accent-folded by the caller; needles MUST also be
    accent-folded. Word boundaries use ``\\b`` so "lait" does NOT
    match "laitue" (lettuce).
    """
    for n in needles:
        if re.search(rf"\b{re.escape(n)}\b", haystack):
            return True
    return False


# ---------------------------------------------------------------------------
# Vocabularies — accent-folded, lowercase
# ---------------------------------------------------------------------------


#: Tokens that flag a product as *clearly NOT* plant_based_core,
#: even if the model proposed that category. The brief identified
#: these recurring error patterns in the 150-product audit:
#:
#:   * Fruit preparations (coulis, compote, confiture, marmelade).
#:   * Sweet sugars / spreads (miel, sirop, sucre).
#:   * Vegetable soups & purees (soupe, velouté, gaspacho, mouliné,
#:     potage).
#:   * Cooked vegetable preparations (jardinière, ratatouille, mais
#:     doux, pommes de terre, frites, purée).
#:   * Plain sauces / oils that are sometimes mislabelled (sauce,
#:     huile, ketchup, mayonnaise, vinaigrette, vinaigre).
#:   * Single-vegetable products (épinard, courgette, brocoli…) —
#:     simple vegetables don't carry enough protein to qualify as
#:     plant_based_core.
#:
#: The list is deliberately conservative: bare fruits and simple
#: products that the AI may classify as plant_based_core (apple, pear,
#: tomato, …) are NOT in this list. Phase 36I aims at the systematic
#: error families surfaced by the audit, not every edge case.
_NOT_PLANT_CORE_PATTERNS: tuple[str, ...] = (
    # Fruit preparations
    "coulis",
    "compote",
    "compotes",
    "confiture",
    "confitures",
    "marmelade",
    "marmelades",
    "gelee de fruit",
    "miel",
    "sirop",
    "nectar",
    # Vegetable soups / purees
    "soupe",
    "soupes",
    "veloute",
    "veloutes",
    "gaspacho",
    "gazpacho",
    "mouline",
    "moulinee",
    "potage",
    "potages",
    "puree",
    "purees",
    # Cooked vegetable preparations
    "jardiniere",
    "ratatouille",
    "mais doux",
    "mais en conserve",
    "pommes de terre",
    "pomme de terre",
    "patate",
    "patates",
    "frite",
    "frites",
    "rosti",
    # Sauces / oils / dressings
    "sauce",
    "sauces",
    "huile",
    "huiles",
    "vinaigre",
    "vinaigrette",
    "ketchup",
    "mayonnaise",
    "tapenade",
    "pesto",
    # Single-vegetable simple products (only when unambiguous)
    "epinard",
    "epinards",
    "courgette",
    "courgettes",
    "brocoli",
    "brocolis",
    "carotte rapee",
    "carotte rappee",
    "carottes rapees",
    "carottes rappees",
    "betterave",
    "betteraves",
    "concombre",
    "concombres",
    "salade verte",
    "salade composee",
    "salade iceberg",
    "tomate",
    "tomates",
)


#: Tokens that signal a genuinely protein-rich plant food, which is
#: what ``plant_based_core`` is for. The plant_core guard only fires
#: when a NEGATIVE pattern (see ``_NOT_PLANT_CORE_PATTERNS``) is
#: matched; this allow-list serves as a SAFETY OVERRIDE — if any of
#: these protein-rich anchors appears, we keep plant_based_core even
#: when a NEGATIVE pattern is also present. E.g. "Soupe aux pois
#: chiches" keeps the chickpea protein anchor and stays in core.
_PLANT_CORE_TOKENS: tuple[str, ...] = (
    # Legumes / pulses
    "lentille",
    "lentilles",
    "pois chiche",
    "pois chiches",
    "chiche",
    "chiches",
    "haricot",
    "haricots",
    "haricot rouge",
    "haricot blanc",
    "haricot noir",
    "haricot azuki",
    "pois casse",
    "pois casses",
    "feve",
    "feves",
    "flageolet",
    "flageolets",
    "edamame",
    "soja jaune",
    "soja vert",
    "mungo",
    # Soy products / explicit plant protein
    "tofu",
    "tempeh",
    "seitan",
    "soja texture",
    "proteine de soja",
    "proteines de soja",
    "proteine vegetale",
    "proteines vegetales",
    "plant protein",
    "vegan protein",
    # Nuts / seeds
    "noix",
    "noisette",
    "noisettes",
    "amande",
    "amandes",
    "cacahuete",
    "cacahuetes",
    "pistache",
    "pistaches",
    "cajou",
    "noix de cajou",
    "pignon",
    "pignons",
    "noix de macadamia",
    "graine",
    "graines",
    "chia",
    "lin",
    "sesame",
    "tournesol",
    "courge",  # used in "graines de courge"
    # Plant substitutes (explicit meat/dairy analogs)
    "steak vegetal",
    "burger vegetal",
    "escalope vegetale",
    "nugget vegetal",
    "nuggets vegetaux",
    "boulette vegetale",
    "boulettes vegetales",
    "saucisse vegetale",
    "haché vegetal",
    "hache vegetal",
    "emince vegetal",
    "emince vegetale",
    "viande vegetale",
)


#: Tokens that mark a product as a beverage that's strictly
#: out_of_scope. No protein contribution, never tracked in PT runs.
_BEVERAGE_OUT_OF_SCOPE_TOKENS: tuple[str, ...] = (
    "the",       # thé (accent-folded)
    "tisane",
    "infusion",
    "cafe",      # café
    "limonade",
    "soda",
    "cola",
    "eau",
    "eau minerale",
    "eau gazeuse",
    "biere",     # bière
    "vin",
    "champagne",
    "spiritueux",
    "whisky",
    "vodka",
    "rhum",
    "gin",
    "liqueur",
    "alcool",
    "energy drink",
    "boisson energisante",
    "boisson energetique",
)


#: Tokens that mark a product as a fruit-derived drink that DOES
#: contribute (small amount of) protein and is in-scope as
#: plant_based_non_core.
_BEVERAGE_NON_CORE_TOKENS: tuple[str, ...] = (
    "smoothie",
    "smoothies",
    "pur jus",
    "purjus",
    "nectar",
    "nectars",
    "boisson fruitee",
    "boisson aux fruits",
    "boisson fruits",
    "fruit juice",
    "jus de fruit",
    "jus de fruits",
    "jus de pomme",
    "jus de raisin",
    "jus de orange",
    "jus d'orange",
    "jus de tomate",
    "jus de legumes",
    "jus de legume",
    "jus multifruits",
)


#: Sweet bakery / chocolate-confectionery products that are nearly
#: always composites (contain butter, milk, eggs, or dairy chocolate).
#: The classifier sometimes routes them to plant_based_non_core or
#: animal_core; the bakery guard moves them to composite_products.
_BAKERY_COMPOSITE_TOKENS: tuple[str, ...] = (
    "biscuit",
    "biscuits",
    "sable",     # sablé
    "sables",    # sablés
    "speculoos",
    "spéculoos",
    "madeleine",
    "madeleines",
    "financier",
    "financiers",
    "cookie",
    "cookies",
    "brownie",
    "brownies",
    "gateau",
    "gateaux",
    "tarte au",  # tarte au citron, tarte au chocolat — composite
    "tartelette",
    "croissant",
    "croissants",
    "pain au chocolat",
    "pain aux raisins",
    "viennoiserie",
    "viennoiseries",
    "brioche",
    "brioches",
    "kouign",
    "religieuse",
    "eclair",
    "eclairs",
    "macaron",
    "macarons",
    "tablette lait",
    "tablette de lait",
    "chocolat au lait",
    "chocolat lait",
    "praline",
    "pralines",
    "truffe au chocolat",
)


#: Animal-source tokens — when these appear together with a
#: "prepared dish" marker (sauce, légumes, féculent, gratin,
#: poêlée…), the animal_prepared_meal guard reroutes the verdict
#: from ``animal_core`` to ``composite_products``.
_ANIMAL_TOKENS: tuple[str, ...] = (
    "poulet",
    "dinde",
    "canard",
    "boeuf",
    "boeuf",  # accent-folded variant
    "veau",
    "porc",
    "agneau",
    "jambon",
    "saumon",
    "thon",
    "cabillaud",
    "merlu",
    "truite",
    "sardine",
    "maquereau",
    "hareng",
    "crevette",
    "crevettes",
    "moule",
    "moules",
    "huitre",
    "calamar",
    "poulpe",
    "homard",
    "crabe",
    "surimi",
    "oeuf",
    "œuf",
    "lardon",
    "lardons",
    "bacon",
    "chorizo",
    "merguez",
)


#: Composite-dish names that ALWAYS imply animal + starch + sauce
#: by tradition, even when the product name doesn't explicitly list
#: the animal ingredient. E.g. "Cassoulet Provençale" → cassoulet
#: always contains saucisse + lardons + haricots; "Lasagnes
#: Bolognaise" → bolognaise always contains beef.
_SELF_EVIDENT_ANIMAL_COMPOSITES: tuple[str, ...] = (
    "cassoulet",
    "blanquette",
    "bourguignon",
    "bolognaise",
    "bolognese",
    "carbonara",
    "milanaise",
    "savoyarde",
    "choucroute",
    "hachis parmentier",
    "parmentier",
    "tartiflette",
    "raclette",
    "fondue",
    "pot au feu",
    "pot-au-feu",
    "boeuf bourguignon",
    "navarin",
    "osso bucco",
    "stroganoff",
)


#: "Prepared dish" markers. Their presence next to an animal token
#: signals a composite, not a simple animal food. Includes typical
#: dish formats AND broad categories (salade, soupe, poêlée…).
_PREPARED_DISH_MARKERS: tuple[str, ...] = (
    "curry",
    "parmentier",
    "poelee",        # poêlée
    "salade",
    "soupe",
    "veloute",
    "gaspacho",
    "moulinee",      # mouliné
    "mouline",
    "cassoulet",
    "blanquette",
    "bourguignon",
    "lasagne",
    "lasagnes",
    "ratatouille",
    "tajine",
    "tagine",
    "paella",
    "risotto",
    "gratin",
    "quiche",
    "tourte",
    "tarte",
    "pizza",
    "wrap",
    "sandwich",
    "burger",
    "nuggets",       # composite if breaded/coated
    "cordon bleu",
    "pâtes au",      # "pâtes au saumon"
    "pates au",
    "riz au",
    "tagliatelle",
    "spaghetti",
    "ravioli",
    "raviolis",
    "gnocchi",
    "couscous",
    "chili",
    "boulgour",
    "feuillete",     # feuilleté
    "vol au vent",
    "bouchee",
    "bouchee a la reine",
    "preparation",
    "plat cuisine",
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardOverride:
    """Returned when a guard fires.

    The batch classifier uses this to:
      * substitute the corrected classification,
      * extend the rationale with the override reason,
      * clamp confidence to ``_GUARD_CONFIDENCE_CEILING`` so the
        verdict lands in ``needs_review`` (never auto-accept a
        guard-corrected category silently).
    """

    rule: str
    new_classification: ProteinTrackerProductClassification


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_pt_guards(
    product_name: str,
    classification: ProteinTrackerProductClassification,
) -> GuardOverride | None:
    """Apply the four Phase 36I guards in order.

    Returns ``None`` when no guard fires (the caller keeps the
    original verdict). Returns a :class:`GuardOverride` otherwise —
    the caller substitutes ``override.new_classification`` and marks
    the row for review.
    """
    name = _normalise(product_name)
    pt_group = classification.pt_group

    # Guard 1 — plant_core demoted when name matches a "clearly not
    # core" pattern (coulis/compote/soupe/velouté/gaspacho/sauce/
    # huile/single-veg…). The protein-rich token list acts as a
    # safety override: "Soupe aux pois chiches" keeps the chickpea
    # anchor and stays in plant_based_core.
    if pt_group is ProteinTrackerGroup.PLANT_BASED_CORE:
        if _contains_any_word(
            name, _NOT_PLANT_CORE_PATTERNS
        ) and not _contains_any_word(name, _PLANT_CORE_TOKENS):
            return _override(
                classification,
                ProteinTrackerGroup.PLANT_BASED_NON_CORE,
                rule="plant_core_demoted_preparation_or_simple_veg",
                detail=(
                    "name matches a coulis/compote/soupe/velouté/"
                    "gaspacho/sauce/huile/simple-veg pattern with no "
                    "protein-rich anchor; demoted to "
                    "plant_based_non_core"
                ),
            )

    # Guard 2 — beverages.
    # 2a: tea/coffee/soda/water/alcohol — strict out_of_scope.
    if _contains_any_word(name, _BEVERAGE_OUT_OF_SCOPE_TOKENS):
        # Skip if the name ALSO contains a plant-milk anchor —
        # boisson avoine / soja / amande etc. carry "boisson" which
        # is NOT in our oos vocabulary, so they're safe. The check
        # below specifically protects fruit-juice products which
        # the model can legitimately classify as plant_based_non_core.
        if pt_group is not ProteinTrackerGroup.OUT_OF_SCOPE and not _contains_any_word(
            name, _BEVERAGE_NON_CORE_TOKENS
        ):
            return _override(
                classification,
                ProteinTrackerGroup.OUT_OF_SCOPE,
                rule="beverage_out_of_scope",
                detail=(
                    "name contains tea/coffee/soda/water/alcohol "
                    "token; routed to out_of_scope"
                ),
            )
    # 2b: fruit juices / smoothies — plant_based_non_core.
    if _contains_any_word(name, _BEVERAGE_NON_CORE_TOKENS) and pt_group in {
        ProteinTrackerGroup.UNKNOWN,
        ProteinTrackerGroup.OUT_OF_SCOPE,
    }:
        return _override(
            classification,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            rule="fruit_drink_non_core",
            detail=(
                "name contains smoothie/jus/nectar/boisson-fruitée "
                "token; routed to plant_based_non_core"
            ),
        )

    # Guard 3 — sweet bakery / chocolate-confectionery composites.
    if pt_group in {
        ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        ProteinTrackerGroup.PLANT_BASED_CORE,
        ProteinTrackerGroup.ANIMAL_CORE,
    } and _contains_any_word(name, _BAKERY_COMPOSITE_TOKENS):
        return _override(
            classification,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            rule="bakery_composite",
            detail=(
                "name matches biscuit/sablé/croissant/viennoiserie/"
                "chocolat-au-lait token; routed to composite_products"
            ),
        )

    # Guard 4 — animal-source prepared meal → composite.
    # Fires when EITHER:
    #   (a) the name contains an animal token AND a prepared-dish
    #       marker (curry/poêlée/salade…) — the explicit case;
    #   (b) the name contains a self-evident animal composite
    #       (cassoulet/lasagne bolognaise/blanquette…) — the
    #       traditional French dish case where the animal is
    #       implicit.
    if pt_group is ProteinTrackerGroup.ANIMAL_CORE:
        explicit = _contains_any_word(
            name, _ANIMAL_TOKENS
        ) and _contains_any_word(name, _PREPARED_DISH_MARKERS)
        self_evident = _contains_any_word(
            name, _SELF_EVIDENT_ANIMAL_COMPOSITES
        )
        if explicit or self_evident:
            return _override(
                classification,
                ProteinTrackerGroup.COMPOSITE_PRODUCTS,
                rule="animal_prepared_meal_composite",
                detail=(
                    "name contains animal token + prepared-dish "
                    "marker, or is a self-evident animal composite "
                    "(cassoulet/lasagne bolognaise/blanquette…); "
                    "routed to composite_products"
                ),
            )

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _override(
    original: ProteinTrackerProductClassification,
    new_group: ProteinTrackerGroup,
    *,
    rule: str,
    detail: str,
) -> GuardOverride:
    """Build a corrected classification + override descriptor.

    The new classification inherits everything from the original
    except:
      * ``pt_group`` is the corrected category,
      * ``confidence`` is clamped to ``_GUARD_CONFIDENCE_CEILING``,
      * ``review_reason`` (when source allows) carries the rule
        identifier so the wizard can show "AI guard corrected: …".
    """
    new_confidence = min(original.confidence, _GUARD_CONFIDENCE_CEILING)
    new_classification = original.model_copy(
        update={
            "pt_group": new_group,
            "confidence": new_confidence,
        }
    )
    _ = detail  # surfaced via the orchestrator's sample_errors
    return GuardOverride(rule=rule, new_classification=new_classification)

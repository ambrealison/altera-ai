"""Phase Quality-V2-A/B — NEVO matching V2: precision-first gating.

Offline only — NOT production-wired. These rules do not perform the
actual NEVO lookup; they *gate* a candidate so a match is only
high-confidence when it is safe. The governing principle: a confident
WRONG match is worse than no match — abstaining/review beats it.

The gate decides, for a (product_name, candidate) pair, one of:
  * exact  — product head literally equals the candidate head.
  * alias  — product and candidate map to the same canonical concept
             across FR/EN (pois chiches ↔ chickpeas, lait ↔ milk, …).
  * proxy  — plausible but not head-exact → review_required, NOT
             high-confidence.
  * rejected / abstain — secondary-ingredient traps, with/without
             qualifiers, or unrelated concepts.

Every decision carries a trace (candidate, accepted, match_type,
confidence, reason) so the evaluator + a future review UI can explain
why a candidate was accepted or rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from altera_api.classification_v2.pt_rules import _norm

# Phase Quality-V2-F — preparation STATE words (boiled, canned, dried, …)
# describe a SIMPLE food in a particular form. They are SAFE: a candidate
# that is "simple food + preparation state" ("Peas chick boiled",
# "Lentils red boiled", "Beans black canned") is still a clean match for
# the same-concept product. They are NOT composite-dish markers.
_PREPARATION_STATES = frozenset({
    "boiled", "cooked", "canned", "dried", "frozen", "fresh", "raw",
    "drained", "roasted", "steamed", "unprepared", "baked", "prepared",
    "av", "average", "grilled", "stewed",  # 'stewed' = state, 'stew' = dish
})
# Phase Quality-V2-F — COMPOSITE markers. A candidate carrying one of
# these is a prepared/mixed DISH, not a simple food:
#   * joiners introduce a different ingredient ("X with Y", "X without Y",
#     and the NEVO shorthands " w " = with, " wo " = without);
#   * dish nouns name a composite product (soup, pie, lasagne, …).
# A composite candidate is rejected UNLESS its HEAD concept (the food
# BEFORE the first marker) equals the product concept — so "Hummus with
# chickpeas" (head=hummus) is rejected for a chickpea product, while
# "Ratatouille prepared wo meat" (head=ratatouille) still matches a
# ratatouille product.
_COMPOSITE_JOINERS = frozenset({"with", "without", "avec", "sans", "w", "wo"})
_DISH_NOUNS = frozenset({
    "soup", "stew", "casserole", "pie", "pizza", "lasagne", "lasagna",
    "lasagnes", "curry", "gratin", "quiche", "sauce", "tart",
    "cake", "pudding", "salad", "smoothie", "bar", "biscuit", "spread",
    "dish", "meal", "bolognaise", "bolognese", "wrap", "sandwich", "burger",
})
# Phase Quality-V2-K — ``hummus`` is BOTH a product and a trap-ingredient.
# It is a concept (so "Houmous" matches "Hummus natural"), not a dish noun;
# the "Hummus with chickpeas" trap is still rejected for a chickpea product
# via the JOINER ("with") head logic (head = hummus != chickpea).
# Concepts that are fundamentally secondary ingredients — a candidate
# whose head IS one of these can never be the primary match for a
# product of a different concept (even if embeddings rank it highly).
_QUALIFIER_CONCEPTS = frozenset({"oil", "olive", "garlic"})

# Canonical concept → surface forms (FR + EN + the inverted NEVO 2025
# naming, e.g. "Peas chick", "Beans black", "Lentils red"). Multi-word
# phrases are matched with phrase preference so "beurre de cacahuete"
# resolves to ``peanut_butter`` rather than ``butter`` and "pois chiches"
# resolves to ``chickpea`` rather than the bare head ``pois`` (Phase
# Quality-V2-F).
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "peanut_butter": ("beurre de cacahuete", "beurre de cacahuetes", "peanut butter"),
    "black_bean": (
        "haricot noir", "haricots noirs", "black bean", "black beans",
        "beans black",  # NEVO inverted naming
    ),
    "chickpea": (
        "pois chiche", "pois chiches", "chickpea", "chickpeas",
        "peas chick", "kikkererwten",  # NEVO English + Dutch
    ),
    "lentil": (
        "lentille", "lentilles", "lentilles corail", "lentil", "lentils",
        "lentils red", "lentils green",  # NEVO inverted naming
    ),
    "bean": ("haricot", "haricots", "bean", "beans"),
    "tofu": ("tofu",),
    "tempeh": ("tempeh",),
    "seitan": ("seitan",),
    "milk": ("lait", "milk"),
    "yoghurt": ("yaourt", "yogurt", "yoghurt"),
    "quark": ("quark", "fromage blanc", "fresh cheese"),
    "cheese": ("fromage", "cheese"),
    "butter": ("beurre", "butter"),
    "oil": ("huile", "oil"),
    "olive": ("olive", "olives"),
    "garlic": ("ail", "garlic"),
    "ratatouille": ("ratatouille",),
    "pasta": ("pates", "pates alimentaires", "pasta", "spaghetti", "macaroni",
              "penne"),
    "muesli": ("muesli", "granola"),
    "rice": ("riz", "rice"),
    "potato": ("pomme de terre", "pommes de terre", "potato", "potatoes",
               "patate", "patates"),
    "apple": ("pomme", "pommes", "apple", "apples"),
    "tomato": ("tomate", "tomates", "tomato", "tomatoes"),
    # Phase Quality-V2-J — real FR retailer foods. FR product forms + the
    # EN/NEVO reference names so a French product resolves to the same
    # concept as its English NEVO entry. Ingredient-token traps are NOT
    # added here — they are handled by the composite/dish-noun head logic
    # (e.g. "Beans white baked in tomato sauce", "Chicken schnitzel … w
    # corn flakes", "Biscuit Cafe noir" all have a non-matching head).
    "chocolate": ("chocolat", "chocolate"),
    "tuna": ("thon", "tuna"),
    # sweet corn ONLY — never bare "corn"/"mais" (that would swallow
    # "Corn starch"/"Corn flour"); requires the sweet/doux qualifier.
    "sweet_corn": ("mais doux", "sweetcorn", "sweet corn", "corn sweet"),
    "corn_flakes": ("corn flakes", "cornflakes"),
    "orange_juice": ("jus d orange", "jus orange", "juice orange", "orange juice"),
    "coffee": ("cafe", "coffee"),
    # tea: avoid the bare English word "the" (too common); use FR phrases
    # + EN tea words.
    "tea": ("the noir", "the vert", "the glace", "the infusion", "earl grey",
            "tea", "green tea", "black tea"),
    "soup": ("soupe", "veloute", "soup"),
    "tomato_sauce": ("sauce tomate", "sauce tomato", "tomato sauce"),
    # Phase Quality-V2-K — more real FR retailer foods. Same rules: FR
    # product forms + EN/NEVO reference names; traps stay rejected by the
    # composite/dish-noun head logic and by precise (non-bare) forms.
    "mustard": ("moutarde", "mustard"),
    "vinegar": ("vinaigre", "vinegar", "balsamique", "balsamic"),
    "vinaigrette": ("vinaigrette",),
    "crisps": ("chips", "crisps"),
    "quinoa": ("quinoa",),
    # semolina ≈ couscous (both wheat semolina); never bare "ble".
    "couscous": ("couscous", "semoule", "semolina"),
    # wheat flour only — NEVER bare "flour"/"corn" (that would swallow
    # "Flour corn"/"Corn starch").
    "wheat_flour": ("farine", "farine de ble", "flour wheat", "wheat flour"),
    "sugar": ("sucre", "sugar"),
    "bread": ("pain", "pain de mie", "pain complet", "bread"),
    "honey": ("miel", "honey"),
    "jam": ("confiture", "jam"),
    # phrase forms beat the bare "cheese" concept at the same position.
    "mozzarella": ("mozzarella", "cheese mozzarella"),
    "feta": ("feta", "cheese feta"),
    "creme_fraiche": ("creme fraiche",),
    "margarine": ("margarine",),
    "ham": ("jambon", "ham"),
    "chicken": ("poulet", "chicken"),
    "egg": ("oeuf", "oeufs", "egg"),
    "salmon": ("saumon", "salmon"),
    "hummus": ("houmous", "hummus"),
    "almond_drink": ("boisson amande", "lait amande", "drink almond",
                     "almond drink", "almond milk"),
    "sorbet": ("sorbet",),
}


@dataclass(frozen=True)
class NevoCandidate:
    nevo_code: str
    food_name_en: str


@dataclass(frozen=True)
class NevoGateResult:
    accepted: bool
    confidence: float
    reason: str
    match_type: str = "none"  # exact | alias | proxy | rejected | abstain
    review_required: bool = False


def _significant_tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) >= 3]


def _primary_head(text: str) -> str | None:
    toks = _significant_tokens(text)
    return toks[0] if toks else None


def _concept_of_norm(norm_str: str) -> str | None:
    """Concept of a normalised (``_norm``-padded) string. Phrase
    preference on ties so the longest/earliest surface form wins."""
    best: tuple[int, int, str] | None = None  # (position, -length, concept)
    for concept, forms in _CONCEPTS.items():
        for form in forms:
            idx = norm_str.find(f" {form} ")
            if idx == -1:
                continue
            key = (idx, -len(form), concept)
            if best is None or key < best:
                best = key
    return best[2] if best else None


def concept_of(text: str) -> str | None:
    """The canonical concept of a text (full text).

    Used for PRODUCTS, where the meaningful food may be a trailing word
    ("Soupe lentilles coco" → ``lentil``). For CANDIDATES use
    :func:`_head_concept`, which ignores anything after a composite
    marker so "Hummus with chickpeas" is recognised as a *dish* (head
    ``hummus``), not as chickpeas."""
    return _concept_of_norm(_norm(text))


def _first_joiner_index(tokens: list[str]) -> int | None:
    for i, tok in enumerate(tokens):
        if tok in _COMPOSITE_JOINERS:
            return i
    return None


def _has_dish_noun(tokens: list[str]) -> bool:
    return any(tok in _DISH_NOUNS for tok in tokens)


def _is_composite(text: str) -> bool:
    """A candidate is composite if it names a prepared dish (a dish noun
    anywhere) or joins a different ingredient ("X with/without Y")."""
    toks = _norm(text).split()
    return _has_dish_noun(toks) or _first_joiner_index(toks) is not None


def _head_concept(text: str) -> str | None:
    """Concept of the candidate's MAIN food.

    * A DISH-NOUN candidate ("Apple pie without sugar", "Muesli bar",
      "Soup with tomato", "Hummus with chickpeas") IS a prepared dish —
      the leading word is only a modifier of the dish — so it has no
      simple-food identity → ``None``.
    * A JOINER-only candidate ("Ratatouille prepared wo meat", "Apple w
      skin av") is the food BEFORE the joiner → its concept.
    * A simple food, possibly with a preparation state ("Peas chick
      boiled"), → its concept (whole text)."""
    toks = _norm(text).split()
    if _has_dish_noun(toks):
        return None
    j = _first_joiner_index(toks)
    prefix = toks if j is None else toks[:j]
    return _concept_of_norm(f" {' '.join(prefix)} ")


def decide_candidate(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    """Precision-first decision for one (product, candidate) pair.

    Order: (1) qualifier-ingredient trap, (2) composite-dish rejection,
    (3) concept (alias) match, (4) exact primary-head match, (5) weaker
    literal-token match → REVIEW (never a silent high-confidence accept),
    else abstain. A confident WRONG match is worse than no match."""
    p_head = _primary_head(product_name)
    if p_head is None:
        return NevoGateResult(False, 0.0, "No usable product head.", "abstain")

    prod_concept = concept_of(product_name)
    cand_head_concept = _head_concept(candidate.food_name_en)
    cand_primary_head = _primary_head(candidate.food_name_en)
    cand_tokens = set(_significant_tokens(candidate.food_name_en))
    cand_is_composite = _is_composite(candidate.food_name_en)

    # 1. Qualifier-ingredient trap: the candidate's HEAD is fundamentally
    #    an oil/olive/garlic (a typical secondary ingredient) but the
    #    product is a different concept. Hard-reject regardless of how the
    #    embeddings ranked it ("Ratatouille à l'huile d'olive" must never
    #    match "Oil olive").
    if (
        cand_head_concept in _QUALIFIER_CONCEPTS
        and prod_concept is not None
        and prod_concept != cand_head_concept
    ):
        return NevoGateResult(
            False, 0.0,
            f"Candidate is a {cand_head_concept!r} (a secondary ingredient), "
            f"not the product concept {prod_concept!r}.",
            "rejected",
        )

    # 2. Composite/prepared DISH rejection (Phase Quality-V2-F). A
    #    candidate with a joiner ("with/without/w/wo") or a dish noun
    #    ("soup/pie/lasagne/…") is a mixed dish. It is rejected UNLESS its
    #    head food is the SAME concept as the product (so "Ratatouille
    #    prepared wo meat" still matches a ratatouille product, while
    #    "Hummus with chickpeas" / "Apple pie without butter" /
    #    "Potatoes mashed with milk" are rejected — the product is only a
    #    secondary ingredient or absent). Simple preparation states
    #    (boiled/canned/dried/…) are NOT composite markers.
    if cand_is_composite and cand_head_concept != prod_concept:
        return NevoGateResult(
            False, 0.0,
            "Candidate is a composite/prepared dish whose main food differs "
            f"from the product (head concept {cand_head_concept!r} != "
            f"{prod_concept!r}); the product is only a secondary ingredient.",
            "rejected",
        )

    # 3. Concept (alias) match across FR/EN/NL + NEVO naming — the
    #    candidate HEAD must be the product's concept ("Pois chiches" →
    #    "Peas chick boiled", both ``chickpea``).
    if prod_concept is not None and prod_concept == cand_head_concept:
        return NevoGateResult(
            True, 0.96, f"Concept match: {prod_concept!r}.", "alias",
        )

    # 4. Exact primary-head match — both names lead with the same head
    #    token (same-language simple foods with no mapped concept, e.g.
    #    "Date" ↔ "Date dried"). If the PRODUCT resolves to a concept, a
    #    bare head-token match is only safe via the concept path (step 3);
    #    a candidate that does not share that concept — including one with
    #    NO concept at all ("Corn Flakes" head 'corn' vs "Corn starch") —
    #    is rejected here (Phase Quality-V2-J).
    if cand_primary_head is not None and p_head == cand_primary_head:
        if prod_concept is not None:
            return NevoGateResult(
                False, 0.0,
                f"Heads match ({p_head!r}) but the candidate does not share the "
                f"product concept {prod_concept!r} (candidate concept "
                f"{cand_head_concept!r}).",
                "rejected",
            )
        return NevoGateResult(True, 0.95, f"Exact head match: {p_head!r}.", "exact")

    # 5. Weaker literal-token match: the product head appears somewhere in
    #    the candidate but is not its primary head. This is the
    #    high-confidence-false-positive risk class (Phase Quality-V2-F /
    #    PART D).
    if p_head in cand_tokens:
        # Product has a known concept the candidate doesn't share → reject.
        if prod_concept is not None and cand_head_concept != prod_concept:
            return NevoGateResult(
                False, 0.0,
                f"Product resolves to concept {prod_concept!r}; candidate "
                f"(head concept {cand_head_concept!r}) does not share it.",
                "rejected",
            )
        # No reliable concept/head agreement → REVIEW, never auto-accept.
        return NevoGateResult(
            False, 0.6,
            f"Literal token {p_head!r} present but not the candidate head — "
            "needs review, not auto-accept.",
            "proxy", review_required=True,
        )

    # 6. Otherwise: not safe for high confidence → abstain.
    return NevoGateResult(
        False, 0.0,
        f"No safe head/concept match for {p_head!r} → abstain.",
        "abstain",
    )


def gate_candidate(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    """Public entry point used by the evaluator + tests."""
    return decide_candidate(product_name, candidate)


# Backwards-compatible individual gates (kept for the Quality-V2-A
# tests). Each returns a NevoGateResult; ``gate_candidate`` above is the
# integrated decision.
def head_match_required(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    p_head = _primary_head(product_name)
    if p_head is None:
        return NevoGateResult(False, 0.0, "No usable product head — abstain.", "abstain")
    if p_head in set(_significant_tokens(candidate.food_name_en)):
        return NevoGateResult(True, 0.95, f"Primary head match: {p_head!r}", "exact")
    return NevoGateResult(
        False, 0.0, f"Primary product head {p_head!r} not in candidate — reject.",
        "rejected",
    )


def reject_secondary_ingredient(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    r = decide_candidate(product_name, candidate)
    return r


def reject_with_without_trap(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    # A candidate that is a composite DISH whose head food differs from
    # the product is a with/without trap (e.g. "Apple pie without butter"
    # for a butter product).
    prod_concept = concept_of(product_name)
    if _is_composite(candidate.food_name_en) and _head_concept(
        candidate.food_name_en
    ) != prod_concept:
        return NevoGateResult(
            False, 0.0, "Candidate is a composite dish with a different main food.",
            "rejected",
        )
    return NevoGateResult(True, 0.9, "No with/without composite trap.", "exact")

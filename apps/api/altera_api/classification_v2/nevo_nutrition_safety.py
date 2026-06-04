"""Phase Quality-V2-P — second-stage nutrition-safety policy.

The NEVO V2 matcher decides whether a candidate is the right *food concept*.
For NUTRITION enrichment the exact *physical state* also matters: dry vs
cooked, canned vs dried, brewed vs instant/powder, plain vs sweetened, a
whole food vs a processing proxy (syrup/concentrate). A concept-correct
match can still be a nutrition-wrong source.

This module adds a SECOND stage, used only by the dry-run proposals tool
(``nevo_v2_enrich``). It never changes the matcher gates and never writes
anything. It downgrades an otherwise-enrichable match to review/skip when
the product and the matched NEVO reference differ materially on state.
"""

from __future__ import annotations

from altera_api.classification_v2.nevo_rules import _norm, concept_of

_AUTO_ACCEPT_THRESHOLD = 0.90

# Concepts where cooked/dried/canned state changes nutrition a lot (water).
_STATE_SENSITIVE_CONCEPTS = frozenset({
    "pasta", "rice", "couscous", "lentil", "bean", "black_bean", "chickpea",
    "green_peas", "quinoa", "sweet_corn",
})

# State token groups (EN + FR).
_COOKED = frozenset({
    "cooked", "boiled", "prepared", "simmered", "stewed",
    "cuit", "cuite", "cuits", "cuites", "bouilli", "bouillie",
})
_DRIED = frozenset({"dried", "dry", "dehydrated", "sec", "seche", "seches"})
_RAW = frozenset({"raw", "cru", "crue", "crues"})
_CANNED = frozenset({
    "canned", "tinned", "glass", "jar", "naturel", "conserve", "bocal", "boite",
})

# Reference words that mark a processing proxy, not a whole-food source.
_PROXY_TOO_BROAD = frozenset({
    "syrup", "rinse", "essence", "aroma", "arome", "flavour", "flavor",
    "concentrate", "concentrated", "extract",
})

# Beverage processing markers (instant/powder/sweetened/herbal/brewed…).
_BEV_PROCESSED = frozenset({
    "instant", "powder", "soluble", "cappuccino", "sweetened", "sweetend",
    "sugar", "herbal", "prepared", "brewed", "latte", "mix",
})

# --- Quality-V2-Q — targeted final filters -------------------------------

# A whole food matched to a "drink"/"boisson" reference is a processed
# plant/beverage proxy, not the food itself (rice grain vs rice drink).
_DRINK_FORM = frozenset({"drink", "boisson", "milkdrink"})
# Concepts that ARE beverages — a drink reference is fine for these.
_BEVERAGE_CONCEPTS = frozenset({
    "almond_drink", "orange_juice", "coffee", "tea",
})

# Vinegar variety token → canonical type (cider vinegar != balsamic vinegar).
_VINEGAR_TYPES = {
    "cider": "cider", "cidre": "cider", "apple": "cider",
    "balsamic": "balsamic", "balsamique": "balsamic",
    "wine": "wine", "vin": "wine",
    "white": "white", "blanc": "white", "blanche": "white",
    "rice": "rice", "riz": "rice",
    "sherry": "sherry", "xeres": "sherry",
    "raspberry": "raspberry", "framboise": "raspberry",
    "malt": "malt",
}

# Oil variety token → canonical type, plus blend/margarine markers.
_OIL_MARKERS = frozenset({"oil", "huile"})
_OIL_TYPES = {
    "colza": "rapeseed", "rapeseed": "rapeseed", "canola": "rapeseed",
    "olive": "olive", "tournesol": "sunflower", "sunflower": "sunflower",
    "arachide": "peanut", "peanut": "peanut", "sesame": "sesame",
    "lin": "linseed", "linseed": "linseed", "flax": "linseed",
    "noix": "walnut", "walnut": "walnut", "coco": "coconut",
    "coconut": "coconut", "pepins": "grapeseed", "grapeseed": "grapeseed",
    "soja": "soy", "soy": "soy",
}
_OIL_BLEND_MARGARINE = frozenset({
    "blend", "margarine", "becel", "spread", "tartine", "minarine",
})

# Instant/dehydrated potato puree vs a prepared mash with added milk/fat.
_PUREE_DRY = frozenset({
    "mousseline", "flakes", "flocons", "instant", "deshydrate",
    "deshydratee", "deshydrates", "poudre", "sachet", "sachets", "powder",
})
_PREPARED_FAT = frozenset({
    "milk", "lait", "margarine", "butter", "beurre", "cream", "creme",
    "prepared", "whole",
})

# Fruit variety token → canonical (wrong-fruit jam).
_FRUITS = {
    "apricot": "apricot", "abricot": "apricot",
    "strawberry": "strawberry", "fraise": "strawberry", "fraises": "strawberry",
    "raspberry": "raspberry", "framboise": "raspberry", "framboises": "raspberry",
    "apple": "apple", "pomme": "apple", "pommes": "apple",
    "pear": "pear", "poire": "pear",
    "peach": "peach", "peche": "peach",
    "cherry": "cherry", "cerise": "cherry", "cerises": "cherry",
    "orange": "orange", "oranges": "orange",
    "lemon": "lemon", "citron": "lemon",
    "fig": "fig", "figue": "fig",
    "blueberry": "blueberry", "myrtille": "blueberry", "myrtilles": "blueberry",
    "blackberry": "blackberry", "mure": "blackberry", "mures": "blackberry",
    "rosehip": "rosehip", "eglantine": "rosehip", "hip": "rosehip",
    "plum": "plum", "prune": "plum", "prunes": "plum",
    "mango": "mango", "mangue": "mango",
    "blackcurrant": "blackcurrant", "cassis": "blackcurrant",
    "grape": "grape", "raisin": "grape", "raisins": "grape",
    "rhubarb": "rhubarb", "rhubarbe": "rhubarb",
    "quince": "quince", "coing": "quince",
}

# Snack bases + flavour-noise (generic-snack-proxy detection).
_SNACK_BASE = frozenset({
    "cracker", "crackers", "crisps", "crisp", "chips", "chip",
    "tortilla", "tortillas",
})
_SNACK_TOKENS = frozenset({"cracker", "crackers", "crisps", "crisp",
                           "chips", "chip"})
_SNACK_CONCEPTS = frozenset({"crisps", "tortilla_crisps"})
_SNACK_NOISE = frozenset({
    "de", "des", "du", "la", "le", "les", "au", "aux", "a", "l", "d", "et",
    "sans", "avec", "bio", "nature", "naturel", "naturels", "pack", "lot",
    "format", "family", "maxi", "x", "g", "kg", "cl", "ml", "potato",
    "potatoes", "unflavoured", "unflavored", "plain", "light", "the",
    "with", "and",
})

_BASE_REASONS = {
    "skip_no_match": "matcher produced no candidate",
    "route_to_review": "matcher result is review-level / low-confidence",
    "skip_no_nutrition_value": "matched reference has no nutrition value",
}

#: every nutrition_safety_action value (for summaries/tests).
NUTRITION_SAFETY_ACTIONS = (
    "would_enrich", "route_to_review", "skip_no_match",
    "skip_no_nutrition_value", "skip_state_mismatch", "skip_proxy_too_broad",
)


def base_safety_action(
    *, matched: bool, review_required: bool, protein: float | None,
    confidence: float,
) -> str:
    """Stage-1 (matcher + value) gate — independent of physical state."""
    if not matched:
        return "skip_no_match"
    if review_required or confidence < _AUTO_ACCEPT_THRESHOLD:
        return "route_to_review"
    if protein is None:
        return "skip_no_nutrition_value"
    return "would_enrich"


def _staple_state(tokens: set[str]) -> str | None:
    if tokens & _CANNED:
        return "canned"
    if tokens & _COOKED:
        return "cooked"
    if tokens & _DRIED:
        return "dried"
    if tokens & _RAW:
        return "raw"
    return None


def _state_mismatch(p_tokens: set[str], r_tokens: set[str]) -> tuple[str, str] | None:
    # A packaged staple with no explicit state is treated as dry.
    ps = _staple_state(p_tokens) or "dry"
    rs = _staple_state(r_tokens)
    if rs == "cooked" and ps != "cooked":
        return (
            "skip_state_mismatch",
            "product is dry/packaged but the reference is cooked "
            "(nutrition differs by water content)",
        )
    if ps == "cooked" and rs in ("dried", "raw"):
        return (
            "skip_state_mismatch",
            "product is cooked but the reference is dried/raw",
        )
    return None


def _beverage_mismatch(p_tokens: set[str], r_tokens: set[str]) -> tuple[str, str] | None:
    if (r_tokens & _BEV_PROCESSED) and not (p_tokens & _BEV_PROCESSED):
        bad = sorted(r_tokens & _BEV_PROCESSED)
        return (
            "skip_state_mismatch",
            f"reference is a processed/instant/sweetened beverage "
            f"({', '.join(bad)}); the product is whole/plain",
        )
    return None


def _varieties(tokens: set[str], lexicon: dict[str, str]) -> set[str]:
    return {lexicon[t] for t in tokens if t in lexicon}


def _drink_form_mismatch(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    """A whole food matched to a beverage 'drink' reference (rice grain vs
    rice drink) — a processed plant/beverage proxy, not the food."""
    if concept in _BEVERAGE_CONCEPTS or (p_tokens & _DRINK_FORM):
        return None
    if r_tokens & _DRINK_FORM:
        return (
            "skip_proxy_too_broad",
            "reference is a plant/beverage drink, not the whole food "
            "(different nutrition basis)",
        )
    return None


def _vinegar_type_mismatch(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    if concept != "vinegar":
        return None
    pv = _varieties(p_tokens, _VINEGAR_TYPES)
    rv = _varieties(r_tokens, _VINEGAR_TYPES)
    if pv and rv and pv.isdisjoint(rv):
        return (
            "skip_proxy_too_broad",
            f"wrong vinegar type: product is {'/'.join(sorted(pv))} but the "
            f"reference is {'/'.join(sorted(rv))} vinegar",
        )
    return None


def _jam_fruit_mismatch(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    is_jam = concept == "jam" or bool(
        (p_tokens | r_tokens) & {"jam", "confiture", "marmalade", "marmelade"}
    )
    if not is_jam:
        return None
    pf = _varieties(p_tokens, _FRUITS)
    rf = _varieties(r_tokens, _FRUITS)
    if pf and rf and pf.isdisjoint(rf):
        return (
            "skip_proxy_too_broad",
            f"wrong fruit: product is {'/'.join(sorted(pf))} jam but the "
            f"reference is {'/'.join(sorted(rf))} jam",
        )
    return None


def _oil_mismatch(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    if concept != "oil" and not (p_tokens & _OIL_MARKERS):
        return None
    if r_tokens & _OIL_BLEND_MARGARINE:
        return (
            "route_to_review",
            "product is a pure oil but the reference is a branded "
            "blend/margarine-like spread — confirm the fat profile before "
            "enriching",
        )
    pv = _varieties(p_tokens, _OIL_TYPES)
    rv = _varieties(r_tokens, _OIL_TYPES)
    if pv and rv and pv.isdisjoint(rv):
        return (
            "route_to_review",
            f"wrong oil type: product is {'/'.join(sorted(pv))} oil but the "
            f"reference is {'/'.join(sorted(rv))} oil — confirm or pick a "
            "matching / generic vegetable oil",
        )
    return None


def _potato_puree_mismatch(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    if concept != "potato":
        return None
    if (p_tokens & _PUREE_DRY) and (r_tokens & _PREPARED_FAT):
        bad = sorted(r_tokens & _PREPARED_FAT)
        return (
            "skip_state_mismatch",
            "product is an instant/dehydrated puree but the reference is a "
            f"prepared mash with added {', '.join(bad)} "
            "(different fat/water basis)",
        )
    return None


def _snack_generic_proxy(
    concept: str | None, p_tokens: set[str], r_tokens: set[str]
) -> tuple[str, str] | None:
    """Flavoured/specific snack matched to a generic/unflavoured snack
    reference — acceptable only as a deliberate proxy, so route to review
    instead of silently auto-enriching."""
    is_snack = concept in _SNACK_CONCEPTS or bool(p_tokens & _SNACK_TOKENS)
    if not is_snack:
        return None
    p_flavour = {
        t for t in p_tokens
        if t not in _SNACK_BASE and t not in _SNACK_NOISE
        and not t.isdigit() and len(t) > 2
    }
    if p_flavour and not (p_flavour <= r_tokens):
        return (
            "route_to_review",
            "generic snack proxy: a flavoured/specific snack is matched to a "
            "generic/unflavoured snack reference — confirm before enriching "
            "(not silently auto-enriched)",
        )
    return None


#: Quality-V2-Q rules, evaluated in order; first hit wins.
_TARGETED_RULES = (
    _drink_form_mismatch,
    _vinegar_type_mismatch,
    _jam_fruit_mismatch,
    _oil_mismatch,
    _potato_puree_mismatch,
)


def nutrition_safety_action(
    *,
    matched: bool,
    review_required: bool,
    confidence: float,
    protein: float | None,
    product_name: str,
    ref_name: str,
) -> tuple[str, str]:
    """Returns ``(action, reason)``. ``would_enrich`` only when the matcher
    accepts AND the physical states are aligned enough for nutrition."""
    base = base_safety_action(
        matched=matched, review_required=review_required, protein=protein,
        confidence=confidence,
    )
    if base != "would_enrich":
        return base, _BASE_REASONS[base]

    p_tokens = set(_norm(product_name).split())
    r_tokens = set(_norm(ref_name).split())
    concept = concept_of(product_name)

    # Proxy-too-broad applies to any concept (syrup/concentrate/essence…).
    if r_tokens & _PROXY_TOO_BROAD:
        bad = sorted(r_tokens & _PROXY_TOO_BROAD)
        return (
            "skip_proxy_too_broad",
            f"reference is a processing proxy ({', '.join(bad)}), not a "
            "whole-food nutrition source",
        )

    # Targeted variety/form/state filters (rice drink, vinegar/oil/jam type,
    # instant-vs-prepared puree).
    for rule in _TARGETED_RULES:
        hit = rule(concept, p_tokens, r_tokens)
        if hit is not None:
            return hit

    if concept in ("coffee", "tea"):
        beverage = _beverage_mismatch(p_tokens, r_tokens)
        if beverage is not None:
            return beverage
    if concept in _STATE_SENSITIVE_CONCEPTS:
        state = _state_mismatch(p_tokens, r_tokens)
        if state is not None:
            return state

    # Generic snack proxy is the last gate — flag rather than silently enrich.
    snack = _snack_generic_proxy(concept, p_tokens, r_tokens)
    if snack is not None:
        return snack

    return "would_enrich", "product and reference physical states aligned"

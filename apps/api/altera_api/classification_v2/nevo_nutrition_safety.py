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

    # Proxy-too-broad applies to any concept (syrup/concentrate/essence…).
    if r_tokens & _PROXY_TOO_BROAD:
        bad = sorted(r_tokens & _PROXY_TOO_BROAD)
        return (
            "skip_proxy_too_broad",
            f"reference is a processing proxy ({', '.join(bad)}), not a "
            "whole-food nutrition source",
        )

    concept = concept_of(product_name)
    if concept in ("coffee", "tea"):
        beverage = _beverage_mismatch(p_tokens, r_tokens)
        if beverage is not None:
            return beverage
    if concept in _STATE_SENSITIVE_CONCEPTS:
        state = _state_mismatch(p_tokens, r_tokens)
        if state is not None:
            return state

    return "would_enrich", "product and reference physical states aligned"

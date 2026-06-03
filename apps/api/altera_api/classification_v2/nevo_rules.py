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

# Secondary-ingredient / qualifier phrases that must NOT drive a
# high-confidence match for a simple product head.
_SECONDARY_QUALIFIERS = (
    "huile", "olive", "ail", "persil", "with", "without", "sans", "avec",
    "sauce", "mashed", "puree", "boiled", "fried",
)
# STRUCTURAL qualifiers describe a composite/prepared candidate
# ("X with Y", "X without Y", "mashed", "sauce"). A candidate carrying
# one is never a clean head match for a simple product — reject it even
# when the product name itself contains an ingredient word.
_STRUCTURAL_QUALIFIERS = (
    "with", "without", "avec", "sans", "mashed", "mash", "puree", "sauce",
    "fried", "boiled",
)
# Concepts that are fundamentally secondary ingredients — a candidate
# whose head IS one of these can never be the primary match for a
# product of a different concept (even if embeddings rank it highly).
_QUALIFIER_CONCEPTS = frozenset({"oil", "olive", "garlic"})

# Canonical concept → surface forms (FR + EN). Multi-word phrases are
# matched with phrase preference so "beurre de cacahuete" resolves to
# ``peanut_butter`` rather than ``butter``.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "peanut_butter": ("beurre de cacahuete", "beurre de cacahuetes", "peanut butter"),
    "black_bean": ("haricot noir", "haricots noirs", "black bean", "black beans"),
    "chickpea": ("pois chiche", "pois chiches", "chickpea", "chickpeas"),
    "lentil": ("lentille", "lentilles", "lentil", "lentils"),
    "bean": ("haricot", "haricots", "bean", "beans"),
    "tofu": ("tofu",),
    "milk": ("lait", "milk"),
    "yoghurt": ("yaourt", "yogurt", "yoghurt"),
    "cheese": ("fromage", "cheese"),
    "butter": ("beurre", "butter"),
    "oil": ("huile", "oil"),
    "olive": ("olive", "olives"),
    "garlic": ("ail", "garlic"),
    "ratatouille": ("ratatouille",),
    "lasagne": ("lasagne", "lasagnes", "lasagna", "lasagnas"),
    "pasta": ("pates", "pasta", "spaghetti", "macaroni"),
    "muesli": ("muesli", "granola"),
    "potato": ("pomme de terre", "pommes de terre", "potato", "potatoes",
               "patate", "patates"),
    "apple": ("pomme", "pommes", "apple", "apples"),
    "tomato": ("tomate", "tomates", "tomato", "tomatoes"),
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


def _has_qualifier(text: str) -> bool:
    norm = _norm(text)
    return any(f" {q} " in norm for q in _SECONDARY_QUALIFIERS)


def concept_of(text: str) -> str | None:
    """The canonical concept of a text's HEAD.

    Scans every concept surface form; returns the concept whose form
    appears earliest in the text (phrase preference on ties), i.e. the
    concept of the leading/primary food — not a trailing secondary
    ingredient. Returns ``None`` when nothing is recognised.
    """
    norm = _norm(text)
    best: tuple[int, int, str] | None = None  # (position, -length, concept)
    for concept, forms in _CONCEPTS.items():
        for form in forms:
            idx = norm.find(f" {form} ")
            if idx == -1:
                continue
            key = (idx, -len(form), concept)
            if best is None or key < best:
                best = key
    return best[2] if best else None


def decide_candidate(product_name: str, candidate: NevoCandidate) -> NevoGateResult:
    """Precision-first decision for one (product, candidate) pair."""
    p_head = _primary_head(product_name)
    if p_head is None:
        return NevoGateResult(False, 0.0, "No usable product head.", "abstain")

    norm_cand = _norm(candidate.food_name_en)
    prod_concept = concept_of(product_name)
    cand_concept = concept_of(candidate.food_name_en)
    cand_tokens = set(_significant_tokens(candidate.food_name_en))
    cand_structural = any(f" {q} " in norm_cand for q in _STRUCTURAL_QUALIFIERS)
    cand_has_qual = _has_qualifier(candidate.food_name_en)
    prod_has_qual = _has_qualifier(product_name)

    # 1. Hard rejection: the candidate is a composite/prepared
    #    description ("hummus with chickpeas", "coffee with milk", "apple
    #    pie without butter", "potatoes mashed with milk", "salad with
    #    oil") — never a clean head match for a simple product, even when
    #    the qualifier ingredient equals the product.
    if cand_structural:
        return NevoGateResult(
            False, 0.0,
            "Candidate is a composite/prepared description (with/without/"
            "mashed/sauce), not a simple food head.",
            "rejected",
        )
    # 1b. Candidate is a secondary ingredient / qualifier the simple
    #     product head lacks (e.g. 'Oil olive' for a non-oil product).
    if cand_has_qual and not prod_has_qual and prod_concept != cand_concept:
        return NevoGateResult(
            False, 0.0,
            "Candidate matches a secondary ingredient, not the product head.",
            "rejected",
        )
    # 1c. The candidate IS fundamentally an oil/garlic/olive (a typical
    #     secondary ingredient) but the product is a different concept.
    #     Hard-reject regardless of qualifiers — this holds even when the
    #     product name itself contains 'huile'/'olive' (e.g. "Ratatouille
    #     à l'huile d'olive" must not match "Oil olive"). Embeddings can
    #     surface such a candidate; the rule must still kill it.
    if (
        cand_concept in _QUALIFIER_CONCEPTS
        and prod_concept is not None
        and prod_concept != cand_concept
    ):
        return NevoGateResult(
            False, 0.0,
            f"Candidate is a {cand_concept!r} (a secondary ingredient), not the "
            f"product head {prod_concept!r}.",
            "rejected",
        )

    # 2. Concept (alias) match across FR/EN — but only when the CANDIDATE
    #    head is that concept (blocks 'milk' matching 'potatoes … milk').
    if prod_concept is not None and prod_concept == cand_concept:
        return NevoGateResult(
            True, 0.96,
            f"Concept match: {prod_concept!r}.",
            "alias",
        )

    # 3. Exact literal head match.
    if p_head in cand_tokens:
        # …unless the candidate is dominated by a different leading
        # concept (e.g. product 'lait' vs candidate 'potatoes … milk').
        if cand_concept is not None and prod_concept is not None and cand_concept != prod_concept:
            return NevoGateResult(
                False, 0.0,
                f"Product head {p_head!r} is a secondary ingredient of a "
                f"{cand_concept!r} candidate.",
                "rejected",
            )
        return NevoGateResult(True, 0.95, f"Exact head match: {p_head!r}.", "exact")

    # 4. Otherwise: not safe for high confidence → abstain (a softer
    #    'proxy' could be surfaced for review, but never auto-accepted).
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
    prod_has = _has_qualifier(product_name)
    cand_has = _has_qualifier(candidate.food_name_en)
    if cand_has and not prod_has:
        return NevoGateResult(
            False, 0.0, "Candidate adds a qualifier the simple product head lacks.",
            "rejected",
        )
    return NevoGateResult(True, 0.9, "No with/without qualifier trap.", "exact")

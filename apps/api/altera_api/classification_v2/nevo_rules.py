"""Phase Quality-V2-A — minimal NEVO matching V2 rules (skeleton).

Precision-first candidate gating. These rules don't perform the actual
NEVO lookup (that's V1's job today + the embeddings retriever later);
they encode the *guardrails* that keep a match high-confidence:

1. ``head_match_required`` — high confidence needs the candidate's
   food head to match the product head (not a secondary ingredient).
2. ``reject_secondary_ingredient`` — a candidate that only matches a
   minor/secondary ingredient ("à l'huile d'olive", "ail & persil")
   is rejected for a simple-head product.
3. ``reject_with_without_trap`` — "with X" / "without X" / "à l'huile"
   variants are rejected for a simple food head that has no such
   qualifier.

Philosophy: abstaining is better than a confident wrong match.
"""

from __future__ import annotations

from dataclasses import dataclass

from altera_api.classification_v2.pt_rules import _norm

# Secondary-ingredient / qualifier phrases that should NOT drive a
# high-confidence match for a simple food head.
_SECONDARY_QUALIFIERS = (
    "huile",
    "olive",
    "ail",
    "persil",
    "with",
    "without",
    "sans",
    "avec",
    "sauce",
)


@dataclass(frozen=True)
class NevoCandidate:
    nevo_code: str
    food_name_en: str


@dataclass(frozen=True)
class NevoGateResult:
    accepted: bool
    confidence: float
    reason: str


def _significant_tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) >= 3]


def _primary_head(text: str) -> str | None:
    """The product's PRIMARY head token — the first significant word.

    For "Ratatouille à l'huile d'olive" the primary head is
    ``ratatouille``; ``huile`` / ``olive`` are qualifiers that must NOT
    drive a match. Matching on the primary head (not any of the first
    few tokens) is what blocks the "Oil olive" trap.
    """
    toks = _significant_tokens(text)
    return toks[0] if toks else None


def head_match_required(
    product_name: str, candidate: NevoCandidate
) -> NevoGateResult:
    """High confidence only when the product's PRIMARY head token
    appears among the candidate's tokens. Precision-first: a candidate
    that only matches a qualifier/secondary token is rejected."""
    p_head = _primary_head(product_name)
    if p_head is None:
        return NevoGateResult(False, 0.0, "No usable product head — abstain.")
    c_tokens = set(_significant_tokens(candidate.food_name_en))
    if p_head in c_tokens:
        return NevoGateResult(True, 0.95, f"Primary head match: {p_head!r}")
    return NevoGateResult(
        False,
        0.0,
        f"Primary product head {p_head!r} not in candidate — reject.",
    )


def reject_secondary_ingredient(
    product_name: str, candidate: NevoCandidate
) -> NevoGateResult:
    """Reject a candidate whose name is a secondary ingredient of the
    product (e.g. product 'Ratatouille à l'huile d'olive' must NOT match
    NEVO 'Oil olive')."""
    cand = _norm(candidate.food_name_en)
    if any(f" {q} " in cand for q in _SECONDARY_QUALIFIERS):
        # Candidate is itself a qualifier/secondary food; only accept if
        # the product's PRIMARY head literally is that food.
        p_head = _primary_head(product_name)
        c_tokens = set(_significant_tokens(candidate.food_name_en))
        if p_head is None or p_head not in c_tokens:
            return NevoGateResult(
                False,
                0.0,
                "Candidate is a secondary ingredient, not the product head.",
            )
    return NevoGateResult(True, 0.9, "Not a secondary-ingredient trap.")


def reject_with_without_trap(
    product_name: str, candidate: NevoCandidate
) -> NevoGateResult:
    """For a simple product head with no qualifier, reject candidates
    that add a 'with/without/à l'huile' qualifier."""
    prod = _norm(product_name)
    cand = _norm(candidate.food_name_en)
    prod_has_qualifier = any(f" {q} " in prod for q in _SECONDARY_QUALIFIERS)
    cand_has_qualifier = any(f" {q} " in cand for q in _SECONDARY_QUALIFIERS)
    if cand_has_qualifier and not prod_has_qualifier:
        return NevoGateResult(
            False,
            0.0,
            "Candidate adds a qualifier the simple product head lacks.",
        )
    return NevoGateResult(True, 0.9, "No with/without qualifier trap.")


# All gates must pass for a high-confidence accept; the lowest
# confidence among passing gates is the resulting score.
NEVO_GATES = [
    head_match_required,
    reject_secondary_ingredient,
    reject_with_without_trap,
]


def gate_candidate(
    product_name: str, candidate: NevoCandidate
) -> NevoGateResult:
    """Run all gates; reject on the first failure, else return the
    minimum-confidence passing result."""
    min_conf = 1.0
    reasons: list[str] = []
    for gate in NEVO_GATES:
        r = gate(product_name, candidate)
        if not r.accepted:
            return r
        min_conf = min(min_conf, r.confidence)
        reasons.append(r.reason)
    return NevoGateResult(True, min_conf, "; ".join(reasons))

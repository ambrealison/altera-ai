"""Phase Quality-V2-R — internal review workflow for NEVO V2 dry-run proposals.

The V2 dry-run never writes to the database. To make the proposals usable for a
human reviewer (and to plan a future, gated apply path), this module annotates
each proposal with:

* ``suggested_action`` — what a reviewer most likely needs to do.
* ``review_priority``  — how urgent / risky the row is (P0..P3).
* ``review_bucket``    — which review tab/CSV the row belongs to.

It is pure (no I/O, no DB, no routes) and is imported only by the
``nevo_v2_enrich`` dry-run CLI.
"""

from __future__ import annotations

from typing import Any

from altera_api.classification_v2.nevo_rules import _norm, concept_of

# --- vocabularies ---------------------------------------------------------

#: Pet food / accessories — excluded by policy (never a human-food source).
_PET_MARKERS = frozenset({
    "chat", "chats", "chien", "chiens", "chiot", "chaton", "litiere",
    "croquette", "croquettes", "patee", "animal", "animaux", "aquarium",
    "collier", "laisse", "niche", "rongeur", "oiseau", "poisson",  # pet fish
})

#: Clearly non-food (household / hygiene / cosmetics / hardware).
_NON_FOOD_MARKERS = frozenset({
    "vaisselle", "lessive", "nettoyant", "shampooing", "shampoing", "savon",
    "dentifrice", "douche", "papier", "mouchoir", "mouchoirs", "essuie",
    "sac", "sacs", "pile", "piles", "ampoule", "bougie", "detergent",
    "adoucissant", "javel", "eponge", "eponges", "lingette", "lingettes",
    "deodorant", "maquillage", "couche", "couches", "coton", "rasoir",
    "insecticide", "desodorisant",
})

#: allowed manual_decision values (documented for the reviewer).
MANUAL_DECISION_VALUES = ("approve", "reject", "replace", "needs_more_info")

SUGGESTED_ACTIONS = (
    "approve_auto_candidate",
    "review_state_mismatch",
    "review_proxy_too_broad",
    "review_generic_proxy",
    "review_no_match",
    "reject_non_food",
    "reject_policy_excluded",
    "needs_manual_nevo_search",
)

REVIEW_PRIORITIES = ("P0", "P1", "P2", "P3")

REVIEW_BUCKETS = (
    "auto_ready", "needs_review", "state_mismatch", "proxy_too_broad",
    "no_match", "non_food_policy",
)

#: blank columns the reviewer fills in.
REVIEWER_BLANK_COLUMNS = (
    "manual_decision", "reviewer_notes", "approved_nevo_code",
    "approved_nevo_name", "approved_protein_g_per_100g",
)
#: computed columns (populated by this module).
REVIEW_COMPUTED_COLUMNS = ("review_priority", "suggested_action")

#: full set of extra columns appended to a proposal row for the review package,
#: in the order required by the brief (Part A).
REVIEW_EXTRA_COLUMNS = (
    "manual_decision", "reviewer_notes", "approved_nevo_code",
    "approved_nevo_name", "approved_protein_g_per_100g", "review_priority",
    "suggested_action",
)

_AUTO_HIGH_CONFIDENCE = 0.97

INSTRUCTIONS = (
    "NEVO V2 dry-run review package — internal use only. This is a DRY-RUN: no "
    "database writes were made and V2 is not active in production.",
    "Workflow: open this package, review each row, and record a decision in "
    "the manual_decision column.",
    f"manual_decision allowed values: {', '.join(MANUAL_DECISION_VALUES)}.",
    "  approve  — the suggested NEVO match is correct for nutrition enrichment.",
    "  reject   — do not enrich from this NEVO entry (wrong food / non-food).",
    "  replace  — enrich, but from a different NEVO entry; fill approved_* "
    "columns (approved_nevo_code, approved_nevo_name, approved_protein_g_per_100g).",
    "  needs_more_info — cannot decide yet; add reviewer_notes.",
    "review_priority: P0 high-risk / never auto-apply; P1 likely useful but "
    "needs human confirmation; P2 safe abstain / optional; P3 non-food / "
    "policy excluded.",
    "suggested_action is advisory only — the matcher and safety actions are "
    "unchanged; nothing here is auto-applied.",
)


def _tokens(text: str) -> set[str]:
    return set(_norm(text).split())


def classify_product_policy(product_name: str) -> str:
    """``food`` | ``pet`` | ``non_food``. A recognized food concept always wins;
    otherwise explicit pet/household markers classify it; unknown defaults to
    ``food`` (so we route to a manual search rather than wrongly rejecting)."""
    if concept_of(product_name) is not None:
        return "food"
    toks = _tokens(product_name)
    if toks & _PET_MARKERS:
        return "pet"
    if toks & _NON_FOOD_MARKERS:
        return "non_food"
    return "food"


def _has_top5(row: dict[str, Any]) -> bool:
    return bool(str(row.get("top_5_candidates") or "").strip())


def suggested_action(row: dict[str, Any]) -> str:
    policy = classify_product_policy(row["product_name"])
    if policy == "pet":
        return "reject_policy_excluded"
    if policy == "non_food":
        return "reject_non_food"

    action = row["nutrition_safety_action"]
    reason = str(row.get("nutrition_safety_reason") or "")
    if action == "would_enrich":
        return "approve_auto_candidate"
    if action == "skip_state_mismatch":
        return "review_state_mismatch"
    if action == "skip_proxy_too_broad":
        return "review_proxy_too_broad"
    if action == "route_to_review":
        if (
            "generic snack proxy" in reason
            or "blend/margarine" in reason
            or "wrong oil type" in reason
        ):
            return "review_generic_proxy"
        return "needs_manual_nevo_search"
    if action == "skip_no_match":
        return "review_no_match" if _has_top5(row) else "needs_manual_nevo_search"
    # skip_no_nutrition_value (matched but reference has no protein value)
    return "needs_manual_nevo_search"


def review_priority(row: dict[str, Any], action: str | None = None) -> str:
    policy = classify_product_policy(row["product_name"])
    if policy in ("pet", "non_food"):
        # A non-food/pet item that the matcher nonetheless ACCEPTED is the most
        # dangerous case (food nutrition onto a non-food) → never auto.
        return "P0" if row["matcher_outcome"] != "no_match" else "P3"

    action = action or suggested_action(row)
    if action == "approve_auto_candidate":
        try:
            conf = float(row.get("matcher_confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        return "P2" if conf >= _AUTO_HIGH_CONFIDENCE else "P1"
    if action in ("review_no_match", "needs_manual_nevo_search"):
        return "P1" if _has_top5(row) else "P2"
    # review_state_mismatch / review_proxy_too_broad / review_generic_proxy
    return "P1"


def review_bucket(row: dict[str, Any], action: str | None = None) -> str:
    action = action or suggested_action(row)
    if action in ("reject_non_food", "reject_policy_excluded"):
        return "non_food_policy"
    if action == "approve_auto_candidate":
        return "auto_ready"
    if action == "review_state_mismatch":
        return "state_mismatch"
    if action == "review_proxy_too_broad":
        return "proxy_too_broad"
    if action == "review_generic_proxy":
        return "needs_review"
    if action == "review_no_match":
        return "no_match"
    # needs_manual_nevo_search
    return "no_match" if row["matcher_outcome"] == "no_match" else "needs_review"


def annotate(row: dict[str, Any]) -> dict[str, str]:
    """Return ``{suggested_action, review_priority, review_bucket}`` for a row."""
    action = suggested_action(row)
    return {
        "suggested_action": action,
        "review_priority": review_priority(row, action),
        "review_bucket": review_bucket(row, action),
    }

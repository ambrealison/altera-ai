"""Phase Quality-V2-AI — conservative multilingual retrieval decision layer.

The raw multilingual benchmark (compare_nevo_multilingual_retrieval) showed the
FR/DE reference *globally* degrades retrieval: it replaces good baseline matches
and drifts into broad/wrong food families (corn->cocoa, sugar->syrup,
almond-drink->soya, hummus->citrus, mustard->roux, jam->fruit-in-syrup,
peas-frozen->tinned). So we must NOT adopt the multilingual reference globally.

This module is a read-only decision layer that operates on the row-level
comparison objects: the conservative output candidate is the BASELINE candidate
by default, and only switches to the multilingual candidate when it *clearly*
fixes a baseline failure/safety-downgrade and passes every safety guard. It never
replaces a baseline ``auto_ready`` (unless an explicit unsafe flag is set), never
switches into ``no_match``/zero-confidence, and never crosses a known food-family
boundary. It does not weaken validation or nutrition safety — it only chooses
between two already-gated matcher outputs. No DB writes; no routes.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import _s

#: Bucket ordering (lower rank = closer to safe auto-enrich).
BUCKETS = ("auto_ready", "safety_downgrade", "needs_review", "no_match",
           "true_high_risk")
RANK = {b: i for i, b in enumerate(BUCKETS)}
#: A switch is only ever considered when the baseline is one of these.
ELIGIBLE_BASELINE_BUCKETS = frozenset(
    {"no_match", "needs_review", "safety_downgrade"})
DEFAULT_MIN_CONFIDENCE = 0.90
#: How many safe improvements we need before calling the lift "material".
MATERIAL_IMPROVEMENT_MIN = 3

#: Row-level columns the conservative layer appends to the comparison CSV.
CONSERVATIVE_EXTRA_COLUMNS = [
    "conservative_bucket", "conservative_nevo_code", "conservative_top1",
    "conservative_confidence", "conservative_decision", "conservative_reason",
    "conservative_matches_existing_v2",
]


def _norm(text: Any) -> str:
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def family_mismatch(product_name: str, baseline_top1: str, ml_top1: str,
                    ) -> str | None:
    """Return a reason string when switching to ``ml_top1`` would cross a known
    food-family boundary (so the switch must be blocked), else None.

    Each rule encodes "do not let the multilingual candidate broaden/drift the
    food family unless the product text explicitly licenses it".
    """
    p = _norm(product_name)
    b = _norm(baseline_top1)
    m = _norm(ml_top1)

    # corn product -> cocoa
    if (_has(p, "corn", "mais") or _has(b, "corn", "mais")) and \
            _has(m, "cocoa", "cacao"):
        return "corn_to_cocoa"
    # sugar -> syrup, unless product explicitly contains syrup/sirop
    if (_has(p, "sugar", "sucre") or _has(b, "sugar")) and \
            _has(m, "syrup", "sirop") and not _has(p, "syrup", "sirop"):
        return "sugar_to_syrup"
    # almond drink -> soya drink
    if _has(p, "almond", "amande") and \
            _has(p, "drink", "boisson", "milk", "lait") and \
            _has(m, "soya", "soja", "soy"):
        return "almond_drink_to_soya"
    # hummus -> citrus/grapefruit/lemon-only
    if _has(p, "hummus", "houmous") and \
            _has(m, "lemon", "citron", "grapefruit", "pamplemousse", "citrus",
                 "lime") and \
            not _has(m, "chickpea", "pois chiche", "hummus", "houmous"):
        return "hummus_to_citrus"
    # mustard -> roux sauce, unless product explicitly contains sauce/roux
    if _has(p, "mustard", "moutarde") and _has(m, "roux", "sauce") and \
            not _has(p, "sauce", "roux"):
        return "mustard_to_roux_sauce"
    # jam/confiture fruit -> fruit in syrup, unless product says syrup/sirop
    if _has(p, "jam", "confiture", "marmalade", "marmelade") and \
            _has(m, "syrup", "sirop") and not _has(p, "syrup", "sirop"):
        return "jam_to_fruit_in_syrup"
    # peas frozen/surgele -> tinned/canned, unless product says canned/tinned
    if _has(p, "peas", "pois") and _has(p, "frozen", "surgele") and \
            _has(m, "tinned", "canned", "conserve") and \
            not _has(p, "canned", "tinned", "conserve"):
        return "peas_frozen_to_tinned"
    return None


def decide_row(row: dict[str, Any], *, allow_overwrite_auto_ready: bool,
               min_confidence: float) -> dict[str, Any]:
    """Return *row* enriched with conservative_* fields.

    Default conservative candidate = baseline. Switch to multilingual only when
    it strictly improves an eligible baseline bucket AND passes every guard.
    """
    base_bucket = _s(row.get("baseline_bucket"))
    ml_bucket = _s(row.get("multilingual_bucket"))
    base_rank = RANK.get(base_bucket, 99)
    ml_rank = RANK.get(ml_bucket, 99)
    ml_conf = _f(row.get("multilingual_confidence"))

    switch = False
    if base_bucket == "auto_ready":
        # Never replace a good baseline match unless explicitly allowed.
        if allow_overwrite_auto_ready and ml_rank < base_rank:
            switch, reason = True, "switch_overwrite_auto_ready_explicit"
        else:
            reason = "kept_baseline_auto_ready_protected"
    elif base_bucket not in ELIGIBLE_BASELINE_BUCKETS:
        reason = "kept_baseline_not_eligible"
    elif ml_bucket == "no_match":
        reason = "blocked_multilingual_no_match"
    elif ml_conf <= 0.0:
        reason = "blocked_multilingual_zero_confidence"
    elif ml_rank >= base_rank:
        reason = "kept_baseline_not_strictly_better"
    elif ml_conf < min_confidence:
        reason = "blocked_below_confidence_threshold"
    elif _s(row.get("baseline_matches_existing_v2")) == "true":
        reason = "blocked_conflicts_existing_v2_agreement"
    elif (fam := family_mismatch(
            _s(row.get("product_name")), _s(row.get("baseline_top1")),
            _s(row.get("multilingual_top1")))):
        reason = f"blocked_family_mismatch:{fam}"
    else:
        switch, reason = True, "switch_clear_improvement"

    if switch:
        chosen_bucket = ml_bucket
        chosen = {
            "conservative_bucket": ml_bucket,
            "conservative_nevo_code": _s(row.get("multilingual_nevo_code")),
            "conservative_top1": _s(row.get("multilingual_top1")),
            "conservative_confidence": _s(row.get("multilingual_confidence")),
            "conservative_matches_existing_v2": _s(
                row.get("multilingual_matches_existing_v2")),
            "conservative_decision": "switch_multilingual",
        }
    else:
        chosen_bucket = base_bucket
        chosen = {
            "conservative_bucket": base_bucket,
            "conservative_nevo_code": _s(row.get("baseline_nevo_code")),
            "conservative_top1": _s(row.get("baseline_top1")),
            "conservative_confidence": _s(row.get("baseline_confidence")),
            "conservative_matches_existing_v2": _s(
                row.get("baseline_matches_existing_v2")),
            "conservative_decision": "keep_baseline",
        }
    chosen["conservative_reason"] = reason
    chosen["_conservative_rank"] = RANK.get(chosen_bucket, 99)
    chosen["_baseline_rank"] = base_rank
    chosen["_ml_rank"] = ml_rank
    return {**row, **chosen}


def _agreement(rows: list[dict[str, Any]], field: str) -> int:
    return sum(1 for r in rows if _s(r.get(field)) == "true")


def _bucket_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return {b: sum(1 for r in rows if _s(r.get(field)) == b) for b in BUCKETS}


def conservative_decisions(comparison_rows: list[dict[str, Any]], *,
                           allow_overwrite_auto_ready: bool = False,
                           min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                           coverage: float | None = None) -> dict[str, Any]:
    """Apply the conservative layer to the raw comparison rows.

    Returns ``{"summary": {...}, "rows": [...]}`` where each row carries the
    ``conservative_*`` fields. The conservative output never regresses a row by
    construction (it only switches to a strictly-better, guarded candidate).
    """
    rows = [decide_row(r, allow_overwrite_auto_ready=allow_overwrite_auto_ready,
                       min_confidence=min_confidence) for r in comparison_rows]
    n = len(rows)

    switch_count = sum(1 for r in rows
                       if r["conservative_decision"].startswith("switch"))
    kept_baseline = n - switch_count
    improved = sum(1 for r in rows
                   if r["_conservative_rank"] < r["_baseline_rank"])
    regressed = sum(1 for r in rows
                    if r["_conservative_rank"] > r["_baseline_rank"])
    # A raw multilingual that WOULD have regressed but the layer prevented.
    blocked_regression = sum(1 for r in rows if r["_ml_rank"] > r["_baseline_rank"]
                             and r["conservative_decision"] == "keep_baseline")

    base_counts = _bucket_counts(rows, "baseline_bucket")
    raw_counts = _bucket_counts(rows, "multilingual_bucket")
    cons_counts = _bucket_counts(rows, "conservative_bucket")

    base_agree = _agreement(rows, "baseline_matches_existing_v2")
    raw_agree = _agreement(rows, "multilingual_matches_existing_v2")
    cons_agree = _agreement(rows, "conservative_matches_existing_v2")

    base_thr = base_counts["true_high_risk"]
    cons_thr = cons_counts["true_high_risk"]
    # Agreement may not degrade by more than a tiny tolerance.
    agreement_tolerance = max(1, math.ceil(0.02 * max(base_agree, 1)))
    agreement_ok = cons_agree >= base_agree - agreement_tolerance

    adopt_ok = (
        cons_thr <= base_thr
        and regressed == 0
        and cons_counts["auto_ready"] >= base_counts["auto_ready"]
        and cons_counts["no_match"] <= base_counts["no_match"]
        and agreement_ok
        and improved >= MATERIAL_IMPROVEMENT_MIN
    )

    if cons_thr > base_thr or regressed > 0:
        recommendation = "reject_due_to_regressions"
    elif adopt_ok:
        recommendation = "adopt_conservative_candidate"
    elif coverage is not None and coverage < 0.5:
        recommendation = "needs_more_coverage"
    else:
        recommendation = "neutral_no_lift"

    summary = {
        "phase": "quality-v2-ai",
        "decision_mode": "conservative",
        "allow_multilingual_overwrite_auto_ready": allow_overwrite_auto_ready,
        "min_confidence": min_confidence,
        "products_compared": n,
        "baseline_counts": base_counts,
        "raw_multilingual_counts": raw_counts,
        "conservative_counts": cons_counts,
        "conservative_switch_count": switch_count,
        "conservative_kept_baseline_count": kept_baseline,
        "conservative_blocked_regression_count": blocked_regression,
        "conservative_improved_count": improved,
        "conservative_regressed_count": regressed,
        "true_high_risk_delta": cons_thr - base_thr,
        "agreement_with_existing_v2": {
            "baseline": base_agree, "raw": raw_agree,
            "conservative": cons_agree,
        },
        "multilingual_coverage": coverage,
        "recommendation": recommendation,
    }
    # Drop the private rank helpers from the emitted rows.
    for r in rows:
        for k in ("_conservative_rank", "_baseline_rank", "_ml_rank"):
            r.pop(k, None)
    return {"summary": summary, "rows": rows}

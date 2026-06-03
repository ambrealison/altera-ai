"""Phase Quality-V2-F — NEVO benchmark failure diagnostics.

Turns the per-case decisions of a benchmark run into focused failure
reports so an operator can identify the exact problem cases without
grepping the full candidate CSV:

  * ``nevo_failures_<model>.csv``                       — everything that
    is not a clean correct auto-accept.
  * ``nevo_high_conf_false_positives_<model>.csv``      — the dangerous
    cases: a high-confidence accept of the wrong food (gate-blocking).
  * ``nevo_expected_missing_topk_<model>.csv``          — the expected food
    is in the NEVO reference but retrieval missed it (recall problem).
  * ``nevo_fixture_expected_not_in_reference_<model>.csv`` — the fixture
    expects a food absent from the NEVO reference (fixture/coverage gap).
  * ``nevo_abstains_<model>.csv``                        — should-match
    cases the matcher (safely) sent to review/abstain.

Plus concise console sections for the three highest-signal buckets.
Everything is derived from decisions already computed by the evaluator —
no extra embedding/network cost.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from altera_api.classification_v2.nevo_eval_embeddings import _same_food
from altera_api.classification_v2.nevo_rules import _significant_tokens, concept_of

FAILURE_CSV_COLUMNS = [
    "fixture_id", "product_name", "expected_match", "expected_nevo_code",
    "expected_exists_in_reference", "final_decision", "accepted_candidate_name",
    "accepted_candidate_code", "accepted_candidate_rank", "confidence",
    "match_type", "review_required", "taxonomy_bucket", "top_1_candidate_name",
    "top_1_similarity", "top_5_candidate_names", "rejection_reasons_summary",
    "notes",
]


def _reference_membership(references: list[dict[str, Any]]):
    names = {str(r.get("food_name_en", "")).strip().lower() for r in references}
    codes = {str(r.get("nevo_code", "")) for r in references if r.get("nevo_code")}
    concepts = {concept_of(str(r.get("food_name_en", ""))) for r in references}
    concepts.discard(None)
    return names, codes, concepts


def _expected_exists(
    expected: dict[str, Any], names: set, codes: set, concepts: set
) -> bool:
    exp_name = str(expected.get("food_name_en", "")).strip().lower()
    exp_code = str(expected.get("nevo_code", ""))
    if exp_code and exp_code in codes:
        return True
    if exp_name and exp_name in names:
        return True
    exp_concept = concept_of(str(expected.get("food_name_en", "")))
    return exp_concept is not None and exp_concept in concepts


def build_diagnosis_rows(
    records: list[tuple[dict[str, Any], Any]],
    references: list[dict[str, Any]],
    *,
    auto_accept_threshold: float = 0.90,
) -> list[dict[str, Any]]:
    """One diagnosis row per fixture case, classified into a bucket."""
    names, codes, concepts = _reference_membership(references)
    rows: list[dict[str, Any]] = []
    for case, decision in records:
        expected = case.get("expected_match") or {}
        should_match = bool(case.get("should_match", bool(expected)))
        top = list(getattr(decision, "top_candidates", []))
        top1 = top[0] if top else None
        top5 = top[:5]

        # The candidate the decision actually chose (by name), if any.
        accepted_name = decision.food_name_en if decision.matched else ""
        accepted_code = decision.nevo_code if decision.matched else ""
        accepted_rank = ""
        for tr in top:
            if decision.matched and tr.candidate_name == decision.food_name_en:
                accepted_rank = tr.rank
                break

        exp_exists = _expected_exists(expected, names, codes, concepts) if expected else False
        high_conf = decision.matched and not decision.review_required and (
            decision.confidence >= auto_accept_threshold
        )
        correct = (
            _same_food(expected, decision.nevo_code, decision.food_name_en)
            if expected else False
        )

        # Where did the expected food land among candidates?
        expected_rank = None
        for tr in top:
            if _same_food(expected, tr.nevo_code, tr.candidate_name):
                expected_rank = tr.rank
                break

        bucket = _classify(
            should_match=should_match, exp_exists=exp_exists, high_conf=high_conf,
            correct=correct, matched=decision.matched,
            review=decision.review_required, expected_rank=expected_rank,
            has_expected=bool(expected),
        )

        rejections = sorted(
            {tr.rejection_reason for tr in top5 if tr.rejection_reason}
        )
        rows.append(
            {
                "fixture_id": str(case.get("id", "")),
                "product_name": case.get("product_name", ""),
                "expected_match": expected.get("food_name_en", ""),
                "expected_nevo_code": expected.get("nevo_code", ""),
                "expected_exists_in_reference": exp_exists,
                "final_decision": decision.match_type,
                "accepted_candidate_name": accepted_name,
                "accepted_candidate_code": accepted_code,
                "accepted_candidate_rank": accepted_rank,
                "confidence": round(decision.confidence, 4),
                "match_type": decision.match_type,
                "review_required": decision.review_required,
                "taxonomy_bucket": bucket,
                "top_1_candidate_name": top1.candidate_name if top1 else "",
                "top_1_similarity": top1.similarity if top1 else "",
                "top_5_candidate_names": " | ".join(t.candidate_name for t in top5),
                "rejection_reasons_summary": " ;; ".join(rejections),
                "notes": case.get("notes", ""),
            }
        )
    return rows


def _classify(
    *, should_match: bool, exp_exists: bool, high_conf: bool, correct: bool,
    matched: bool, review: bool, expected_rank: int | None, has_expected: bool,
) -> str:
    if not should_match:
        if high_conf:
            return "high_conf_false_positive_should_abstain"
        return "correct_abstain"
    if not has_expected:
        return "no_safe_reference"
    if high_conf and not correct:
        return "high_conf_false_positive"
    if not exp_exists:
        return "fixture_expected_not_in_reference"
    if high_conf and correct:
        if expected_rank == 1:
            return "matched_rank_1"
        if expected_rank is not None and expected_rank <= 5:
            return "matched_rank_2_5"
        return "matched_rank_6_20"
    if expected_rank is None:
        return "expected_missing_from_topk"
    if matched and review:
        return "review"
    if matched and not high_conf:
        return "review"
    return "expected_retrieved_but_rejected"


_FAILURE_BUCKETS = {
    "high_conf_false_positive", "high_conf_false_positive_should_abstain",
    "fixture_expected_not_in_reference", "expected_missing_from_topk",
    "expected_retrieved_but_rejected", "review", "no_safe_reference",
}
_HC_FP_BUCKETS = {
    "high_conf_false_positive", "high_conf_false_positive_should_abstain",
}


def write_failure_reports(
    out_dir: str | Path, model: str, rows: list[dict[str, Any]]
) -> dict[str, int]:
    """Write the five focused failure CSVs. Returns row counts per file."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = model.replace(".", "_")
    subsets = {
        f"nevo_failures_{slug}.csv": [
            r for r in rows if r["taxonomy_bucket"] in _FAILURE_BUCKETS
        ],
        f"nevo_high_conf_false_positives_{slug}.csv": [
            r for r in rows if r["taxonomy_bucket"] in _HC_FP_BUCKETS
        ],
        f"nevo_expected_missing_topk_{slug}.csv": [
            r for r in rows if r["taxonomy_bucket"] == "expected_missing_from_topk"
        ],
        f"nevo_fixture_expected_not_in_reference_{slug}.csv": [
            r for r in rows
            if r["taxonomy_bucket"] == "fixture_expected_not_in_reference"
        ],
        f"nevo_abstains_{slug}.csv": [
            r for r in rows
            if r["taxonomy_bucket"] in (
                "review", "expected_missing_from_topk",
                "expected_retrieved_but_rejected",
            )
        ],
    }
    counts: dict[str, int] = {}
    for fname, subset in subsets.items():
        with (out / fname).open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FAILURE_CSV_COLUMNS)
            w.writeheader()
            for r in subset:
                w.writerow(r)
        counts[fname] = len(subset)
    return counts


def print_console_diagnostics(rows: list[dict[str, Any]]) -> None:
    """Concise, high-signal console sections (no need to open the CSVs)."""
    hc_fp = [r for r in rows if r["taxonomy_bucket"] in _HC_FP_BUCKETS]
    missing = [r for r in rows if r["taxonomy_bucket"] == "expected_missing_from_topk"]
    not_in_ref = [
        r for r in rows if r["taxonomy_bucket"] == "fixture_expected_not_in_reference"
    ]

    print("\nHigh-confidence false positives:")
    if not hc_fp:
        print("  (none) ✓")
    for r in hc_fp:
        print(
            f"  {r['fixture_id']} | {r['product_name']} | exp {r['expected_match']!r}"
            f" | accepted {r['accepted_candidate_name']!r}"
            f" | conf {r['confidence']} | {r['rejection_reasons_summary'] or r['match_type']}"
        )

    print("\nExpected missing from top-k:")
    if not missing:
        print("  (none)")
    for r in missing:
        print(
            f"  {r['fixture_id']} | {r['product_name']} | exp {r['expected_match']!r}"
            f" | top1 {r['top_1_candidate_name']!r} | top5 {r['top_5_candidate_names']}"
        )

    print("\nExpected not in NEVO reference:")
    if not not_in_ref:
        print("  (none) ✓")
    for r in not_in_ref:
        print(
            f"  {r['fixture_id']} | {r['product_name']} | exp {r['expected_match']!r}"
            f" | closest: {r['top_5_candidate_names']}"
        )


# ===========================================================================
# Phase Quality-V2-G — rank-miss inspection (before any reranker).
#
# Two focused reports over should-match cases where the expected reference
# WAS retrieved in the top-k but is not rank-1 (rank-miss) or was rejected
# by the rules (retrieved-but-rejected). Each row carries the expected
# candidate, the accepted candidate, the top-5 context, and a heuristic
# ``diagnosis_bucket`` so we can tell harmless equivalents apart from real
# ranking problems — and decide whether a reranker is actually warranted.
# ===========================================================================
RANK_INSPECTION_CSV_COLUMNS = [
    "fixture_id", "product_name", "expected_name", "expected_code",
    "expected_rank", "expected_candidate_name", "expected_candidate_code",
    "expected_similarity", "expected_rejection_reason",
    "accepted_candidate_name", "accepted_candidate_code",
    "accepted_candidate_rank", "accepted_candidate_similarity",
    "accepted_match_type", "accepted_confidence",
    "accepted_same_concept_as_expected", "top_5_candidate_names",
    "top_5_candidate_codes", "top_5_similarities", "diagnosis_bucket",
    "match_relationship", "notes",
]

#: All diagnosis buckets a rank-miss / rejected case can fall into.
DIAGNOSIS_BUCKETS = (
    "harmless_equivalent",     # a safe same-concept food was accepted; coverage fine
    "expected_too_specific",   # accepted a broader same-concept food (fixture too specific)
    "rule_too_strict",         # rule rejected the only good candidate
    "true_ranking_issue",      # expected couldn't be located among candidates
    "fixture_should_change",   # expected resolves to a different food per rules
    "needs_reranker",          # a DIFFERENT-concept food was accepted above the right one
)

#: Finer notes on how the accepted food relates to the expected one.
MATCH_RELATIONSHIPS = (
    "exact_code_rank_miss",          # exact expected code, just below rank 1
    "same_concept_code_mismatch",    # same concept, different NEVO code/variant
    "accepted_more_specific_variant",  # accepted a narrower variant of the expected
    "fixture_expected_too_specific",   # accepted is broader; the fixture was too specific
    "different_concept_ranking_noise",  # accepted a different concept (real ranking issue)
    "expected_variant_rejected",     # the expected candidate itself was rejected
    "no_accept",                     # nothing was accepted
)


def _is_broader(accepted_name: str, expected_name: str) -> bool:
    a = set(_significant_tokens(accepted_name))
    b = set(_significant_tokens(expected_name))
    return bool(a) and a < b


def _diagnosis_bucket(
    *,
    exp_trace: Any,
    accepted_name: str,
    expected_name: str,
    accepted_same_concept: bool,
) -> str:
    """Heuristic, advisory classification (see ``DIAGNOSIS_BUCKETS``).

    Phase Quality-V2-H: a rank>1 miss where the system still accepted a
    correct, safe same-concept food is HARMLESS (coverage 100%, HC-FP 0) —
    it is NOT a reranker failure. ``needs_reranker`` is reserved for the
    case where a DIFFERENT-concept food was accepted above the right
    same-concept one (a reranker preferring concept agreement would fix the
    selection). Broadness is judged against the fixture's EXPECTED label."""
    if exp_trace is None:
        return "true_ranking_issue"

    if not exp_trace.accepted:
        # The expected candidate was rejected by the rules.
        if accepted_same_concept:
            # …but an equivalent same-concept food WAS accepted → the
            # rejection of this (usually composite) variant is correct.
            return "harmless_equivalent"
        reason = (exp_trace.rejection_reason or "").lower()
        if (
            "composite" in reason or "prepared dish" in reason
            or "secondary ingredient" in reason
        ):
            return "rule_too_strict"
        return "fixture_should_change"

    # The expected candidate was ACCEPTED, but at rank > 1.
    if not accepted_same_concept:
        # A different-concept food was accepted above the right same-concept
        # one → reordering by concept agreement would help.
        return "needs_reranker"
    # A correct same-concept food was accepted → harmless rank miss. If the
    # accepted food is BROADER than the fixture's expected label, the fixture
    # was simply too specific.
    if accepted_name and expected_name and _is_broader(accepted_name, expected_name):
        return "expected_too_specific"
    return "harmless_equivalent"


def _match_relationship(
    *,
    expected: dict[str, Any],
    exp_trace: Any,
    decision: Any,
    accepted_same_concept: bool,
) -> str:
    if not exp_trace.accepted:
        return "expected_variant_rejected"
    if not decision.matched:
        return "no_accept"
    if not accepted_same_concept:
        return "different_concept_ranking_noise"
    exp_code = str(expected.get("nevo_code", "") or "")
    acc_code = str(decision.nevo_code or "")
    if exp_code and acc_code and exp_code == acc_code:
        return "exact_code_rank_miss"
    # Compare the ACCEPTED food to the fixture's EXPECTED label.
    acc_name = str(decision.food_name_en or "")
    exp_name = str(expected.get("food_name_en", "") or "")
    if _is_broader(acc_name, exp_name):
        return "fixture_expected_too_specific"
    if _is_broader(exp_name, acc_name):
        return "accepted_more_specific_variant"
    return "same_concept_code_mismatch"


def _rank_inspection_row(
    case: dict[str, Any], decision: Any, exp_trace: Any, top: list[Any],
) -> dict[str, Any]:
    expected = case.get("expected_match") or {}
    accepted_trace = None
    if decision.matched:
        for tr in top:
            if tr.candidate_name == decision.food_name_en and tr.accepted:
                accepted_trace = tr
                break
    accepted_same_concept = (
        _same_food(expected, decision.nevo_code, decision.food_name_en)
        if decision.matched else False
    )
    top5 = top[:5]
    bucket = _diagnosis_bucket(
        exp_trace=exp_trace,
        accepted_name=str(decision.food_name_en or "") if decision.matched else "",
        expected_name=str(expected.get("food_name_en", "") or ""),
        accepted_same_concept=accepted_same_concept,
    )
    relationship = _match_relationship(
        expected=expected, exp_trace=exp_trace, decision=decision,
        accepted_same_concept=accepted_same_concept,
    )
    return {
        "fixture_id": str(case.get("id", "")),
        "product_name": case.get("product_name", ""),
        "expected_name": expected.get("food_name_en", ""),
        "expected_code": expected.get("nevo_code", ""),
        "expected_rank": exp_trace.rank,
        "expected_candidate_name": exp_trace.candidate_name,
        "expected_candidate_code": exp_trace.nevo_code,
        "expected_similarity": exp_trace.similarity,
        "expected_rejection_reason": (
            "" if exp_trace.accepted else exp_trace.rejection_reason
        ),
        "accepted_candidate_name": decision.food_name_en if decision.matched else "",
        "accepted_candidate_code": decision.nevo_code if decision.matched else "",
        "accepted_candidate_rank": accepted_trace.rank if accepted_trace else "",
        "accepted_candidate_similarity": (
            accepted_trace.similarity if accepted_trace else ""
        ),
        "accepted_match_type": decision.match_type,
        "accepted_confidence": round(decision.confidence, 4),
        "accepted_same_concept_as_expected": accepted_same_concept,
        "top_5_candidate_names": " | ".join(t.candidate_name for t in top5),
        "top_5_candidate_codes": " | ".join(str(t.nevo_code) for t in top5),
        "top_5_similarities": " | ".join(f"{t.similarity:.3f}" for t in top5),
        "diagnosis_bucket": bucket,
        "match_relationship": relationship,
        "notes": case.get("notes", ""),
    }


def inspect_rank_misses(
    records: list[tuple[dict[str, Any], Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(rank_miss_rows, retrieved_but_rejected_rows)``.

    Uses the SAME first-``_same_food`` expected-candidate selection as the
    taxonomy, so the row counts equal the taxonomy's
    ``expected_rank_2_5 + expected_rank_6_20`` and
    ``expected_retrieved_but_rejected``."""
    rank_miss: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for case, decision in records:
        expected = case.get("expected_match") or {}
        should_match = bool(case.get("should_match", bool(expected)))
        if not should_match or not expected:
            continue
        top = list(getattr(decision, "top_candidates", []))
        exp_trace = None
        for tr in top:
            if _same_food(expected, tr.nevo_code, tr.candidate_name):
                exp_trace = tr
                break
        if exp_trace is None:
            continue  # missing-from-top-k — covered by the other reports
        if not exp_trace.accepted:
            rejected.append(_rank_inspection_row(case, decision, exp_trace, top))
        elif exp_trace.rank > 1:
            rank_miss.append(_rank_inspection_row(case, decision, exp_trace, top))
    return rank_miss, rejected


def write_rank_inspection_reports(
    out_dir: str | Path, model: str,
    rank_miss_rows: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
) -> dict[str, int]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = model.replace(".", "_")
    files = {
        f"nevo_rank_misses_{slug}.csv": rank_miss_rows,
        f"nevo_expected_retrieved_but_rejected_{slug}.csv": rejected_rows,
    }
    counts: dict[str, int] = {}
    for fname, subset in files.items():
        with (out / fname).open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=RANK_INSPECTION_CSV_COLUMNS)
            w.writeheader()
            for r in subset:
                w.writerow(r)
        counts[fname] = len(subset)
    return counts


def print_rank_inspection(
    rank_miss_rows: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
) -> None:
    print("\nExpected retrieved but not rank-1 (rank misses):")
    if not rank_miss_rows:
        print("  (none)")
    for r in rank_miss_rows:
        print(
            f"  {r['fixture_id']} | {r['product_name']} | exp {r['expected_name']!r}"
            f" @ rank {r['expected_rank']} (sim {r['expected_similarity']}) | "
            f"accepted {r['accepted_candidate_name']!r} "
            f"(rank {r['accepted_candidate_rank']}, same_concept="
            f"{r['accepted_same_concept_as_expected']}) | {r['diagnosis_bucket']}"
            f" [{r['match_relationship']}]"
        )

    print("\nExpected retrieved but rejected by rules:")
    if not rejected_rows:
        print("  (none)")
    for r in rejected_rows:
        print(
            f"  {r['fixture_id']} | {r['product_name']} | exp {r['expected_name']!r}"
            f" @ rank {r['expected_rank']} | rejected: "
            f"{r['expected_rejection_reason']} | accepted "
            f"{r['accepted_candidate_name']!r} | {r['diagnosis_bucket']}"
        )

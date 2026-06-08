"""Phase Quality-V2-AI — plan FR/DE language-specific V2 apply candidates.

Reads the language-specific conservative benchmark artifacts (JSON summary + CSV)
and prepares a GUARDED apply plan: which conservative-switch candidates would be
applied as language-specific V2 enrichments, and why every other row is skipped.

Read-only / plan-only. There is NO DB write path in this phase: passing
``--confirm-apply-language-v2`` does not apply — it reports
``apply_not_implemented_for_language_specific_v2`` and exits non-zero. This
mirrors the safety posture of the existing V2 apply tooling (dry-run default,
explicit confirm, never overwrite manual/V1/existing-V2) while keeping the actual
write path deferred until it can be made provably safe.

    python -m altera_api.classification_v2.plan_nevo_language_specific_v2_apply \
        --project-id <uuid> --retailer-language fr \
        --language-benchmark-json .../nevo_language_specific_retrieval_fr_<p>.json \
        --language-benchmark-csv  .../nevo_language_specific_retrieval_fr_<p>.csv \
        --output-dir /tmp/altera-quality --min-confidence 0.90 \
        --require-recommendation adopt_language_specific_candidate \
        --include-only-switches --dry-run

No DB writes; no routes; no commercial fields; V1 default; embeddings opt-in.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import _s

#: Conservative decisions that represent a switch to the language candidate.
SWITCH_DECISIONS = frozenset({"switch_multilingual", "switch_language"})
CANDIDATE_ACTION = "candidate_apply_language_v2"
#: Future apply provenance (documented; not written in this phase).
APPLY_SOURCE = "nevo"
APPLY_MATCH_METHOD = "ai_assisted"

PLAN_CSV_COLUMNS = [
    "project_id", "retailer_language", "product_id", "product_name",
    "baseline_bucket", "language_bucket", "conservative_bucket",
    "baseline_nevo_code", "conservative_nevo_code",
    "baseline_top1", "conservative_top1",
    "baseline_confidence", "conservative_confidence",
    "conservative_decision", "conservative_reason",
    "baseline_matches_existing_v2", "conservative_matches_existing_v2",
    "plan_action", "skip_reason", "source_version", "source_metadata_json",
]
REVIEW_CSV_COLUMNS = [
    "product_name", "candidate_nevo_code", "candidate_nevo_name", "confidence",
    "baseline_bucket", "baseline_nevo_code", "baseline_nevo_name",
    "conservative_reason", "retailer_language", "product_id", "source_version",
]


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def source_version_for(language: str) -> str:
    return f"v2_language_specific_{language}"


def _global_skip(summary: dict[str, Any], *, language: str,
                 require_recommendation: str | None) -> str | None:
    """A whole-plan guard failure (forces every candidate to skip), or None."""
    if (require_recommendation
            and _s(summary.get("recommendation")) != require_recommendation):
        return "skip_recommendation_guard"
    if (_i(summary.get("conservative_regressed_count")) > 0
            or _f(summary.get("true_high_risk_delta")) > 0
            or _i(summary.get("conservative_true_high_risk")) > 0):
        return "skip_regression_guard"
    if _f(summary.get("language_reference_coverage")) < 0.50:
        return "skip_coverage_guard"
    if _s(summary.get("retailer_language")) != language:
        return "skip_language_mismatch"
    return None


def _row_skip(row: dict[str, Any], *, language: str, min_confidence: float
              ) -> str | None:
    """A per-row reason this row cannot be a candidate, or None."""
    row_lang = _s(row.get("retailer_language"))
    if row_lang and row_lang != language:
        return "skip_language_mismatch"
    if _s(row.get("conservative_decision")) not in SWITCH_DECISIONS:
        return "skip_not_switch"
    if _s(row.get("conservative_bucket")) != "auto_ready":
        return "skip_not_auto_ready"
    if _f(row.get("conservative_confidence")) < min_confidence:
        return "skip_low_confidence"
    if not (_s(row.get("conservative_nevo_code"))
            and _s(row.get("conservative_top1"))):
        return "skip_missing_nevo_code"
    if _s(row.get("baseline_nevo_code")) == _s(row.get("conservative_nevo_code")):
        return "skip_same_nevo_code"
    # Never switch away from a baseline that agrees with an existing V2 record.
    if (_s(row.get("baseline_matches_existing_v2")) == "true"
            and _s(row.get("conservative_matches_existing_v2")) != "true"):
        return "skip_existing_v2_conflict"
    return None


def _metadata(row: dict[str, Any], summary: dict[str, Any], *, language: str,
              json_path: str, csv_path: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "retrieval_mode": "language_specific_conservative",
        "retailer_language": language,
        "baseline_reference_source": _s(summary.get("reference_source"))
        or "nevo",
        "language_reference_coverage": _f(
            summary.get("language_reference_coverage")),
        "benchmark_recommendation": _s(summary.get("recommendation")),
        "conservative_decision": _s(row.get("conservative_decision")),
        "conservative_reason": _s(row.get("conservative_reason")),
        "baseline_nevo_code": _s(row.get("baseline_nevo_code")),
        "baseline_top1": _s(row.get("baseline_top1")),
        "language_nevo_code": _s(row.get("conservative_nevo_code")),
        "language_top1": _s(row.get("conservative_top1")),
        "confidence": _f(row.get("conservative_confidence")),
        "source_artifact": {"benchmark_json": json_path,
                            "benchmark_csv": csv_path},
    }
    provider = _s(summary.get("provider"))
    if provider:
        meta["provider"] = provider
    return meta


def build_plan(summary: dict[str, Any], rows: list[dict[str, Any]], *,
               project_id: str, language: str, min_confidence: float,
               require_recommendation: str | None,
               json_path: str, csv_path: str) -> dict[str, Any]:
    source_version = source_version_for(language)
    global_skip = _global_skip(summary, language=language,
                               require_recommendation=require_recommendation)

    plan_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    skip_counts: dict[str, int] = {}
    candidate_codes: list[str] = []

    for row in rows:
        reason = _row_skip(row, language=language, min_confidence=min_confidence)
        if reason is None and global_skip is not None:
            reason = global_skip
        is_candidate = reason is None
        action = CANDIDATE_ACTION if is_candidate else reason
        meta_json = ""
        if is_candidate:
            meta_json = json.dumps(
                _metadata(row, summary, language=language, json_path=json_path,
                          csv_path=csv_path), ensure_ascii=False)
            candidate_codes.append(_s(row.get("conservative_nevo_code")))
        else:
            skip_counts[reason] = skip_counts.get(reason, 0) + 1

        plan_rows.append({
            "project_id": project_id, "retailer_language": language,
            "product_id": _s(row.get("product_id")),
            "product_name": _s(row.get("product_name")),
            "baseline_bucket": _s(row.get("baseline_bucket")),
            "language_bucket": _s(row.get("language_bucket")),
            "conservative_bucket": _s(row.get("conservative_bucket")),
            "baseline_nevo_code": _s(row.get("baseline_nevo_code")),
            "conservative_nevo_code": _s(row.get("conservative_nevo_code")),
            "baseline_top1": _s(row.get("baseline_top1")),
            "conservative_top1": _s(row.get("conservative_top1")),
            "baseline_confidence": _s(row.get("baseline_confidence")),
            "conservative_confidence": _s(row.get("conservative_confidence")),
            "conservative_decision": _s(row.get("conservative_decision")),
            "conservative_reason": _s(row.get("conservative_reason")),
            "baseline_matches_existing_v2": _s(
                row.get("baseline_matches_existing_v2")),
            "conservative_matches_existing_v2": _s(
                row.get("conservative_matches_existing_v2")),
            "plan_action": action,
            "skip_reason": "" if is_candidate else reason,
            "source_version": source_version if is_candidate else "",
            "source_metadata_json": meta_json,
        })
        if is_candidate:
            review_rows.append({
                "product_name": _s(row.get("product_name")),
                "candidate_nevo_code": _s(row.get("conservative_nevo_code")),
                "candidate_nevo_name": _s(row.get("conservative_top1")),
                "confidence": _s(row.get("conservative_confidence")),
                "baseline_bucket": _s(row.get("baseline_bucket")),
                "baseline_nevo_code": _s(row.get("baseline_nevo_code")),
                "baseline_nevo_name": _s(row.get("baseline_top1")),
                "conservative_reason": _s(row.get("conservative_reason")),
                "retailer_language": language,
                "product_id": _s(row.get("product_id")),
                "source_version": source_version,
            })

    candidate_count = len(review_rows)
    plan_summary = {
        "phase": "quality-v2-ai",
        "project_id": project_id,
        "retailer_language": language,
        "benchmark_json_path": json_path,
        "benchmark_csv_path": csv_path,
        "recommendation": _s(summary.get("recommendation")),
        "language_reference_coverage": _f(
            summary.get("language_reference_coverage")),
        "baseline_counts": summary.get("baseline_counts"),
        "raw_language_counts": summary.get("raw_language_counts"),
        "conservative_counts": summary.get("conservative_counts"),
        "conservative_switch_count": _i(
            summary.get("conservative_switch_count")),
        "conservative_improved_count": _i(
            summary.get("conservative_improved_count")),
        "conservative_regressed_count": _i(
            summary.get("conservative_regressed_count")),
        "true_high_risk_delta": _f(summary.get("true_high_risk_delta")),
        "baseline_existing_v2_agreement": _i(
            summary.get("baseline_existing_v2_agreement")),
        "conservative_existing_v2_agreement": _i(
            summary.get("conservative_existing_v2_agreement")),
        "total_rows": len(rows),
        "candidate_count": candidate_count,
        "skipped_count": len(rows) - candidate_count,
        "skip_counts": skip_counts,
        "candidate_nevo_codes": candidate_codes,
        "global_skip": global_skip,
        "source_tagging": {
            "source": APPLY_SOURCE, "match_method": APPLY_MATCH_METHOD,
            "source_version": source_version,
        },
        "min_confidence": min_confidence,
        "require_recommendation": require_recommendation,
    }
    return {"summary": plan_summary, "plan_rows": plan_rows,
            "review_rows": review_rows}


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]
               ) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "plan_nevo_language_specific_v2_apply",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--retailer-language", choices=["fr", "de", "en"],
                    required=True)
    ap.add_argument("--language-benchmark-json", required=True)
    ap.add_argument("--language-benchmark-csv", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--min-confidence", type=float, default=0.90)
    ap.add_argument("--require-recommendation",
                    default="adopt_language_specific_candidate",
                    help="required benchmark recommendation; empty string "
                         "disables the guard.")
    ap.add_argument("--include-only-switches", action="store_true",
                    help="candidates are restricted to conservative switches "
                         "(always enforced; this is a safety affirmation).")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--confirm-apply-language-v2", action="store_true",
                    help="NOT IMPLEMENTED: there is no DB write path in this "
                         "phase; this reports apply_not_implemented and exits "
                         "non-zero.")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    json_path = Path(args.language_benchmark_json)
    csv_path = Path(args.language_benchmark_csv)
    if not json_path.exists():
        print(f"ERROR: benchmark JSON not found: {json_path}")
        return 2
    if not csv_path.exists():
        print(f"ERROR: benchmark CSV not found: {csv_path}")
        return 2

    summary = json.loads(json_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    language = args.retailer_language
    pid = _s(args.project_id)
    require = args.require_recommendation or None
    result = build_plan(summary, rows, project_id=pid, language=language,
                        min_confidence=args.min_confidence,
                        require_recommendation=require,
                        json_path=str(json_path), csv_path=str(csv_path))
    s = result["summary"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"nevo_language_specific_v2_apply_plan_{language}_{pid}"
    plan_csv = out_dir / f"{stem}.csv"
    review_csv = (out_dir
                  / f"nevo_language_specific_v2_apply_review_{language}_{pid}"
                    ".csv")
    _write_csv(plan_csv, PLAN_CSV_COLUMNS, result["plan_rows"])
    _write_csv(review_csv, REVIEW_CSV_COLUMNS, result["review_rows"])

    confirm = args.confirm_apply_language_v2
    apply_status = ("apply_not_implemented_for_language_specific_v2" if confirm
                    else "dry_run_plan_only")
    s.update({
        "output_paths": {
            "plan_json": str(out_dir / f"{stem}.json"),
            "plan_csv": str(plan_csv), "review_csv": str(review_csv),
        },
        "dry_run": True,
        "apply_supported": False,
        "apply_status": apply_status,
        "recommendation_for_next_step": (
            f"human-review the {s['candidate_count']} candidate(s); a guarded "
            "apply CLI (not implemented this phase) would tag them "
            f"source={APPLY_SOURCE}, match_method={APPLY_MATCH_METHOD}, "
            f"source_version={source_version_for(language)} — never overwriting "
            "manual/V1/existing-V2."
            if s["candidate_count"] else "no candidates selected; nothing to "
            "apply"),
    })
    plan_json = out_dir / f"{stem}.json"
    plan_json.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print("# NEVO language-specific V2 apply PLAN (read-only — no DB writes)")
    print(f"  project={pid} retailer_language={language} "
          f"recommendation={s['recommendation']} "
          f"coverage={s['language_reference_coverage']}")
    print(f"  total_rows={s['total_rows']} candidates={s['candidate_count']} "
          f"skipped={s['skipped_count']}")
    print(f"  skip_counts={s['skip_counts']}")
    print(f"  candidate_nevo_codes={s['candidate_nevo_codes']}")
    print(f"  source_version={source_version_for(language)} "
          f"apply_supported={s['apply_supported']} "
          f"apply_status={s['apply_status']}")
    print(f"  Plan JSON: {plan_json}")
    print(f"  Plan CSV:  {plan_csv}")
    print(f"  Review CSV: {review_csv}")

    if confirm:
        print("REFUSED: apply_not_implemented_for_language_specific_v2 — there "
              "is no DB write path in this phase. The plan above was written; "
              "no database changes were made.")
        return 3
    print("READ-ONLY — no database writes were made (dry-run plan only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

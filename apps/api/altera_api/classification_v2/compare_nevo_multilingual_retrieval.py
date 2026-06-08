"""Phase Quality-V2-AI (Part G) — baseline vs multilingual retrieval benchmark.

Runs the SAME project products through baseline V2 retrieval and multilingual V2
retrieval (the generated FR/DE reference), then reports whether the multilingual
reference is an adoption candidate. Read-only: reads the project + reference,
writes a comparison CSV + JSON. No DB writes, no routes, no production change —
adoption still requires a human decision and high_risk = 0.

    python -m altera_api.classification_v2.compare_nevo_multilingual_retrieval \
        --project-id <uuid> --baseline-reference-source nevo \
        --multilingual-reference /tmp/altera-quality/nevo_reference_multilingual.csv \
        --output-dir /tmp/altera-quality --top-k 20 \
        --cache-dir /tmp/altera-quality/cache --require-voyage
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from altera_api.classification_v2 import nevo_multilingual_conservative as conservative
from altera_api.classification_v2 import nevo_v2_project_batch_dry_run as proj
from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.nevo_multilingual_reference import (
    load_multilingual_nevo_reference,
)
from altera_api.classification_v2.nevo_v2_batch_dry_run import (
    DEFAULT_EMBEDDING_MODEL,
    _build_matcher,
    _write_csv,
)

_BUCKETS = ("auto_ready", "safety_downgrade", "needs_review", "no_match",
            "true_high_risk")
#: Lower rank = closer to safe auto-enrich. Used to classify improve/regress.
_RANK = {b: i for i, b in enumerate(_BUCKETS)}
COMPARISON_COLUMNS = [
    "product_id", "product_name", "baseline_bucket", "multilingual_bucket",
    "change", "baseline_nevo_code", "multilingual_nevo_code",
    "baseline_top1", "multilingual_top1", "baseline_confidence",
    "multilingual_confidence", "baseline_matches_existing_v2",
    "multilingual_matches_existing_v2",
]


def _args_for(base: argparse.Namespace, *, multilingual: str | None
              ) -> SimpleNamespace:
    return SimpleNamespace(
        evaluator_fake=base.evaluator_fake, require_voyage=base.require_voyage,
        embedding_model=base.embedding_model,
        reference_source=base.baseline_reference_source,
        reference=base.baseline_reference, multilingual_reference=multilingual,
        cache_dir=base.cache_dir, top_k=base.top_k, batch_size=base.batch_size,
        debug=base.debug)


def _bucket_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {b: sum(1 for r in rows if r["_bucket"] == b) for b in _BUCKETS}


def _top1(row: dict[str, Any]) -> str:
    names = _s(row.get("top_5_candidate_names"))
    return names.split("|")[0].strip() if names else ""


def compare(*, baseline_rows: list[dict[str, Any]],
            multilingual_rows: list[dict[str, Any]]) -> dict[str, Any]:
    base_by_pid = {_s(r["product_id"]): r for r in baseline_rows}
    rows_out: list[dict[str, Any]] = []
    improved = regressed = changed = needing_review = 0
    base_agree = ml_agree = 0

    for ml in multilingual_rows:
        pid = _s(ml["product_id"])
        base = base_by_pid.get(pid)
        if base is None:
            continue
        b_bucket, m_bucket = base["_bucket"], ml["_bucket"]
        if base.get("batch_matches_existing_v2") == "true":
            base_agree += 1
        if ml.get("batch_matches_existing_v2") == "true":
            ml_agree += 1
        if m_bucket == "needs_review":
            needing_review += 1
        if _RANK[m_bucket] < _RANK[b_bucket]:
            change = "improved"
            improved += 1
        elif _RANK[m_bucket] > _RANK[b_bucket]:
            change = "regressed"
            regressed += 1
        elif _s(base["batch_nevo_code"]) != _s(ml["batch_nevo_code"]):
            change = "changed_candidate"
            changed += 1
        else:
            change = "same"
        rows_out.append({
            "product_id": pid, "product_name": _s(ml["product_name"]),
            "baseline_bucket": b_bucket, "multilingual_bucket": m_bucket,
            "change": change,
            "baseline_nevo_code": _s(base["batch_nevo_code"]),
            "multilingual_nevo_code": _s(ml["batch_nevo_code"]),
            "baseline_top1": _top1(base), "multilingual_top1": _top1(ml),
            "baseline_confidence": _s(base.get("confidence")),
            "multilingual_confidence": _s(ml.get("confidence")),
            "baseline_matches_existing_v2": _s(
                base.get("batch_matches_existing_v2")),
            "multilingual_matches_existing_v2": _s(
                ml.get("batch_matches_existing_v2")),
        })

    base_counts = _bucket_counts(baseline_rows)
    ml_counts = _bucket_counts(multilingual_rows)
    n = len(rows_out)
    regression_threshold = max(2, math.ceil(0.05 * n)) if n else 0

    ml_high_risk = ml_counts["true_high_risk"]
    base_high_risk = base_counts["true_high_risk"]
    improvement = (ml_counts["auto_ready"] > base_counts["auto_ready"]
                   or ml_counts["no_match"] < base_counts["no_match"])
    regressions_ok = regressed <= regression_threshold
    agreement_ok = ml_agree >= base_agree - 1  # allow tiny noise.

    if ml_high_risk > base_high_risk or ml_high_risk > 0:
        recommendation = "reject_due_to_regressions"
    elif not regressions_ok:
        recommendation = "reject_due_to_regressions"
    elif improvement and agreement_ok:
        recommendation = "adopt_multilingual_reference_candidate"
    else:
        recommendation = "needs_review_before_adoption"

    summary = {
        "phase": "quality-v2-ai",
        "products_compared": n,
        "baseline_counts": base_counts,
        "multilingual_counts": ml_counts,
        "true_high_risk_delta": ml_high_risk - base_high_risk,
        "rows_improved": improved,
        "rows_regressed": regressed,
        "rows_changed_candidate": changed,
        "rows_needing_review": needing_review,
        "regression_threshold": regression_threshold,
        "existing_v2_agreement_baseline": base_agree,
        "existing_v2_agreement_multilingual": ml_agree,
        "recommendation": recommendation,
    }
    return {"summary": summary, "rows": rows_out}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "compare_nevo_multilingual_retrieval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--multilingual-reference", required=True)
    ap.add_argument("--baseline-reference-source", choices=["fixture", "nevo"],
                    default="nevo")
    ap.add_argument("--baseline-reference", default=None)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--cache-dir", default="/tmp/altera-quality/cache")
    ap.add_argument("--run-id", default="run")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-products", type=int, default=None)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--require-voyage", action="store_true")
    ap.add_argument("--evaluator-fake", action="store_true")
    ap.add_argument("--debug", action="store_true")
    # Phase Quality-V2-AI conservative experiment (raw default = unchanged).
    ap.add_argument("--decision-mode", choices=["raw", "conservative"],
                    default="raw",
                    help="raw (default) keeps existing behaviour; conservative "
                         "only switches to a multilingual candidate on a clear, "
                         "guarded improvement of a baseline failure.")
    ap.add_argument("--allow-multilingual-overwrite-auto-ready",
                    action="store_true",
                    help="UNSAFE-FOR-NOW: allow conservative mode to replace a "
                         "baseline auto_ready candidate.")
    ap.add_argument("--min-confidence", type=float,
                    default=conservative.DEFAULT_MIN_CONFIDENCE,
                    help="conservative confidence floor for a switch.")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if store is None:
        from altera_api.api.store_factory import get_store
        store = get_store()

    from uuid import UUID
    project_id = UUID(_s(args.project_id))
    store.get_project(project_id)
    products = list(store.list_products_for_project(project_id))
    if args.limit_products:
        products = products[: args.limit_products]
    records = list(store.list_enrichment_records_for_project(project_id))

    groups = proj.dedupe_products(products, enabled=True)
    existing = proj._existing_v2_index(records)

    base_built = _build_matcher(_args_for(args, multilingual=None))
    if isinstance(base_built, int):
        return base_built
    base_matcher = base_built[0]
    ml_built = _build_matcher(
        _args_for(args, multilingual=args.multilingual_reference))
    if isinstance(ml_built, int):
        return ml_built
    ml_matcher = ml_built[0]

    baseline_rows = proj.build_results(groups, matcher=base_matcher,
                                       top_k=args.top_k, existing=existing)
    multilingual_rows = proj.build_results(groups, matcher=ml_matcher,
                                           top_k=args.top_k, existing=existing)
    result = compare(baseline_rows=baseline_rows,
                     multilingual_rows=multilingual_rows)
    s = result["summary"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = _s(args.project_id)
    # Raw comparison is ALWAYS written unchanged (preserve before/after).
    csv_path = (out_dir
                / f"nevo_multilingual_retrieval_comparison_{pid}.csv")
    _write_csv(csv_path, COMPARISON_COLUMNS, result["rows"])
    json_path = (out_dir
                 / f"nevo_multilingual_retrieval_comparison_{pid}.json")
    json_path.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print("# NEVO multilingual retrieval benchmark (read-only — no DB writes)")
    print(f"  products={s['products_compared']} "
          f"improved={s['rows_improved']} regressed={s['rows_regressed']} "
          f"needs_review={s['rows_needing_review']}")
    print(f"  baseline={s['baseline_counts']}")
    print(f"  multilingual={s['multilingual_counts']}")
    print(f"  true_high_risk_delta={s['true_high_risk_delta']} "
          f"agreement {s['existing_v2_agreement_baseline']}"
          f"->{s['existing_v2_agreement_multilingual']}")
    print(f"  RAW RECOMMENDATION: {s['recommendation']}")
    print(f"  Comparison CSV: {csv_path}")
    print(f"  Summary JSON: {json_path}")

    if args.decision_mode == "conservative":
        coverage = _coverage(args.multilingual_reference)
        cons = conservative.conservative_decisions(
            result["rows"], coverage=coverage,
            allow_overwrite_auto_ready=(
                args.allow_multilingual_overwrite_auto_ready),
            min_confidence=args.min_confidence)
        cs = cons["summary"]
        cons_csv = (out_dir
                    / f"nevo_multilingual_retrieval_conservative_{pid}.csv")
        _write_csv(cons_csv,
                   COMPARISON_COLUMNS + conservative.CONSERVATIVE_EXTRA_COLUMNS,
                   cons["rows"])
        cons_json = (out_dir
                     / f"nevo_multilingual_retrieval_conservative_{pid}.json")
        cons_json.write_text(json.dumps(cs, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        agree = cs["agreement_with_existing_v2"]
        print("")
        print("# Conservative decision layer (default keep baseline)")
        print(f"  switches={cs['conservative_switch_count']} "
              f"kept_baseline={cs['conservative_kept_baseline_count']} "
              f"blocked_regressions={cs['conservative_blocked_regression_count']}")
        print(f"  improved={cs['conservative_improved_count']} "
              f"regressed={cs['conservative_regressed_count']} "
              f"true_high_risk_delta={cs['true_high_risk_delta']}")
        print(f"  conservative_counts={cs['conservative_counts']}")
        print(f"  agreement baseline={agree['baseline']} raw={agree['raw']} "
              f"conservative={agree['conservative']}")
        print(f"  coverage={cs['multilingual_coverage']}")
        print(f"  CONSERVATIVE RECOMMENDATION: {cs['recommendation']}")
        print(f"  Conservative CSV: {cons_csv}")
        print(f"  Conservative JSON: {cons_json}")

    print("READ-ONLY — no database writes were made.")
    return 0


def _coverage(multilingual_reference: str | None) -> float | None:
    """Fraction of multilingual rows with a FR or DE name (None if unknown)."""
    if not multilingual_reference:
        return None
    try:
        rows = load_multilingual_nevo_reference(multilingual_reference)
    except (FileNotFoundError, OSError):
        return None
    if not rows:
        return 0.0
    covered = sum(1 for r in rows
                  if _s(r.get("nevo_food_name_fr"))
                  or _s(r.get("nevo_food_name_de")))
    return round(covered / len(rows), 4)


if __name__ == "__main__":
    raise SystemExit(main())

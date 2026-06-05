"""Phase Quality-V2-AF — consolidated batch review package (read-only).

Merges the project/batch dry-run review buckets (safety_downgrade, needs_review,
no_match, existing_v2_diffs, and optionally true_high_risk / policy_excluded)
into ONE reviewer-facing CSV with blank decision columns, so a human can correct
the batch and feed approvals / gold cases / alias-rule candidates back. It reads
the existing batch artifacts and writes a package + summary — never the DB, never
a route.

    python -m altera_api.classification_v2.build_nevo_v2_batch_review_package \
        --project-id <uuid> --output-dir /tmp/altera-quality
        # explicit --batch-results / --safety-downgrade / --needs-review /
        # --no-match / --existing-v2-diffs override auto-discovery.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import _s

REVIEWER_COLUMNS = [
    "manual_decision", "reviewer_notes", "approved_nevo_code",
    "approved_nevo_name", "approved_protein_g_per_100g", "alias_candidate",
    "rule_candidate", "gold_case_decision",
]
REVIEW_PACKAGE_COLUMNS = [
    "project_id", "run_id", "review_source", "review_priority", "product_id",
    "canonical_product_key", "product_name", "brand", "category", "ingredients",
    "duplicate_count", "v2_outcome", "safety_action", "suggested_action",
    "nevo_code", "nevo_food_name", "protein_g_per_100g", "confidence",
    "match_type", "top_5_candidate_names", "top_5_candidate_codes",
    "top_5_similarities", "rejection_summary", "existing_v2_nevo_code",
    "existing_v2_nevo_name", "batch_nevo_code", "batch_nevo_name",
    "batch_matches_existing_v2", "diff_bucket", *REVIEWER_COLUMNS,
]

#: (review_source, default filename slug). Order = package order.
_SOURCES = (
    ("safety_downgrade", "safety_downgrade"),
    ("needs_review", "needs_review"),
    ("no_match", "no_match"),
    ("existing_v2_diff", "existing_v2_diffs"),
)
_OPTIONAL_SOURCES = (
    ("true_high_risk", "true_high_risk"),
    ("policy_excluded", "policy_excluded"),
)


def review_priority(source: str, raw: dict[str, Any]) -> str:
    if source == "true_high_risk":
        return "P0"
    if source in ("existing_v2_diff", "safety_downgrade", "needs_review"):
        return "P1"
    if source == "no_match":
        return "P2" if _s(raw.get("top_5_candidate_names")) else "P3"
    if source == "policy_excluded":
        return "P3"
    return "P1"


def _package_row(raw: dict[str, Any], *, source: str, project_id: str,
                 run_id: str, ctx_by_name: dict[str, dict[str, Any]],
                 ) -> dict[str, Any]:
    name = _s(raw.get("product_name"))
    ctx = ctx_by_name.get(name, {})

    def g(*keys: str) -> str:
        for k in keys:
            v = _s(raw.get(k)) or _s(ctx.get(k))
            if v:
                return v
        return ""

    row = {
        "project_id": project_id, "run_id": run_id, "review_source": source,
        "review_priority": review_priority(source, raw),
        "product_id": g("product_id"),
        "canonical_product_key": g("canonical_product_key"),
        "product_name": name, "brand": g("brand"), "category": g("category"),
        "ingredients": g("ingredients"),
        "duplicate_count": g("duplicate_count"), "v2_outcome": g("v2_outcome"),
        "safety_action": g("safety_action"),
        "suggested_action": g("suggested_action"),
        "nevo_code": g("batch_nevo_code", "nevo_code"),
        "nevo_food_name": g("batch_nevo_name", "nevo_food_name"),
        "protein_g_per_100g": g("protein_g_per_100g"),
        "confidence": g("confidence"), "match_type": g("match_type"),
        "top_5_candidate_names": g("top_5_candidate_names"),
        "top_5_candidate_codes": g("top_5_candidate_codes"),
        "top_5_similarities": g("top_5_similarities"),
        "rejection_summary": g("rejection_summary"),
        "existing_v2_nevo_code": g("existing_v2_nevo_code"),
        "existing_v2_nevo_name": g("existing_v2_nevo_name"),
        "batch_nevo_code": g("batch_nevo_code", "nevo_code"),
        "batch_nevo_name": g("batch_nevo_name", "nevo_food_name"),
        "batch_matches_existing_v2": (
            g("batch_matches_existing_v2")
            or ("false" if source == "existing_v2_diff" else "")),
        "diff_bucket": g("diff_bucket"),
    }
    row.update(dict.fromkeys(REVIEWER_COLUMNS, ""))
    return row


def _read_rows(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _auto_discover(out_dir: Path, slug: str, project_id: str) -> Path | None:
    pattern = f"nevo_v2_project_batch_{slug}_{project_id}_*.csv"
    matches = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _run_id_from(path: Path | None, project_id: str) -> str | None:
    if path is None:
        return None
    m = re.search(rf"_{re.escape(project_id)}_(.+)\.csv$", path.name)
    return m.group(1) if m else None


def build_package(*, project_id: str, run_id: str, sources: dict[str, list[dict]],
                  ctx_rows: list[dict[str, Any]],
                  include_optional: tuple[str, ...]) -> list[dict[str, Any]]:
    ctx_by_name = {_s(r.get("product_name")): r for r in ctx_rows
                   if _s(r.get("product_name"))}
    out: list[dict[str, Any]] = []
    order = [s for s, _ in _SOURCES] + [s for s in
                                        ("true_high_risk", "policy_excluded")
                                        if s in include_optional]
    for source in order:
        for raw in sources.get(source, []):
            out.append(_package_row(raw, source=source, project_id=project_id,
                                    run_id=run_id, ctx_by_name=ctx_by_name))
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "build_nevo_v2_batch_review_package",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--batch-results", default=None)
    ap.add_argument("--safety-downgrade", default=None)
    ap.add_argument("--needs-review", default=None)
    ap.add_argument("--no-match", default=None)
    ap.add_argument("--existing-v2-diffs", default=None)
    ap.add_argument("--true-high-risk", default=None)
    ap.add_argument("--policy-excluded", default=None)
    ap.add_argument("--include-policy-excluded", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out_dir = Path(args.output_dir)
    pid = _s(args.project_id)

    explicit = {
        "safety_downgrade": args.safety_downgrade,
        "needs_review": args.needs_review,
        "no_match": args.no_match,
        "existing_v2_diff": args.existing_v2_diffs,
        "true_high_risk": args.true_high_risk,
        "policy_excluded": args.policy_excluded,
    }
    slugs = {**dict(_SOURCES), **dict(_OPTIONAL_SOURCES)}
    resolved: dict[str, Path | None] = {}
    for source, slug in slugs.items():
        if explicit[source]:
            resolved[source] = Path(explicit[source])
        else:
            resolved[source] = _auto_discover(out_dir, slug, pid)

    batch_results_path = (Path(args.batch_results) if args.batch_results
                          else _auto_discover(out_dir, "results", pid))

    run_id = args.run_id
    if run_id is None:
        for cand in (batch_results_path, resolved["safety_downgrade"],
                     resolved["no_match"]):
            run_id = _run_id_from(cand, pid)
            if run_id:
                break
    run_id = run_id or "run"

    sources = {s: _read_rows(resolved[s]) for s in explicit}
    ctx_rows = _read_rows(batch_results_path)
    include_optional = ("policy_excluded",) if args.include_policy_excluded else ()
    rows = build_package(project_id=pid, run_id=run_id, sources=sources,
                         ctx_rows=ctx_rows, include_optional=include_optional)

    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = out_dir / f"nevo_v2_batch_review_package_{pid}_{run_id}.csv"
    with pkg_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_PACKAGE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    count_by_source = {s: sum(1 for r in rows if r["review_source"] == s)
                       for s in {r["review_source"] for r in rows}}
    count_by_priority = {p: sum(1 for r in rows if r["review_priority"] == p)
                         for p in ("P0", "P1", "P2", "P3")}
    summary = {
        "project_id": pid, "run_id": run_id,
        "total_review_rows": len(rows),
        "count_by_review_source": count_by_source,
        "count_by_priority": count_by_priority,
        "input_files_used": {s: str(resolved[s]) if resolved[s] else None
                             for s in explicit}
        | {"batch_results": str(batch_results_path)
           if batch_results_path else None},
        "output_paths": {"review_package_csv": str(pkg_path)},
    }
    json_path = out_dir / f"nevo_v2_batch_review_summary_{pid}_{run_id}.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print("# NEVO V2 batch review package (read-only — no database writes)")
    print(f"  project={pid} run_id={run_id} total_rows={len(rows)}")
    print(f"  by_source={count_by_source}")
    print(f"  by_priority={count_by_priority}")
    print(f"  Package CSV: {pkg_path}")
    print(f"  Summary JSON: {json_path}")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase Quality-V2-AD — project-level NEVO V2 batch DRY-RUN.

Same industrial batch/dedup/matching/reporting as ``nevo_v2_batch_dry_run`` but
sourcing products from an existing Altera project (read-only) instead of a
retailer CSV — so we can exercise the 30k-style pipeline on the pilot project
without a CSV. It additionally compares the batch match against the project's
already-applied V2 records.

Strictly read-only: loads project + products + enrichment records, writes
artifacts only. It reads ONLY product-identification fields (product_name,
brand, retailer_category, ingredients_text, labels) — never any commercial
volume/sales/price/margin field. No DB writes, no route imports it, V1 default,
embeddings off by default.

    python -m altera_api.classification_v2.nevo_v2_project_batch_dry_run \
        --project-id <uuid> --output-dir /tmp/altera-quality \
        --top-k 20 --cache-dir /tmp/altera-quality/cache \
        --matcher-version v2-embeddings --require-voyage
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2 import nevo_v2_batch_dry_run as ac
from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.nevo_review_workflow import REVIEWER_BLANK_COLUMNS
from altera_api.classification_v2.nevo_v2_protein_split import SPLIT_SOURCE_VERSION
from altera_api.domain.enrichment import SOURCE_VERSION_V2_EMBEDDINGS

# Per-product result row (Part D adds the existing-V2 comparison fields).
PROJECT_RESULT_COLUMNS = [
    "product_id", "canonical_product_key", "representative_product_name",
    "product_name", "brand", "category", "duplicate_count", "v2_outcome",
    "safety_action", "batch_nevo_code", "batch_nevo_name", "protein_g_per_100g",
    "confidence", "match_type", "top_5_candidate_names", "top_5_candidate_codes",
    "top_5_similarities", "rejection_summary", "review_priority",
    "suggested_action", "existing_v2_total_record", "existing_v2_split_record",
    "existing_v2_nevo_code", "existing_v2_nevo_name", "batch_matches_existing_v2",
]
PROJECT_DEDUP_COLUMNS = [
    "canonical_product_key", "representative_product_name", "duplicate_count",
    "product_ids", "safe_fields_used",
]

# Partition buckets (Quality-V2-AE). ``high_risk`` file is a back-compat alias
# of ``true_high_risk``.
_PARTITION_BUCKETS = ("auto_ready", "safety_downgrade", "needs_review",
                      "no_match", "true_high_risk")
_REVIEW_PACKAGE_FILES = (
    ("auto_ready", "auto_ready"), ("safety_downgrade", "safety_downgrade"),
    ("needs_review", "needs_review"), ("no_match", "no_match"),
    ("true_high_risk", "true_high_risk"), ("high_risk", "true_high_risk"),
)

DIFF_CSV_COLUMNS = [
    "product_name", "existing_v2_nevo_code", "existing_v2_nevo_name",
    "batch_nevo_code", "batch_nevo_name", "safety_action", "suggested_action",
    "confidence", "top_5_candidate_names", "diff_bucket",
]


def _diff_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Products with an applied V2 record the batch did NOT reproduce."""
    out: list[dict[str, Any]] = []
    for r in results:
        if r["existing_v2_total_record"] and r["batch_matches_existing_v2"] != "true":
            out.append({
                "product_name": r["product_name"],
                "existing_v2_nevo_code": r["existing_v2_nevo_code"],
                "existing_v2_nevo_name": r["existing_v2_nevo_name"],
                "batch_nevo_code": r["batch_nevo_code"],
                "batch_nevo_name": r["batch_nevo_name"],
                "safety_action": r["safety_action"],
                "suggested_action": r["suggested_action"],
                "confidence": r["confidence"],
                "top_5_candidate_names": r["top_5_candidate_names"],
                "diff_bucket": ac.diff_bucket(r),
            })
    return out


def _product_descriptor(product: Any) -> dict[str, Any]:
    """Safe descriptor — identification fields ONLY (never commercial)."""
    labels = getattr(product, "labels", None) or ()
    return {
        "product_name": _s(getattr(product, "product_name", "")),
        "brand": _s(getattr(product, "brand", "")),
        "category": _s(getattr(product, "retailer_category", "")),
        "ingredients": _s(getattr(product, "ingredients_text", "")),
        "labels": [str(x) for x in labels],
        "pack_size": _s(getattr(product, "pack_size", "")),
    }


def dedupe_products(products: list[Any], *, enabled: bool) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for idx, product in enumerate(products):
        desc = _product_descriptor(product)
        key = ac.canonical_key(desc) if enabled else f"product-{idx}"
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "canonical_product_key": key, "descriptor": desc,
                "representative_product_name": desc["product_name"],
                "raw_row_indices": [idx], "products": [product],
                "safe_fields_used": ac._safe_fields_used(desc),
            }
        else:
            g["raw_row_indices"].append(idx)
            g["products"].append(product)
    return list(groups.values())


def _existing_v2_index(records: list[Any]) -> dict[str, dict[str, Any]]:
    by_pid: dict[str, list[Any]] = {}
    for r in records:
        by_pid.setdefault(_s(getattr(r, "product_id", "")), []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for pid, recs in by_pid.items():
        totals = [r for r in recs
                  if getattr(r, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS
                  and getattr(r, "nutrient", None) == "protein_pct"]
        splits = [r for r in recs
                  if getattr(r, "source_version", None) == SPLIT_SOURCE_VERSION]
        code = name = ""
        if totals:
            md = getattr(totals[0], "source_metadata", None) or {}
            code = _s(md.get("nevo_code") or md.get("approved_nevo_code"))
            name = _s(md.get("nevo_food_name") or md.get("approved_nevo_name"))
        out[pid] = {"total": bool(totals), "split": bool(splits),
                    "code": code, "name": name}
    return out


def _matches_existing(batch_code: str, existing: dict[str, Any]) -> str:
    if not existing["total"] or not existing["code"] or not batch_code:
        return "unknown"
    return "true" if batch_code == existing["code"] else "false"


def build_results(groups: list[dict[str, Any]], *, matcher: Any, top_k: int,
                  existing: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        match = ac._match_group(group, matcher=matcher, top_k=top_k)
        batch_code = match["nevo_code"]
        for product in group["products"]:
            pid = _s(getattr(product, "id", ""))
            ex = existing.get(pid, {"total": False, "split": False,
                                    "code": "", "name": ""})
            rows.append({
                "product_id": pid,
                "canonical_product_key": match["canonical_product_key"],
                "representative_product_name": match["representative_product_name"],
                "product_name": _s(getattr(product, "product_name", "")),
                "brand": _s(getattr(product, "brand", "")),
                "category": _s(getattr(product, "retailer_category", "")),
                "duplicate_count": match["duplicate_count"],
                "v2_outcome": match["v2_outcome"],
                "safety_action": match["safety_action"],
                "batch_nevo_code": batch_code,
                "batch_nevo_name": match["nevo_food_name"],
                "protein_g_per_100g": match["protein_g_per_100g"],
                "confidence": match["confidence"],
                "match_type": match["match_type"],
                "top_5_candidate_names": match["top_5_candidate_names"],
                "top_5_candidate_codes": match["top_5_candidate_codes"],
                "top_5_similarities": match["top_5_similarities"],
                "rejection_summary": match["rejection_summary"],
                "review_priority": match["review_priority"],
                "suggested_action": match["suggested_action"],
                "existing_v2_total_record": ex["total"],
                "existing_v2_split_record": ex["split"],
                "existing_v2_nevo_code": ex["code"],
                "existing_v2_nevo_name": ex["name"],
                "batch_matches_existing_v2": _matches_existing(batch_code, ex),
                # internal — for bucketing only (dropped from CSV columns).
                "_bucket": ac.batch_bucket(match),
            })
    return rows


def build_summary(*, project_id: str, run_id: str, raw_count: int,
                  groups: list[dict[str, Any]], results: list[dict[str, Any]],
                  provider: str, model: str, top_k: int,
                  generated_at: str | None) -> dict[str, Any]:
    unique = len(groups)
    dedupe_pct = round((1 - unique / raw_count) * 100, 2) if raw_count else 0.0
    by_bucket = {b: sum(1 for r in results if r["_bucket"] == b)
                 for b in _PARTITION_BUCKETS}
    true_high_risk = by_bucket["true_high_risk"]
    return {
        "project_id": project_id,
        "run_id": run_id,
        "generated_at": generated_at,
        "raw_product_count": raw_count,
        "unique_product_count": unique,
        "duplicate_group_count": sum(1 for g in groups
                                     if len(g["raw_row_indices"]) > 1),
        "max_duplicate_group_size": max(
            (len(g["raw_row_indices"]) for g in groups), default=0),
        "dedupe_reduction_pct": dedupe_pct,
        "embedding_provider": provider,
        "embedding_model": model,
        "top_k": top_k,
        "auto_ready_count": by_bucket["auto_ready"],
        "safety_downgrade_count": by_bucket["safety_downgrade"],
        "needs_review_count": by_bucket["needs_review"],
        "no_match_count": by_bucket["no_match"],
        "true_high_risk_count": true_high_risk,
        "high_risk_count": true_high_risk,  # back-compat alias
        "policy_excluded_count": sum(
            1 for r in results if r["v2_outcome"] == "policy_excluded"),
        "existing_v2_total_count": sum(
            1 for r in results if r["existing_v2_total_record"]),
        "existing_v2_split_product_count": sum(
            1 for r in results if r["existing_v2_split_record"]),
        "batch_matches_existing_v2_count": sum(
            1 for r in results if r["batch_matches_existing_v2"] == "true"),
        "batch_differs_from_existing_v2_count": sum(
            1 for r in results if r["batch_matches_existing_v2"] == "false"),
        "existing_v2_missing_from_batch_count": sum(
            1 for r in results
            if r["existing_v2_total_record"] and not r["batch_nevo_code"]),
        "existing_v2_diff_count": len(_diff_rows(results)),
        # Quality-V2-AE — investigate only for TRUE high-risk auto-applies.
        "recommendation": ("investigate_high_risk" if true_high_risk
                           else "ready_for_human_review"),
    }


def write_artifacts(out_dir: str | Path, project_id: str, run_id: str, *,
                    groups, results, summary) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"{project_id}_{run_id}"
    paths: dict[str, str] = {}

    p = out / f"nevo_v2_project_batch_summary_{suffix}.json"
    p.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    paths["summary_json"] = str(p)

    p = out / f"nevo_v2_project_batch_results_{suffix}.csv"
    ac._write_csv(p, PROJECT_RESULT_COLUMNS, results)
    paths["results_csv"] = str(p)

    dedup_rows = [{
        "canonical_product_key": g["canonical_product_key"],
        "representative_product_name": g["representative_product_name"],
        "duplicate_count": len(g["raw_row_indices"]),
        "product_ids": " ".join(_s(getattr(p_, "id", ""))
                                for p_ in g["products"]),
        "safe_fields_used": " ".join(g["safe_fields_used"]),
    } for g in groups]
    p = out / f"nevo_v2_project_batch_dedup_groups_{suffix}.csv"
    ac._write_csv(p, PROJECT_DEDUP_COLUMNS, dedup_rows)
    paths["dedup_groups_csv"] = str(p)

    review_cols = PROJECT_RESULT_COLUMNS + list(REVIEWER_BLANK_COLUMNS)
    for fname, bucket in _REVIEW_PACKAGE_FILES:
        sel = [{**r, **dict.fromkeys(REVIEWER_BLANK_COLUMNS, "")}
               for r in results if r["_bucket"] == bucket]
        p = out / f"nevo_v2_project_batch_{fname}_{suffix}.csv"
        ac._write_csv(p, review_cols, sel)
        paths[f"{fname}_csv"] = str(p)

    # Part D — existing-V2 diff diagnostics.
    p = out / f"nevo_v2_project_batch_existing_v2_diffs_{suffix}.csv"
    ac._write_csv(p, DIFF_CSV_COLUMNS, _diff_rows(results))
    paths["existing_v2_diffs_csv"] = str(p)
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "nevo_v2_project_batch_dry_run",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--cache-dir", default="/tmp/altera-quality/cache")
    ap.add_argument("--run-id", default="run")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-products", type=int, default=None)
    ap.add_argument("--matcher-version", choices=["v2-embeddings"],
                    default="v2-embeddings")
    ap.add_argument("--embedding-model", default=ac.DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"],
                    default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--dedupe", choices=["true", "false"], default="true")
    ap.add_argument("--require-voyage", action="store_true")
    ap.add_argument("--evaluator-fake", action="store_true")
    ap.add_argument("--debug", action="store_true")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        project_id = UUID(str(args.project_id))
    except (ValueError, TypeError):
        print(f"FATAL: invalid --project-id {args.project_id!r}")
        return 2

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()

    project = store.get_project(project_id)
    if project is None:
        print(f"FATAL: project {project_id} not found")
        return 2
    products = list(store.list_products_for_project(project_id))
    if args.limit_products is not None:
        products = products[: args.limit_products]
    records = list(store.list_enrichment_records_for_project(project_id))

    built = ac._build_matcher(args)
    if isinstance(built, int):
        return built
    matcher, provider_name, model = built

    from datetime import UTC, datetime
    generated_at = datetime.now(UTC).isoformat()

    groups = dedupe_products(products, enabled=args.dedupe == "true")
    existing = _existing_v2_index(records)
    results = build_results(groups, matcher=matcher, top_k=args.top_k,
                            existing=existing)
    summary = build_summary(
        project_id=str(project_id), run_id=args.run_id, raw_count=len(products),
        groups=groups, results=results, provider=provider_name, model=model,
        top_k=args.top_k, generated_at=generated_at)
    paths = write_artifacts(args.output_dir, str(project_id), args.run_id,
                            groups=groups, results=results, summary=summary)

    print("# NEVO V2 PROJECT batch DRY-RUN (read-only — no database writes)")
    print(f"  project={summary['project_id']} run_id={summary['run_id']}")
    print(f"  raw_products={summary['raw_product_count']} "
          f"unique={summary['unique_product_count']} "
          f"dedupe_reduction={summary['dedupe_reduction_pct']}%")
    print(f"  auto_ready={summary['auto_ready_count']} "
          f"safety_downgrade={summary['safety_downgrade_count']} "
          f"needs_review={summary['needs_review_count']} "
          f"no_match={summary['no_match_count']} "
          f"policy_excluded={summary['policy_excluded_count']} "
          f"true_high_risk={summary['true_high_risk_count']}")
    print(f"  existing_v2_total={summary['existing_v2_total_count']} "
          f"existing_v2_split={summary['existing_v2_split_product_count']} "
          f"matches_existing_v2={summary['batch_matches_existing_v2_count']} "
          f"differs_existing_v2={summary['batch_differs_from_existing_v2_count']} "
          f"existing_v2_diffs={summary['existing_v2_diff_count']}")
    print(f"  recommendation={summary['recommendation']}")
    for label, key in (("Summary JSON", "summary_json"),
                       ("Results CSV", "results_csv"),
                       ("Dedup groups", "dedup_groups_csv"),
                       ("Auto-ready", "auto_ready_csv"),
                       ("Needs-review", "needs_review_csv"),
                       ("No-match", "no_match_csv"),
                       ("High-risk", "high_risk_csv")):
        print(f"  {label}: {paths[key]}")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

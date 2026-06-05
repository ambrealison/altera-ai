"""Phase Quality-V2-Y — read-only post-apply audit for NEVO V2 enrichment.

After a V2 apply, confirm the DB state is exactly what we intended: every V2
record is correctly tagged (source=nevo, match_method=ai_assisted,
source_version=v2_embeddings, nutrient=protein_pct, unit=g_per_100g, metadata
present), there are no duplicates, no manual/V1 record was clobbered, and the
applied set matches the approved candidates. Produces a pass/warn/fail report.

Strictly read-only: reads the project's enrichment records + products and the
plan/approved-candidates files; writes report artifacts only. No DB writes, not
imported by any route.

    python -m altera_api.classification_v2.audit_nevo_v2_apply \
        --project-id <uuid> \
        --plan-json .../nevo_v2_apply_plan_<id>.json \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --output-dir /tmp/altera-quality
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2.apply_nevo_v2_plan import (
    ApplyError,
    _read_csv,
    _read_json,
    _s,
)
from altera_api.classification_v2.nevo_v2_scale_baseline import (
    scale_baseline_report,
)
from altera_api.domain.enrichment import (
    SOURCE_VERSION_V2_EMBEDDINGS,
    NutritionEnrichmentSource,
)

_EXPECTED = {
    "source": "nevo",
    "match_method": "ai_assisted",
    "source_version": SOURCE_VERSION_V2_EMBEDDINGS,
    "nutrient": "protein_pct",
    "unit": "g_per_100g",
}

AUDIT_CSV_COLUMNS = [
    "product_id", "product_name", "source", "match_method", "source_version",
    "nutrient", "unit", "enriched_value", "metadata_present", "status",
]
ANOMALY_CSV_COLUMNS = ["product_id", "product_name", "anomaly", "detail"]

#: anomaly types that make the audit FAIL (vs warn-only).
_FAIL_ANOMALIES = frozenset({
    "invalid_source", "invalid_match_method", "invalid_nutrient",
    "invalid_unit", "metadata_missing", "duplicate_v2", "unexpected_v2",
    "manual_conflict", "v1_conflict",
})


def _source_value(record: Any) -> str:
    src = getattr(record, "source", None)
    return _s(getattr(src, "value", src))


def _is_v2(record: Any) -> bool:
    return getattr(record, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS


def _is_manual(record: Any) -> bool:
    return (
        getattr(record, "match_method", None) == "manual"
        or getattr(record, "source", None)
        is NutritionEnrichmentSource.MANUAL_ALTERA
    )


def _is_v1_value(record: Any) -> bool:
    return (
        not _is_v2(record)
        and not _is_manual(record)
        and getattr(record, "enriched_value", None) is not None
    )


def audit_records(
    *, records: list[Any], approved: list[dict[str, Any]],
    plan: dict[str, Any], product_names: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(summary, audit_rows, anomaly_rows)``."""
    protein = [r for r in records
               if _s(getattr(r, "nutrient", "")) == "protein_pct"]
    v2 = [r for r in records if _is_v2(r)]

    approved_ids = {_s(r.get("product_id")) for r in approved}
    name_of = {**{_s(r.get("product_id")): _s(r.get("product_name"))
                  for r in approved}, **product_names}

    anomalies: list[dict[str, Any]] = []

    def flag(pid: str, anomaly: str, detail: str = "") -> None:
        anomalies.append({"product_id": pid,
                          "product_name": name_of.get(pid, ""),
                          "anomaly": anomaly, "detail": detail})

    audit_rows: list[dict[str, Any]] = []
    for r in v2:
        pid = _s(getattr(r, "product_id", ""))
        row_anomalies: list[str] = []
        if _source_value(r) != _EXPECTED["source"]:
            row_anomalies.append("invalid_source")
            flag(pid, "invalid_source", _source_value(r))
        if _s(getattr(r, "match_method", "")) != _EXPECTED["match_method"]:
            row_anomalies.append("invalid_match_method")
            flag(pid, "invalid_match_method", _s(getattr(r, "match_method", "")))
        if _s(getattr(r, "nutrient", "")) != _EXPECTED["nutrient"]:
            row_anomalies.append("invalid_nutrient")
            flag(pid, "invalid_nutrient", _s(getattr(r, "nutrient", "")))
        if _s(getattr(r, "unit", "")) != _EXPECTED["unit"]:
            row_anomalies.append("invalid_unit")
            flag(pid, "invalid_unit", _s(getattr(r, "unit", "")))
        if not getattr(r, "source_metadata", None):
            row_anomalies.append("metadata_missing")
            flag(pid, "metadata_missing")
        if pid not in approved_ids:
            row_anomalies.append("unexpected_v2")
            flag(pid, "unexpected_v2", "V2 record has no approved candidate")
        audit_rows.append({
            "product_id": pid, "product_name": name_of.get(pid, ""),
            "source": _source_value(r),
            "match_method": _s(getattr(r, "match_method", "")),
            "source_version": _s(getattr(r, "source_version", "")),
            "nutrient": _s(getattr(r, "nutrient", "")),
            "unit": _s(getattr(r, "unit", "")),
            "enriched_value": _s(getattr(r, "enriched_value", "")),
            "metadata_present": bool(getattr(r, "source_metadata", None)),
            "status": "anomaly" if row_anomalies else "ok",
        })

    # Duplicate V2 records per (product_id, nutrient).
    dup_counter = Counter(
        (_s(getattr(r, "product_id", "")), _s(getattr(r, "nutrient", "")))
        for r in v2
    )
    duplicate_products = {pid for (pid, _n), c in dup_counter.items() if c > 1}
    for pid in sorted(duplicate_products):
        flag(pid, "duplicate_v2",
             f"{dup_counter[(pid, 'protein_pct')]} V2 protein records")

    # Manual / V1 coexistence on a product that also has a V2 record.
    v2_pids = {_s(getattr(r, "product_id", "")) for r in v2}
    by_pid: dict[str, list[Any]] = {}
    for r in protein:
        by_pid.setdefault(_s(getattr(r, "product_id", "")), []).append(r)
    manual_conflict = set()
    v1_conflict = set()
    for pid in v2_pids:
        recs = by_pid.get(pid, [])
        if any(_is_manual(x) for x in recs):
            manual_conflict.add(pid)
            flag(pid, "manual_conflict",
                 "product has both a manual and a V2 protein record")
        if any(_is_v1_value(x) for x in recs):
            v1_conflict.add(pid)
            flag(pid, "v1_conflict",
                 "product has both a V1 value and a V2 protein record")

    # Approved candidates with no V2 record in the DB.
    missing = sorted(approved_ids - v2_pids)
    for pid in missing:
        flag(pid, "missing_from_db",
             "approved candidate has no V2 record (skipped or not applied)")

    def anom_count(name: str) -> int:
        return sum(1 for a in anomalies if a["anomaly"] == name)

    has_fail = any(a["anomaly"] in _FAIL_ANOMALIES for a in anomalies)
    has_warn = anom_count("missing_from_db") > 0
    status = "fail" if has_fail else ("warn" if has_warn else "pass")
    recommendation = {
        "fail": "rollback_recommended",
        "warn": "investigate_anomalies",
        "pass": "pilot_apply_verified",
    }[status]

    planned = plan.get("planned_operation_count")
    summary = {
        "approved_candidates_count": len(approved),
        "applied_v2_count": len(v2),
        "planned_operation_count": planned,
        "plan_count_matches_applied": (
            planned == len(v2) and len(missing) == 0
        ),
        "matched_approved_count": len(approved_ids & v2_pids),
        "missing_from_db_count": len(missing),
        "unexpected_v2_count": anom_count("unexpected_v2"),
        "duplicate_v2_count": len(duplicate_products),
        "manual_conflict_count": len(manual_conflict),
        "v1_conflict_count": len(v1_conflict),
        "metadata_missing_count": anom_count("metadata_missing"),
        "invalid_source_count": anom_count("invalid_source"),
        "invalid_match_method_count": anom_count("invalid_match_method"),
        "invalid_nutrient_count": anom_count("invalid_nutrient"),
        "invalid_unit_count": anom_count("invalid_unit"),
        "audit_status": status,
        "recommendation": recommendation,
    }
    return summary, audit_rows, anomalies


def _load_v2_state(store: Any, project_id: UUID) -> tuple[list[Any], dict[str, str]]:
    records = list(store.list_enrichment_records_for_project(project_id))
    names: dict[str, str] = {}
    lister = getattr(store, "list_products_for_project", None)
    if callable(lister):
        try:
            for p in lister(project_id):
                names[_s(getattr(p, "id", ""))] = _s(
                    getattr(p, "product_name", "")
                )
        except Exception:  # noqa: BLE001 — names are best-effort enrichment
            names = {}
    return records, names


def write_artifacts(
    out_dir: str | Path, project_id: str, summary: dict[str, Any],
    audit_rows: list[dict[str, Any]], anomalies: list[dict[str, Any]],
) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_apply_audit_{project_id}.json"
    audit_csv = out / f"nevo_v2_apply_audit_{project_id}.csv"
    anom_csv = out / f"nevo_v2_apply_audit_anomalies_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "anomalies": anomalies}, indent=2,
                   ensure_ascii=False),
        encoding="utf-8",
    )
    for path, cols, rows in (
        (audit_csv, AUDIT_CSV_COLUMNS, audit_rows),
        (anom_csv, ANOMALY_CSV_COLUMNS, anomalies),
    ):
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
    return {"audit_json": str(json_path), "audit_csv": str(audit_csv),
            "anomalies_csv": str(anom_csv)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.audit_nevo_v2_apply",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--approved-candidates", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument(
        "--write-scale-baseline", action="store_true",
        help="also write the 30k retailer-scale readiness baseline JSON.",
    )
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        plan = _read_json(args.plan_json, "apply plan")
        approved = _read_csv(args.approved_candidates, "approved candidates")
    except ApplyError as exc:
        print(f"FATAL: {exc}")
        return 2
    # Real project ids are UUIDs; stay lenient for offline/test ids.
    try:
        project_key: Any = UUID(str(args.project_id))
    except (ValueError, TypeError):
        project_key = args.project_id

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()
    if generated_at is None:
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC).isoformat()

    records, product_names = _load_v2_state(store, project_key)
    summary, audit_rows, anomalies = audit_records(
        records=records, approved=approved, plan=plan,
        product_names=product_names,
    )
    summary = {"project_id": _s(args.project_id),
               "generated_at": generated_at, **summary}
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary,
                            audit_rows, anomalies)

    if args.write_scale_baseline:
        baseline_path = (
            Path(args.output_dir) / f"nevo_v2_30k_scale_baseline_{args.project_id}.json"
        )
        baseline_path.write_text(
            json.dumps(scale_baseline_report(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        paths["scale_baseline_json"] = str(baseline_path)

    print("# NEVO V2 post-apply audit (READ-ONLY — no database writes)")
    print(f"  project={summary['project_id']} status={summary['audit_status']} "
          f"recommendation={summary['recommendation']}")
    print(f"  approved={summary['approved_candidates_count']} "
          f"applied_v2={summary['applied_v2_count']} "
          f"matched={summary['matched_approved_count']} "
          f"missing_from_db={summary['missing_from_db_count']} "
          f"unexpected_v2={summary['unexpected_v2_count']}")
    print(f"  duplicates={summary['duplicate_v2_count']} "
          f"manual_conflicts={summary['manual_conflict_count']} "
          f"v1_conflicts={summary['v1_conflict_count']} "
          f"metadata_missing={summary['metadata_missing_count']} "
          f"invalid(src/mm/nut/unit)="
          f"{summary['invalid_source_count']}/"
          f"{summary['invalid_match_method_count']}/"
          f"{summary['invalid_nutrient_count']}/{summary['invalid_unit_count']}")
    for label, key in (("Audit JSON", "audit_json"),
                       ("Audit CSV", "audit_csv"),
                       ("Anomalies CSV", "anomalies_csv")):
        print(f"  {label}: {paths[key]}")
    if "scale_baseline_json" in paths:
        print(f"  Scale baseline JSON: {paths['scale_baseline_json']}")
    print("READ-ONLY — no database writes were made.")
    return {"pass": 0, "warn": 1, "fail": 2}[summary["audit_status"]]


if __name__ == "__main__":
    raise SystemExit(main())

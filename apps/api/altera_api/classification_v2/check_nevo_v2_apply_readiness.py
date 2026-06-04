"""Phase Quality-V2-X — read-only NEVO V2 apply-readiness checker.

Run BEFORE the first real V2 apply (after migration 0037) to confirm the
environment is safe: the provenance columns exist, the plan/approved-candidates
are consistent, the app is still on V1, no route imports V2, and which approved
products already have a manual / V1 / V2 enrichment (those would be skipped).

It is strictly read-only: it reads the plan + approved-candidates and probes the
DB for column existence + existing records. It NEVER writes the DB and is not
imported by any route.

    python -m altera_api.classification_v2.check_nevo_v2_apply_readiness \
        --project-id <uuid> \
        --plan-json .../nevo_v2_apply_plan_<id>.json \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --output-dir /tmp/altera-quality

Exit code: 0 = ready, 1 = not ready (report still written), 2 = bad input.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2.apply_nevo_v2_plan import (
    ApplyError,
    _disposition,
    _read_csv,
    _read_json,
    _s,
    provenance_columns_present,
)

READINESS_CSV_COLUMNS = ["name", "status", "blocking", "detail"]
_CONFLICT_CSV_COLUMNS = ["product_id", "product_name", "conflict"]

_DISP_TO_CONFLICT = {
    "skip_manual": "existing_manual",
    "skip_v1": "existing_v1",
    "skip_v2": "existing_v2",
    "write": "writable",
}


def _routes_import_v2() -> list[str]:
    import altera_api.classification_v2.apply_nevo_v2_plan as anchor

    api_dir = Path(anchor.__file__).resolve().parents[1] / "api"
    return [
        p.name for p in api_dir.rglob("*.py")
        if "classification_v2" in p.read_text(encoding="utf-8")
        or "apply_nevo_v2_plan" in p.read_text(encoding="utf-8")
    ]


def _check(name: str, ok: bool, *, blocking: bool, detail: str = "",
           warn: bool = False) -> dict[str, Any]:
    status = "pass" if ok else ("warn" if warn else "fail")
    return {"name": name, "status": status, "blocking": blocking,
            "detail": detail}


def build_checks(
    *, plan: dict[str, Any], approved: list[dict[str, Any]], project_id: str,
    columns_present: bool,
) -> list[dict[str, Any]]:
    from altera_api.classification_v2.nevo_matcher import (
        resolve_nevo_matcher_version,
    )
    from altera_api.quality_config import embeddings_enabled

    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "provenance_columns_present", columns_present, blocking=True,
        detail="migration 0037 source_version/source_metadata columns exist"
        if columns_present else "migration 0037 NOT applied — apply is blocked",
    ))
    checks.append(_check(
        "plan_project_matches", _s(plan.get("project_id")) == _s(project_id),
        blocking=True,
        detail=f"plan={plan.get('project_id')!r} arg={project_id!r}",
    ))
    planned = plan.get("planned_operation_count")
    checks.append(_check(
        "approved_count_matches_plan", planned == len(approved), blocking=True,
        detail=f"approved={len(approved)} planned={planned}",
    ))
    rec = _s(plan.get("validation_recommendation"))
    checks.append(_check(
        "validation_recommendation", rec == "ready_for_apply_planning",
        blocking=True, warn=(rec == "review_incomplete"),
        detail=f"recommendation={rec!r}"
        + (" (apply needs --allow-incomplete-apply)"
           if rec == "review_incomplete" else ""),
    ))
    checks.append(_check(
        "db_apply_status_expected",
        _s(plan.get("db_apply_status")) == "blocked_pending_schema_migration",
        blocking=True, detail=f"db_apply_status={plan.get('db_apply_status')!r}",
    ))
    no_overwrite = not (
        plan.get("overwrite_existing_v1") or plan.get("overwrite_manual")
        or any(op.get("overwrite_existing_v1") or op.get("overwrite_manual")
               for op in plan.get("operations", []))
    )
    checks.append(_check(
        "no_overwrite_flags", no_overwrite, blocking=True,
        detail="overwrite_existing_v1/overwrite_manual must be false",
    ))
    v1_default = str(resolve_nevo_matcher_version()) == "v1"
    checks.append(_check(
        "v1_default_unchanged", v1_default, blocking=True,
        detail=f"default matcher = {resolve_nevo_matcher_version()}",
    ))
    emb_off = embeddings_enabled() is False
    checks.append(_check(
        "embeddings_off", emb_off, blocking=False, warn=not emb_off,
        detail="embeddings are off" if emb_off
        else "embeddings ENABLED (not required for apply)",
    ))
    offenders = _routes_import_v2()
    checks.append(_check(
        "routes_clean", not offenders, blocking=True,
        detail="no route imports V2/apply" if not offenders
        else f"offending routes: {', '.join(offenders)}",
    ))
    return checks


def compute_conflicts(
    approved: list[dict[str, Any]], store: Any
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {"writable": 0, "existing_manual": 0, "existing_v1": 0,
              "existing_v2": 0, "error": 0}
    rows: list[dict[str, Any]] = []
    for row in approved:
        pid_str = _s(row.get("product_id"))
        name = _s(row.get("product_name"))
        try:
            pid = UUID(pid_str)
        except (ValueError, TypeError):
            counts["error"] += 1
            rows.append({"product_id": pid_str, "product_name": name,
                         "conflict": "error"})
            continue
        existing = store.get_enrichment_records_for_product(pid)
        conflict = _DISP_TO_CONFLICT[_disposition(existing)]
        counts[conflict] += 1
        rows.append({"product_id": pid_str, "product_name": name,
                     "conflict": conflict})
    return counts, rows


def build_readiness(
    *, plan: dict[str, Any], approved: list[dict[str, Any]], project_id: str,
    store: Any, generated_at: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    columns_present = provenance_columns_present(store)
    checks = build_checks(
        plan=plan, approved=approved, project_id=project_id,
        columns_present=columns_present,
    )
    conflict_counts, conflict_rows = compute_conflicts(approved, store)
    ready = not any(
        c["blocking"] and c["status"] == "fail" for c in checks
    )
    summary = {
        "project_id": _s(project_id),
        "generated_at": generated_at,
        "ready": ready,
        "provenance_columns_present": columns_present,
        "checks": checks,
        "conflicts": conflict_counts,
    }
    return summary, conflict_rows


def write_artifacts(
    out_dir: str | Path, project_id: str, summary: dict[str, Any],
    conflict_rows: list[dict[str, Any]],
) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_apply_readiness_{project_id}.json"
    csv_path = out / f"nevo_v2_apply_readiness_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "conflict_rows": conflict_rows}, indent=2,
                   ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=READINESS_CSV_COLUMNS)
        w.writeheader()
        for c in summary["checks"]:
            w.writerow({k: c.get(k, "") for k in READINESS_CSV_COLUMNS})
    return {"readiness_json": str(json_path), "readiness_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "check_nevo_v2_apply_readiness",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--approved-candidates", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
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

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()
    if generated_at is None:
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC).isoformat()

    summary, conflict_rows = build_readiness(
        plan=plan, approved=approved, project_id=args.project_id, store=store,
        generated_at=generated_at,
    )
    paths = write_artifacts(args.output_dir, args.project_id, summary,
                            conflict_rows)

    print("# NEVO V2 apply-readiness check (READ-ONLY — no database writes)")
    print(f"  project={summary['project_id']} READY={summary['ready']}")
    print("-" * 64)
    for c in summary["checks"]:
        flag = {"pass": "ok  ", "warn": "warn", "fail": "FAIL"}[c["status"]]
        print(f"  [{flag}] {c['name']:32} {c['detail']}")
    cc = summary["conflicts"]
    print("-" * 64)
    print(f"  conflicts: writable={cc['writable']} "
          f"existing_v1={cc['existing_v1']} existing_manual={cc['existing_manual']} "
          f"existing_v2={cc['existing_v2']} error={cc['error']}")
    print(f"  Readiness JSON: {paths['readiness_json']}")
    print(f"  Readiness CSV:  {paths['readiness_csv']}")
    print("READ-ONLY — no database writes were made.")
    return 0 if summary["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

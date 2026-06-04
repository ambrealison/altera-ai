"""Phase Quality-V2-T — NEVO V2 apply PLANNING (read-only, no DB writes).

Turns a validated review package into an explicit apply plan that documents
exactly what a future DB-write phase WOULD do — and why it is still blocked. It
reads the validator's outputs (approved-candidates CSV + validation-summary
JSON) and writes plan artifacts only. It never touches the database, imports no
route, does not activate V2, and requires no Supabase migration to run (it
*describes* the migration that a real apply would need).

    python -m altera_api.classification_v2.plan_nevo_v2_apply \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --validation-summary  .../nevo_v2_review_validation_summary_<id>.json \
        --output-dir /tmp/altera-quality --project-id <uuid>

Refuses to plan when the validation recommendation is ``blocked_by_errors``;
refuses ``review_incomplete`` unless ``--allow-incomplete`` is given.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

# A real apply path is NOT implemented. These constants only DESCRIBE the
# intended write so the plan is unambiguous for a future, gated phase.
PLANNED_OPERATION = "create_v2_enrichment_record"
PROPOSED_MATCH_METHOD = "v2_embeddings"
PROPOSED_SOURCE_TAG = "nevo_v2_embeddings"

#: Why a DB apply is blocked, and how to unblock it safely (Part D).
SCHEMA_MIGRATION = {
    "schema_migration_required": True,
    "reason": (
        "The enrichment-records DB CHECK currently allows match_method only in "
        "(deterministic, ai_assisted, manual, none). V2 writes need a "
        "V2-specific source/method tag before a DB apply is safe and reversible."
    ),
    "recommended_options": [
        "Add a 'v2_embeddings' value to the match_method CHECK constraint.",
        "Or add separate source_version / source_metadata (JSONB) columns to "
        "the enrichment-records table and tag V2 rows there.",
    ],
    "rollback_plan": (
        "Set ALTERA_NEVO_MATCHER_VERSION=v1 (or unset it) and do not run apply. "
        "If V2-tagged rows were ever applied, delete the enrichment rows "
        "carrying the V2 match_method / source tag."
    ),
}

PLAN_CSV_COLUMNS = [
    "product_id", "product_name", "approved_nevo_code", "approved_nevo_name",
    "approved_protein_g_per_100g", "source", "planned_operation",
    "requires_schema_migration", "proposed_match_method",
    "proposed_source_tag", "overwrite_existing_v1", "overwrite_manual",
]


class PlanError(Exception):
    """Raised for unreadable inputs."""


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def read_validation_summary(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise PlanError(f"validation summary not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PlanError(f"could not read validation summary {p.name}: {exc}") from exc
    if "recommendation" not in data:
        raise PlanError(
            f"{p.name} has no 'recommendation' — is this a validation summary?"
        )
    return data


def read_approved_candidates(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise PlanError(f"approved-candidates file not found: {p}")
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def build_operations(approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for row in approved:
        ops.append({
            "product_id": _s(row.get("product_id")),
            "product_name": _s(row.get("product_name")),
            "approved_nevo_code": _s(row.get("effective_nevo_code")),
            "approved_nevo_name": _s(row.get("effective_nevo_name")),
            "approved_protein_g_per_100g": _s(
                row.get("effective_protein_g_per_100g")
            ),
            "source": _s(row.get("source")) or "existing",
            "planned_operation": PLANNED_OPERATION,
            "requires_schema_migration": True,
            "proposed_match_method": PROPOSED_MATCH_METHOD,
            "proposed_source_tag": PROPOSED_SOURCE_TAG,
            "overwrite_existing_v1": False,
            "overwrite_manual": False,
        })
    return ops


def build_plan(
    *, approved: list[dict[str, Any]], summary: dict[str, Any],
    project_id: str | None, approved_path: str, summary_path: str,
    blocked_reason: str | None, generated_at: str | None,
) -> dict[str, Any]:
    operations = build_operations(approved)
    return {
        "project_id": project_id,
        "generated_at": generated_at,
        "source_approved_candidates": approved_path,
        "source_validation_summary": summary_path,
        "validation_recommendation": summary.get("recommendation"),
        "apply_ready_count": summary.get("apply_ready_count", len(operations)),
        "planned_operation_count": len(operations),
        "blocked_reason": blocked_reason,
        # DB apply stays blocked until the migration below is in place.
        "db_apply_status": "blocked_pending_schema_migration",
        "overwrite_existing_v1": False,
        "overwrite_manual": False,
        **SCHEMA_MIGRATION,
        "operations": operations,
    }


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _s(value)


def write_plan(
    out_dir: str | Path, project_id: str, plan: dict[str, Any],
) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_apply_plan_{project_id}.json"
    csv_path = out / f"nevo_v2_apply_plan_{project_id}.csv"
    json_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_CSV_COLUMNS)
        w.writeheader()
        for op in plan["operations"]:
            w.writerow({c: _cell(op.get(c, "")) for c in PLAN_CSV_COLUMNS})
    return {"plan_json": str(json_path), "plan_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.plan_nevo_v2_apply",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--approved-candidates", required=True)
    ap.add_argument("--validation-summary", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--project-id", default=None)
    ap.add_argument(
        "--allow-incomplete", action="store_true",
        help="proceed to write a plan even when the validation recommendation "
             "is review_incomplete (pending / needs_more_info rows remain).",
    )
    return ap


def main(argv: list[str] | None = None, *, generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = read_validation_summary(args.validation_summary)
        approved = read_approved_candidates(args.approved_candidates)
    except PlanError as exc:
        print(f"FATAL: {exc}")
        return 2

    recommendation = summary.get("recommendation")
    blocked_reason: str | None = None
    if recommendation == "blocked_by_errors":
        print(
            "FATAL: validation recommendation is 'blocked_by_errors' "
            f"({summary.get('error_count', '?')} errors). Fix the review "
            "package and re-validate before planning an apply. No plan written."
        )
        return 2
    if recommendation == "review_incomplete":
        if not args.allow_incomplete:
            print(
                "FATAL: validation recommendation is 'review_incomplete' "
                f"({summary.get('pending_count', '?')} pending, "
                f"{summary.get('needs_more_info_count', '?')} needs_more_info). "
                "Re-run with --allow-incomplete to plan only the apply-ready "
                "rows. No plan written."
            )
            return 2
        blocked_reason = (
            "review_incomplete: planning only the apply-ready rows "
            "(--allow-incomplete)"
        )
        print(f"WARNING: {blocked_reason}")

    project_id = args.project_id or summary.get("project_id") or "unknown"
    if generated_at is None:
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC).isoformat()

    plan = build_plan(
        approved=approved, summary=summary, project_id=args.project_id
        or summary.get("project_id"),
        approved_path=str(args.approved_candidates),
        summary_path=str(args.validation_summary),
        blocked_reason=blocked_reason, generated_at=generated_at,
    )
    paths = write_plan(args.output_dir, project_id, plan)

    print("# NEVO V2 apply PLAN (read-only — no database writes)")
    print(f"  project={plan['project_id'] or 'n/a'} "
          f"recommendation={plan['validation_recommendation']} "
          f"planned_operations={plan['planned_operation_count']}")
    print(f"  db_apply_status={plan['db_apply_status']}")
    print(f"  schema_migration_required={plan['schema_migration_required']} "
          f"overwrite_existing_v1={plan['overwrite_existing_v1']} "
          f"overwrite_manual={plan['overwrite_manual']}")
    print("-" * 64)
    print(f"  Plan JSON: {paths['plan_json']}")
    print(f"  Plan CSV:  {paths['plan_csv']}")
    print("DB APPLY IS BLOCKED until the documented schema migration is in "
          "place. READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase Quality-V2-W — explicit, heavily-guarded NEVO V2 apply CLI.

This is the ONLY code path that may persist V2-tagged NEVO enrichment records,
and it is designed to be impossible to trigger by accident:

  * default is DRY-RUN (writes nothing);
  * a real write also requires ``--confirm-apply-v2``;
  * it refuses unless the 0037 provenance columns actually exist in the DB;
  * it never overwrites a manual or a V1 enrichment, and never re-writes an
    existing V2 record (no overwrite flags exist yet, by design);
  * it validates the plan against the approved candidates and the validation
    recommendation before doing anything.

It is NOT imported by any app route; V1 stays the default matcher and embeddings
stay off.

    python -m altera_api.classification_v2.apply_nevo_v2_plan \
        --plan-json .../nevo_v2_apply_plan_<id>.json \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --project-id <uuid> --dry-run            # default; writes nothing
        # add --confirm-apply-v2 to actually write (only if columns exist)
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.domain.enrichment import (
    SOURCE_VERSION_V2_EMBEDDINGS,
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

_V2_MATCH_METHOD = "ai_assisted"  # a model helped pick; stays inside the enum
_CONSERVATIVE_CONFIDENCE = Decimal("0.9")
_NUTRIENT = "protein_pct"
_UNIT = "g_per_100g"

RESULT_CSV_COLUMNS = [
    "product_id", "product_name", "status", "candidate_source",
    "manual_decision", "effective_nevo_code", "effective_nevo_name",
    "effective_protein_g_per_100g", "detail",
]

ROW_STATUSES = (
    "would_write", "written", "skipped_existing_v1", "skipped_existing_manual",
    "skipped_existing_v2", "error",
)


class ApplyError(Exception):
    """Unreadable inputs / failed preconditions."""


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _read_json(path: str | Path, label: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ApplyError(f"{label} not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ApplyError(f"could not read {label} {p.name}: {exc}") from exc


def _read_csv(path: str | Path, label: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise ApplyError(f"{label} not found: {p}")
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Preconditions (Part A). Returns a list of failure messages (empty == OK).
# ---------------------------------------------------------------------------
def check_preconditions(
    *, plan: dict[str, Any], approved: list[dict[str, Any]], project_id: str,
    allow_incomplete_apply: bool, validation_summary: dict[str, Any] | None,
) -> list[str]:
    failures: list[str] = []

    if _s(plan.get("project_id")) != _s(project_id):
        failures.append(
            f"plan project_id {plan.get('project_id')!r} != --project-id "
            f"{project_id!r}"
        )
    if plan.get("schema_migration_required") is not True:
        failures.append("plan.schema_migration_required is not true")
    if _s(plan.get("db_apply_status")) != "blocked_pending_schema_migration":
        failures.append(
            f"unexpected plan.db_apply_status {plan.get('db_apply_status')!r}"
        )
    if plan.get("overwrite_existing_v1") or plan.get("overwrite_manual"):
        failures.append(
            "plan requests overwrite_existing_v1/overwrite_manual — not "
            "supported (no overwrite flags exist yet)"
        )
    if any(
        op.get("overwrite_existing_v1") or op.get("overwrite_manual")
        for op in plan.get("operations", [])
    ):
        failures.append("an operation requests overwrite — not supported")

    planned = plan.get("planned_operation_count")
    if planned is not None and planned != len(approved):
        failures.append(
            f"approved candidates ({len(approved)}) != planned operations "
            f"({planned})"
        )

    rec = _s(plan.get("validation_recommendation"))
    if rec == "ready_for_apply_planning":
        pass
    elif rec == "review_incomplete":
        if not allow_incomplete_apply:
            failures.append(
                "plan is review_incomplete — pass --allow-incomplete-apply to "
                "apply only the apply-ready rows"
            )
        elif not _s(plan.get("blocked_reason")):
            failures.append(
                "plan is review_incomplete but was not generated with "
                "--allow-incomplete (no blocked_reason recorded)"
            )
    else:
        failures.append(
            f"validation recommendation {rec!r} is not applicable"
        )

    if validation_summary is not None:
        errors = validation_summary.get("error_count")
        if isinstance(errors, int) and errors > 0:
            failures.append(
                f"validation summary reports {errors} error(s) — refusing"
            )
    return failures


def provenance_columns_present(store: Any) -> bool:
    checker = getattr(store, "has_enrichment_provenance_columns", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001
            return False
    rls = getattr(store, "_rls", None)
    if rls is None:
        return False
    try:
        rls.table("nutrition_enrichment_records").select(
            "source_version,source_metadata"
        ).limit(1).execute()
    except Exception:  # noqa: BLE001
        return False
    return True


# ---------------------------------------------------------------------------
# Per-product disposition + record construction (Part B).
# ---------------------------------------------------------------------------
def _disposition(existing: list[Any]) -> str:
    """``write`` | ``skip_manual`` | ``skip_v2`` | ``skip_v1`` from a product's
    existing protein enrichment records."""
    protein = [r for r in existing if getattr(r, "nutrient", None) == _NUTRIENT]
    manual = any(
        getattr(r, "match_method", None) == "manual"
        or getattr(r, "source", None) is NutritionEnrichmentSource.MANUAL_ALTERA
        for r in protein
    )
    if manual:
        return "skip_manual"
    if any(getattr(r, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS
           for r in protein):
        return "skip_v2"
    if any(
        getattr(r, "source_version", None) != SOURCE_VERSION_V2_EMBEDDINGS
        and getattr(r, "enriched_value", None) is not None
        and getattr(r, "match_method", None) != "manual"
        for r in protein
    ):
        return "skip_v1"
    return "write"


def build_record(
    row: dict[str, Any], *, metadata_base: dict[str, Any], now_iso: str,
) -> NutritionEnrichmentRecord:
    from datetime import datetime

    protein = Decimal(_s(row.get("effective_protein_g_per_100g")))
    metadata = {
        **metadata_base,
        "manual_decision": _s(row.get("manual_decision")),
        "candidate_source": _s(row.get("source")) or "existing",
        "approved_nevo_code": _s(row.get("effective_nevo_code")),
        "approved_nevo_name": _s(row.get("effective_nevo_name")),
        "applied_by_cli": True,
    }
    return NutritionEnrichmentRecord(
        product_id=UUID(_s(row.get("product_id"))),
        nutrient=_NUTRIENT,
        original_value=None,
        enriched_value=protein,
        unit=_UNIT,
        source=NutritionEnrichmentSource.NEVO,
        confidence=_CONSERVATIVE_CONFIDENCE,
        status=NutritionEnrichmentStatus.ENRICHED,
        rationale="NEVO V2 apply (approved review package)",
        created_at=datetime.fromisoformat(now_iso),
        created_by=None,
        match_method=_V2_MATCH_METHOD,
        source_version=SOURCE_VERSION_V2_EMBEDDINGS,
        source_metadata=metadata,
    )


_DISP_TO_SKIP_STATUS = {
    "skip_manual": "skipped_existing_manual",
    "skip_v1": "skipped_existing_v1",
    "skip_v2": "skipped_existing_v2",
}


def apply_plan(
    *, approved: list[dict[str, Any]], store: Any, metadata_base: dict[str, Any],
    write: bool, now_iso: str,
) -> list[dict[str, Any]]:
    """Evaluate every approved candidate; write only when ``write`` is True.
    Returns per-row result dicts."""
    results: list[dict[str, Any]] = []
    for row in approved:
        base = {
            "product_id": _s(row.get("product_id")),
            "product_name": _s(row.get("product_name")),
            "candidate_source": _s(row.get("source")) or "existing",
            "manual_decision": _s(row.get("manual_decision")),
            "effective_nevo_code": _s(row.get("effective_nevo_code")),
            "effective_nevo_name": _s(row.get("effective_nevo_name")),
            "effective_protein_g_per_100g": _s(
                row.get("effective_protein_g_per_100g")
            ),
            "detail": "",
        }
        pid_str = base["product_id"]
        try:
            pid = UUID(pid_str)
        except (ValueError, TypeError):
            results.append({**base, "status": "error",
                            "detail": f"invalid product_id {pid_str!r}"})
            continue

        protein_raw = base["effective_protein_g_per_100g"]
        try:
            Decimal(protein_raw)
        except (InvalidOperation, ValueError):
            results.append({**base, "status": "error",
                            "detail": "missing/invalid approved protein value"})
            continue

        existing = store.get_enrichment_records_for_product(pid)
        disp = _disposition(existing)
        if disp != "write":
            results.append({**base, "status": _DISP_TO_SKIP_STATUS[disp]})
            continue

        if not write:
            results.append({**base, "status": "would_write"})
            continue

        try:
            record = build_record(row, metadata_base=metadata_base, now_iso=now_iso)
            store.add_enrichment_record(record)
        except Exception as exc:  # noqa: BLE001 — report, never abort the run
            results.append({**base, "status": "error", "detail": str(exc)})
            continue
        results.append({**base, "status": "written"})
    return results


def summarize(
    results: list[dict[str, Any]], *, project_id: str | None, dry_run: bool,
    confirmation_present: bool, columns_present: bool, plan_json: str,
    approved_candidates: str, validation_summary: str | None,
    blocked_reason: str | None, generated_at: str | None,
) -> dict[str, Any]:
    def n(status: str) -> int:
        return sum(1 for r in results if r["status"] == status)

    return {
        "project_id": project_id,
        "generated_at": generated_at,
        "dry_run": dry_run,
        "confirmation_present": confirmation_present,
        "provenance_columns_present": columns_present,
        "plan_json": plan_json,
        "approved_candidates": approved_candidates,
        "validation_summary": validation_summary,
        "blocked_reason": blocked_reason,
        "total_planned": len(results),
        "would_write_count": n("would_write"),
        "written_count": n("written"),
        "skipped_existing_count": n("skipped_existing_v2"),
        "skipped_manual_count": n("skipped_existing_manual"),
        "skipped_v1_count": n("skipped_existing_v1"),
        "error_count": n("error"),
    }


def write_artifacts(
    out_dir: str | Path, project_id: str, summary: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_apply_result_{project_id}.json"
    csv_path = out / f"nevo_v2_apply_result_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_CSV_COLUMNS)
        w.writeheader()
        for r in results:
            w.writerow({c: r.get(c, "") for c in RESULT_CSV_COLUMNS})
    return {"result_json": str(json_path), "result_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.apply_nevo_v2_plan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--plan-json", required=True)
    ap.add_argument("--approved-candidates", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--validation-summary", default=None,
                    help="optional; defaults to the plan's source_validation_summary")
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="(default) evaluate and report; write nothing")
    ap.add_argument("--confirm-apply-v2", action="store_true",
                    help="REQUIRED for a real write (also needs migration 0037 "
                         "columns to exist)")
    ap.add_argument("--allow-incomplete-apply", action="store_true",
                    help="permit applying a review_incomplete plan that was "
                         "generated with --allow-incomplete")
    ap.add_argument("--matcher-version", default="v2-embeddings")
    ap.add_argument("--embedding-provider", default=None)
    ap.add_argument("--embedding-model", default=None)
    ap.add_argument("--top-k", type=int, default=None)
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    # Default is dry-run; the SINGLE switch that enables writing is the explicit
    # confirmation flag (and even then only if the migration columns exist).
    write = bool(args.confirm_apply_v2)

    try:
        plan = _read_json(args.plan_json, "apply plan")
        approved = _read_csv(args.approved_candidates, "approved candidates")
        summary_path = args.validation_summary or plan.get("source_validation_summary")
        validation_summary = None
        if summary_path and Path(summary_path).exists():
            validation_summary = _read_json(summary_path, "validation summary")
    except ApplyError as exc:
        print(f"FATAL: {exc}")
        return 2

    failures = check_preconditions(
        plan=plan, approved=approved, project_id=args.project_id,
        allow_incomplete_apply=args.allow_incomplete_apply,
        validation_summary=validation_summary,
    )
    if failures:
        print("FATAL: apply preconditions failed — nothing written:")
        for f in failures:
            print(f"  - {f}")
        return 2

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()

    columns_present = provenance_columns_present(store)
    if generated_at is None:
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC).isoformat()

    # A real write needs BOTH explicit confirmation AND the migration columns.
    effective_write = write and columns_present
    blocked_reason = None
    if write and not columns_present:
        blocked_reason = (
            "provenance columns missing (migration 0037 not applied) — wrote "
            "nothing"
        )

    metadata_base = {
        "matcher_version": args.matcher_version,
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model,
        "top_k": args.top_k,
        "apply_plan_path": str(args.plan_json),
        "approved_candidates_path": str(args.approved_candidates),
        "validation_summary_path": summary_path,
        "review_package_path": (
            validation_summary.get("input_path") if validation_summary else None
        ),
    }

    results = apply_plan(
        approved=approved, store=store, metadata_base=metadata_base,
        write=effective_write, now_iso=generated_at,
    )
    summary = summarize(
        results, project_id=plan.get("project_id"), dry_run=not effective_write,
        confirmation_present=bool(args.confirm_apply_v2),
        columns_present=columns_present, plan_json=str(args.plan_json),
        approved_candidates=str(args.approved_candidates),
        validation_summary=summary_path, blocked_reason=blocked_reason,
        generated_at=generated_at,
    )
    paths = write_artifacts(args.output_dir, args.project_id, summary, results)

    mode = "APPLY (writing)" if effective_write else "DRY-RUN (no writes)"
    print(f"# NEVO V2 apply — {mode}")
    print(f"  project={summary['project_id'] or 'n/a'} "
          f"planned={summary['total_planned']} "
          f"provenance_columns_present={columns_present}")
    print(f"  written={summary['written_count']} "
          f"would_write={summary['would_write_count']} "
          f"skipped_v1={summary['skipped_v1_count']} "
          f"skipped_manual={summary['skipped_manual_count']} "
          f"skipped_existing_v2={summary['skipped_existing_count']} "
          f"errors={summary['error_count']}")
    if blocked_reason:
        print(f"  BLOCKED: {blocked_reason}")
    print(f"  Result JSON: {paths['result_json']}")
    print(f"  Result CSV:  {paths['result_csv']}")
    if not effective_write:
        if not args.confirm_apply_v2:
            print("DRY-RUN — pass --confirm-apply-v2 to write (needs migration "
                  "0037 columns).")
        print("No database writes were made.")
    # Confirmed but blocked by missing columns is a failure to apply.
    return 2 if (write and not columns_present) else 0


if __name__ == "__main__":
    raise SystemExit(main())

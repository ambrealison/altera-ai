"""Phase Quality-V2-AB — guarded backfill of display metadata on V2 records.

The V2 apply stored a generic ``rationale`` ("NEVO V2 apply (approved review
package)") that the UI shows. The matched NEVO food name lives in
``source_metadata`` (``approved_nevo_name`` on totals; nothing on splits). This
CLI normalises ``source_metadata`` so the API/UI can show the food name:

  totals (source_version=v2_embeddings, nutrient=protein_pct):
      nevo_food_name, nevo_code, display_label = "NEVO V2: <name>"
  splits (source_version=v2_embeddings_split, nutrient in plant/animal):
      parent_nevo_food_name, parent_nevo_code, display_label = "NEVO V2 split: <name>"

It updates ONLY ``source_metadata`` — never nutrient/unit/enriched_value/
confidence/source/match_method/source_version/product_id — never a manual or V1
or non-NEVO record, and is idempotent. Default DRY-RUN; a write needs
``--confirm-backfill-display-metadata``. Not imported by any route.

    python -m altera_api.classification_v2.backfill_nevo_v2_display_metadata \
        --project-id <uuid> \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --output-dir /tmp/altera-quality
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
    _read_csv,
    _s,
)
from altera_api.classification_v2.nevo_v2_protein_split import (
    SPLIT_SOURCE_VERSION,
    is_manual,
)
from altera_api.domain.enrichment import (
    SOURCE_VERSION_V2_EMBEDDINGS,
    NutritionEnrichmentSource,
)

_SPLIT_NUTRIENTS = ("plant_protein_pct", "animal_protein_pct")

RESULT_CSV_COLUMNS = [
    "product_id", "nutrient", "source_version", "status",
    "nevo_food_name", "nevo_code", "display_label", "detail",
]


def _is_nevo(record: Any) -> bool:
    return getattr(record, "source", None) is NutritionEnrichmentSource.NEVO


def _name_code_by_product(
    approved: list[dict[str, Any]], records: list[Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Build product_id → (name, code), preferring the approved-candidates CSV,
    then any name already present on the product's total record metadata."""
    names: dict[str, str] = {}
    codes: dict[str, str] = {}
    for r in approved:
        pid = _s(r.get("product_id"))
        if _s(r.get("effective_nevo_name")):
            names.setdefault(pid, _s(r.get("effective_nevo_name")))
        if _s(r.get("effective_nevo_code")):
            codes.setdefault(pid, _s(r.get("effective_nevo_code")))
    for rec in records:
        if getattr(rec, "source_version", None) != SOURCE_VERSION_V2_EMBEDDINGS:
            continue
        md = getattr(rec, "source_metadata", None) or {}
        pid = _s(getattr(rec, "product_id", ""))
        for key in ("nevo_food_name", "approved_nevo_name"):
            if not names.get(pid) and md.get(key):
                names[pid] = _s(md.get(key))
        for key in ("nevo_code", "approved_nevo_code"):
            if not codes.get(pid) and md.get(key):
                codes[pid] = _s(md.get(key))
    return names, codes


def _desired_metadata(record: Any, *, name: str, code: str) -> dict[str, Any]:
    md = dict(getattr(record, "source_metadata", None) or {})
    if getattr(record, "source_version", None) == SPLIT_SOURCE_VERSION:
        md["parent_nevo_food_name"] = name
        md["parent_nevo_code"] = code
        md["display_label"] = f"NEVO V2 split: {name}"
    else:
        md["nevo_food_name"] = name
        md["nevo_code"] = code
        md["display_label"] = f"NEVO V2: {name}"
    return md


def plan_backfill(
    *, records: list[Any], approved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return per-record backfill ops (does not touch the DB)."""
    names, codes = _name_code_by_product(approved, records)
    ops: list[dict[str, Any]] = []
    for rec in records:
        sv = getattr(rec, "source_version", None)
        nutrient = getattr(rec, "nutrient", None)
        pid = _s(getattr(rec, "product_id", ""))
        is_total = sv == SOURCE_VERSION_V2_EMBEDDINGS and nutrient == "protein_pct"
        is_split = sv == SPLIT_SOURCE_VERSION and nutrient in _SPLIT_NUTRIENTS
        if not (is_total or is_split):
            # not a V2 display-bearing record.
            if is_manual(rec):
                ops.append({"product_id": pid, "nutrient": nutrient,
                            "source_version": sv, "status": "skipped_manual"})
            elif (getattr(rec, "enriched_value", None) is not None
                  and sv not in (SOURCE_VERSION_V2_EMBEDDINGS,
                                 SPLIT_SOURCE_VERSION)):
                ops.append({"product_id": pid, "nutrient": nutrient,
                            "source_version": sv, "status": "skipped_v1"})
            continue
        if not _is_nevo(rec):
            ops.append({"product_id": pid, "nutrient": nutrient,
                        "source_version": sv, "status": "skipped_non_nevo"})
            continue
        name = names.get(pid)
        code = codes.get(pid, "")
        if not name:
            ops.append({"product_id": pid, "nutrient": nutrient,
                        "source_version": sv, "status": "missing_approved_candidate"})
            continue
        desired = _desired_metadata(rec, name=name, code=code)
        current = dict(getattr(rec, "source_metadata", None) or {})
        status = "up_to_date" if desired == current else "needs_update"
        ops.append({
            "product_id": pid, "nutrient": nutrient, "source_version": sv,
            "status": status, "nevo_food_name": name, "nevo_code": code,
            "display_label": desired.get("display_label"),
            "_desired": desired,
        })
    return ops


def apply_backfill(ops: list[dict[str, Any]], *, store: Any, write: bool) -> None:
    for op in ops:
        if op["status"] != "needs_update":
            continue
        if not write:
            continue
        try:
            store.update_enrichment_source_metadata(
                product_id=UUID(op["product_id"]), nutrient=op["nutrient"],
                source_version=op["source_version"],
                source_metadata=op["_desired"],
            )
            op["status"] = "updated"
        except Exception as exc:  # noqa: BLE001
            op["status"] = "error"
            op["detail"] = str(exc)


def summarize(ops: list[dict[str, Any]], *, project_id: str, dry_run: bool,
              confirmation_present: bool, generated_at: str | None,
              ) -> dict[str, Any]:
    def n(*statuses: str) -> int:
        return sum(1 for o in ops if o["status"] in statuses)

    v2_seen = sum(1 for o in ops if o["status"] in (
        "needs_update", "up_to_date", "updated", "missing_approved_candidate",
        "skipped_non_nevo"))
    return {
        "project_id": project_id,
        "generated_at": generated_at,
        "dry_run": dry_run,
        "confirmation_present": confirmation_present,
        "total_v2_records_seen": v2_seen,
        "records_that_need_update": n("needs_update", "updated"),
        "records_updated": n("updated"),
        "records_up_to_date": n("up_to_date"),
        "skipped_manual": n("skipped_manual"),
        "skipped_v1": n("skipped_v1"),
        "skipped_non_nevo": n("skipped_non_nevo"),
        "missing_approved_candidate_count": n("missing_approved_candidate"),
        "error_count": n("error"),
    }


def write_artifacts(out_dir: str | Path, project_id: str,
                    summary: dict[str, Any], ops: list[dict[str, Any]],
                    ) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_display_metadata_backfill_{project_id}.json"
    csv_path = out / f"nevo_v2_display_metadata_backfill_{project_id}.csv"
    public_ops = [{k: v for k, v in o.items() if not k.startswith("_")}
                  for o in ops]
    json_path.write_text(
        json.dumps({**summary, "operations": public_ops}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_CSV_COLUMNS)
        w.writeheader()
        for o in public_ops:
            w.writerow({c: o.get(c, "") for c in RESULT_CSV_COLUMNS})
    return {"backfill_json": str(json_path), "backfill_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "backfill_nevo_v2_display_metadata",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--approved-candidates", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--confirm-backfill-display-metadata", action="store_true",
                    help="REQUIRED to actually write source_metadata.")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    write = bool(args.confirm_backfill_display_metadata)
    try:
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
    try:
        project_key: Any = UUID(str(args.project_id))
    except (ValueError, TypeError):
        project_key = args.project_id

    records = list(store.list_enrichment_records_for_project(project_key))
    ops = plan_backfill(records=records, approved=approved)
    apply_backfill(ops, store=store, write=write)
    summary = summarize(ops, project_id=_s(args.project_id), dry_run=not write,
                        confirmation_present=write, generated_at=generated_at)
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary, ops)

    mode = "WRITE" if write else "DRY-RUN (no writes)"
    print(f"# NEVO V2 display-metadata backfill — {mode}")
    print(f"  project={summary['project_id']} "
          f"v2_seen={summary['total_v2_records_seen']} "
          f"need_update={summary['records_that_need_update']} "
          f"updated={summary['records_updated']} "
          f"up_to_date={summary['records_up_to_date']}")
    print(f"  skipped_manual={summary['skipped_manual']} "
          f"skipped_v1={summary['skipped_v1']} "
          f"skipped_non_nevo={summary['skipped_non_nevo']} "
          f"missing_candidate={summary['missing_approved_candidate_count']} "
          f"errors={summary['error_count']}")
    print(f"  JSON: {paths['backfill_json']}")
    print(f"  CSV:  {paths['backfill_csv']}")
    if not write:
        print("DRY-RUN — pass --confirm-backfill-display-metadata to write. No "
              "database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

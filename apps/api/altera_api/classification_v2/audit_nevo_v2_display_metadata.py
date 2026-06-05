"""Phase Quality-V2-AB — read-only audit of V2 display metadata.

Verifies that every V2 enrichment record carries a clean, human-friendly
display label (and the matched NEVO name/code) rather than the generic apply
rationale, and that the backfill never touched a manual or V1 record.

Strictly read-only: reads the project's enrichment records and writes a report.
No DB writes; not imported by any route.

    python -m altera_api.classification_v2.audit_nevo_v2_display_metadata \
        --project-id <uuid> --output-dir /tmp/altera-quality
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.nevo_v2_protein_split import (
    SPLIT_SOURCE_VERSION,
    is_manual,
)
from altera_api.domain.enrichment import SOURCE_VERSION_V2_EMBEDDINGS

_SPLIT_NUTRIENTS = ("plant_protein_pct", "animal_protein_pct")
#: the generic apply rationales the display_label must NOT be.
_GENERIC_LABELS = frozenset({
    "NEVO V2 apply (approved review package)",
    "NEVO V2 plant/animal split (from PT classification)",
})

AUDIT_CSV_COLUMNS = [
    "product_id", "nutrient", "source_version", "display_label",
    "has_name", "status",
]
ANOMALY_CSV_COLUMNS = ["product_id", "nutrient", "anomaly", "detail"]


def audit_display(records: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]],
                                               list[dict[str, Any]]]:
    totals = [r for r in records
              if getattr(r, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS
              and getattr(r, "nutrient", None) == "protein_pct"]
    splits = [r for r in records
              if getattr(r, "source_version", None) == SPLIT_SOURCE_VERSION
              and getattr(r, "nutrient", None) in _SPLIT_NUTRIENTS]

    anomalies: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    def flag(pid, nutrient, anomaly, detail=""):
        anomalies.append({"product_id": pid, "nutrient": nutrient,
                          "anomaly": anomaly, "detail": detail})

    with_label = missing_label = generic = missing_name = 0
    for rec in totals + splits:
        pid = _s(getattr(rec, "product_id", ""))
        nutrient = _s(getattr(rec, "nutrient", ""))
        sv = _s(getattr(rec, "source_version", ""))
        md = getattr(rec, "source_metadata", None) or {}
        label = _s(md.get("display_label"))
        if sv == SPLIT_SOURCE_VERSION:
            name = _s(md.get("parent_nevo_food_name"))
        else:
            name = _s(md.get("nevo_food_name"))

        status = "ok"
        if not label:
            missing_label += 1
            status = "missing_display_label"
            flag(pid, nutrient, "missing_display_label")
        elif label in _GENERIC_LABELS:
            generic += 1
            status = "generic_label"
            flag(pid, nutrient, "generic_label", label)
        else:
            with_label += 1
        if not name:
            missing_name += 1
            if status == "ok":
                status = "missing_name"
            flag(pid, nutrient, "missing_name")
        rows.append({
            "product_id": pid, "nutrient": nutrient, "source_version": sv,
            "display_label": label, "has_name": bool(name), "status": status,
        })

    # A manual / V1 record must never carry V2 display metadata.
    manual_touched = 0
    for rec in records:
        md = getattr(rec, "source_metadata", None) or {}
        looks_v2_display = bool(md.get("display_label")) and (
            md.get("nevo_food_name") or md.get("parent_nevo_food_name"))
        if looks_v2_display and is_manual(rec):
            manual_touched += 1
            flag(_s(getattr(rec, "product_id", "")),
                 _s(getattr(rec, "nutrient", "")), "manual_touched",
                 "manual record carries V2 display metadata")

    fail = generic or manual_touched
    incomplete = missing_label or missing_name
    status = "fail" if fail else ("warn" if incomplete else "pass")
    recommendation = {"fail": "investigate_display_metadata",
                      "warn": "display_metadata_incomplete",
                      "pass": "display_metadata_verified"}[status]

    summary = {
        "total_v2_total_records": len(totals),
        "total_v2_split_records": len(splits),
        "with_display_label_count": with_label,
        "missing_display_label_count": missing_label,
        "generic_label_count": generic,
        "missing_name_count": missing_name,
        "manual_touched_count": manual_touched,
        "audit_status": status,
        "recommendation": recommendation,
    }
    return summary, rows, anomalies


def write_artifacts(out_dir, project_id, summary, rows, anomalies) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_display_metadata_audit_{project_id}.json"
    csv_path = out / f"nevo_v2_display_metadata_audit_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "anomalies": anomalies}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=AUDIT_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in AUDIT_CSV_COLUMNS})
    return {"audit_json": str(json_path), "audit_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "audit_nevo_v2_display_metadata",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
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
    summary, rows, anomalies = audit_display(records)
    summary = {"project_id": _s(args.project_id), "generated_at": generated_at,
               **summary}
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary, rows,
                            anomalies)

    print("# NEVO V2 display-metadata audit (READ-ONLY — no database writes)")
    print(f"  project={summary['project_id']} status={summary['audit_status']} "
          f"recommendation={summary['recommendation']}")
    print(f"  totals={summary['total_v2_total_records']} "
          f"splits={summary['total_v2_split_records']} "
          f"with_label={summary['with_display_label_count']} "
          f"missing_label={summary['missing_display_label_count']} "
          f"generic={summary['generic_label_count']} "
          f"missing_name={summary['missing_name_count']} "
          f"manual_touched={summary['manual_touched_count']}")
    print(f"  Audit JSON: {paths['audit_json']}")
    print(f"  Audit CSV:  {paths['audit_csv']}")
    print("READ-ONLY — no database writes were made.")
    return {"pass": 0, "warn": 1, "fail": 2}[summary["audit_status"]]


if __name__ == "__main__":
    raise SystemExit(main())

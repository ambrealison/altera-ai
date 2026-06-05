"""Phase Quality-V2-AA — read-only audit of the V2 plant/animal split apply.

Verifies the DB split state against the split proposals: every ``would_split``
product has exactly one ``plant_protein_pct`` + one ``animal_protein_pct``
record, ``plant + animal == total`` (±0.01), the records are correctly tagged
(source=nevo, match_method=ai_assisted, source_version=v2_embeddings_split,
unit=g_per_100g, metadata present), there are no duplicates, no
``needs_review``/skip product was split, and no manual/V1 split was clobbered.

Pet food is FOOD here: a pet product whose PT group is clear (e.g. animal_core)
splits normally and is NOT an anomaly. The audit is PT-group driven and never
looks at pet-ness.

Strictly read-only: reads the project's records + the proposals CSV and writes
report artifacts (incl. an app-check CSV). No DB writes; no route imports it.

    python -m altera_api.classification_v2.audit_nevo_v2_protein_split \
        --project-id <uuid> \
        --proposals .../nevo_v2_protein_split_proposals_<id>.csv \
        --output-dir /tmp/altera-quality
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
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
from altera_api.domain.enrichment import SOURCE_VERSION_V2_EMBEDDINGS

_SPLIT_NUTRIENTS = ("plant_protein_pct", "animal_protein_pct")
_SUM_TOLERANCE = Decimal("0.01")
_EXPECTED = {"source": "nevo", "match_method": "ai_assisted",
             "source_version": SPLIT_SOURCE_VERSION, "unit": "g_per_100g"}

AUDIT_CSV_COLUMNS = [
    "product_id", "product_name", "split_action", "pt_group",
    "total_protein", "plant_protein", "animal_protein", "plant_plus_animal",
    "status",
]
ANOMALY_CSV_COLUMNS = ["product_id", "product_name", "anomaly", "detail"]
APP_CHECK_CSV_COLUMNS = [
    "product_id", "product_name", "total_protein", "plant_protein",
    "animal_protein", "plant_plus_animal", "pt_group", "expected_ui_status",
]

_FAIL_ANOMALIES = frozenset({
    "unexpected_split", "duplicate_split", "sum_mismatch", "manual_conflict",
    "v1_conflict", "metadata_missing", "invalid_source",
    "invalid_match_method", "invalid_unit",
})


def _source_value(r: Any) -> str:
    src = getattr(r, "source", None)
    return _s(getattr(src, "value", src))


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(_s(value))
    except (InvalidOperation, ValueError):
        return None


def audit_split(
    *, records: list[Any], proposals: list[dict[str, Any]],
    names: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]]]:
    v2_protein = [r for r in records
                  if getattr(r, "source_version", None) == SOURCE_VERSION_V2_EMBEDDINGS
                  and getattr(r, "nutrient", None) == "protein_pct"]
    v2_split = [r for r in records
                if getattr(r, "source_version", None) == SPLIT_SOURCE_VERSION
                and getattr(r, "nutrient", None) in _SPLIT_NUTRIENTS]

    total_by_pid = {_s(getattr(r, "product_id", "")): getattr(r, "enriched_value", None)
                    for r in v2_protein}
    plant_by_pid: dict[str, list[Any]] = {}
    animal_by_pid: dict[str, list[Any]] = {}
    for r in v2_split:
        pid = _s(getattr(r, "product_id", ""))
        if getattr(r, "nutrient", None) == "plant_protein_pct":
            plant_by_pid.setdefault(pid, []).append(r)
        else:
            animal_by_pid.setdefault(pid, []).append(r)
    all_by_pid: dict[str, list[Any]] = {}
    for r in records:
        all_by_pid.setdefault(_s(getattr(r, "product_id", "")), []).append(r)

    name_of = {**{_s(p.get("product_id")): _s(p.get("product_name"))
                  for p in proposals}, **names}

    anomalies: list[dict[str, Any]] = []

    def flag(pid: str, anomaly: str, detail: str = "") -> None:
        anomalies.append({"product_id": pid, "product_name": name_of.get(pid, ""),
                          "anomaly": anomaly, "detail": detail})

    matched = missing = unexpected = sum_mismatch = 0
    audit_rows: list[dict[str, Any]] = []
    for p in proposals:
        pid = _s(p.get("product_id"))
        action = _s(p.get("split_action"))
        plants = plant_by_pid.get(pid, [])
        animals = animal_by_pid.get(pid, [])
        plant_val = getattr(plants[0], "enriched_value", None) if plants else None
        animal_val = getattr(animals[0], "enriched_value", None) if animals else None
        total = total_by_pid.get(pid) or _dec(p.get("total_protein_g_per_100g"))

        status = "ok"
        if action == "would_split":
            if not plants or not animals:
                missing += 1
                status = "missing_split"
                flag(pid, "missing_split",
                     f"plant={len(plants)} animal={len(animals)}")
            else:
                matched += 1
                if total is not None and plant_val is not None and animal_val is not None:
                    if abs((plant_val + animal_val) - total) > _SUM_TOLERANCE:
                        sum_mismatch += 1
                        status = "sum_mismatch"
                        flag(pid, "sum_mismatch",
                             f"{plant_val}+{animal_val} != {total}")
        elif plants or animals:
            unexpected += 1
            status = "unexpected_split"
            flag(pid, "unexpected_split",
                 f"action={action} has {len(plants) + len(animals)} split record(s)")

        audit_rows.append({
            "product_id": pid, "product_name": name_of.get(pid, ""),
            "split_action": action, "pt_group": _s(p.get("pt_group")),
            "total_protein": _s(total), "plant_protein": _s(plant_val),
            "animal_protein": _s(animal_val),
            "plant_plus_animal": _s(plant_val + animal_val)
            if (plant_val is not None and animal_val is not None) else "",
            "status": status,
        })

    # Duplicates per (product, nutrient).
    dup = Counter((_s(getattr(r, "product_id", "")), getattr(r, "nutrient", ""))
                  for r in v2_split)
    duplicate_products = {pid for (pid, _n), c in dup.items() if c > 1}
    for pid in sorted(duplicate_products):
        flag(pid, "duplicate_split", "more than one split record for a nutrient")

    # Tag validation on every V2 split record.
    invalid = Counter()
    for r in v2_split:
        pid = _s(getattr(r, "product_id", ""))
        if _source_value(r) != _EXPECTED["source"]:
            invalid["invalid_source"] += 1
            flag(pid, "invalid_source", _source_value(r))
        if _s(getattr(r, "match_method", "")) != _EXPECTED["match_method"]:
            invalid["invalid_match_method"] += 1
            flag(pid, "invalid_match_method", _s(getattr(r, "match_method", "")))
        if _s(getattr(r, "unit", "")) != _EXPECTED["unit"]:
            invalid["invalid_unit"] += 1
            flag(pid, "invalid_unit", _s(getattr(r, "unit", "")))
        if not getattr(r, "source_metadata", None):
            invalid["metadata_missing"] += 1
            flag(pid, "metadata_missing")

    # Manual / V1 coexistence on a product that has a V2 split.
    split_pids = {_s(getattr(r, "product_id", "")) for r in v2_split}
    manual_conflict = v1_conflict = 0
    for pid in split_pids:
        recs = all_by_pid.get(pid, [])
        if any(is_manual(x) for x in recs):
            manual_conflict += 1
            flag(pid, "manual_conflict", "manual record coexists with V2 split")
        if any(getattr(x, "nutrient", None) in _SPLIT_NUTRIENTS
               and getattr(x, "source_version", None) not in
               (SPLIT_SOURCE_VERSION, None)
               and not is_manual(x)
               and getattr(x, "enriched_value", None) is not None
               for x in recs):
            v1_conflict += 1
            flag(pid, "v1_conflict", "non-V2 split record coexists with V2 split")

    would_split = sum(1 for p in proposals
                      if _s(p.get("split_action")) == "would_split")
    needs_review = sum(1 for p in proposals
                       if _s(p.get("split_action")) == "needs_review")

    fail = (unexpected or len(duplicate_products) or sum_mismatch
            or manual_conflict or v1_conflict
            or sum(invalid.values()))
    status = "fail" if fail else ("warn" if missing else "pass")
    recommendation = {"fail": "rollback_split_recommended",
                      "warn": "investigate_split_anomalies",
                      "pass": "split_apply_verified"}[status]

    summary = {
        "total_v2_protein_count": len(v2_protein),
        "proposal_would_split_count": would_split,
        "proposal_needs_review_count": needs_review,
        "applied_split_product_count": len(split_pids),
        "plant_split_record_count": len(v2_split) - sum(
            1 for r in v2_split if getattr(r, "nutrient", None) == "animal_protein_pct"),
        "animal_split_record_count": sum(
            1 for r in v2_split if getattr(r, "nutrient", None) == "animal_protein_pct"),
        "matched_would_split_count": matched,
        "missing_split_count": missing,
        "unexpected_split_count": unexpected,
        "duplicate_split_count": len(duplicate_products),
        "sum_mismatch_count": sum_mismatch,
        "manual_conflict_count": manual_conflict,
        "v1_conflict_count": v1_conflict,
        "metadata_missing_count": invalid["metadata_missing"],
        "invalid_source_count": invalid["invalid_source"],
        "invalid_match_method_count": invalid["invalid_match_method"],
        "invalid_unit_count": invalid["invalid_unit"],
        "audit_status": status,
        "recommendation": recommendation,
    }
    app_check = _app_check_rows(proposals, audit_rows)
    return summary, audit_rows, anomalies, app_check


def _app_check_rows(proposals: list[dict[str, Any]],
                    audit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    action_by_pid = {_s(p.get("product_id")): _s(p.get("split_action"))
                     for p in proposals}
    rows: list[dict[str, Any]] = []
    for a in audit_rows:
        has_split = bool(a["plant_protein"]) and bool(a["animal_protein"])
        action = action_by_pid.get(a["product_id"], "")
        if has_split:
            ui = "split_shown"
        elif action == "needs_review":
            ui = "total_only_needs_review"
        else:
            ui = "total_only"
        rows.append({
            "product_id": a["product_id"], "product_name": a["product_name"],
            "total_protein": a["total_protein"], "plant_protein": a["plant_protein"],
            "animal_protein": a["animal_protein"],
            "plant_plus_animal": a["plant_plus_animal"],
            "pt_group": a["pt_group"], "expected_ui_status": ui,
        })
    return rows


def _load(store: Any, project_key: Any) -> tuple[list[Any], dict[str, str]]:
    records = list(store.list_enrichment_records_for_project(project_key))
    names: dict[str, str] = {}
    lister = getattr(store, "list_products_for_project", None)
    if callable(lister):
        try:
            for p in lister(project_key):
                names[_s(getattr(p, "id", ""))] = _s(getattr(p, "product_name", ""))
        except Exception:  # noqa: BLE001
            names = {}
    return records, names


def write_artifacts(out_dir: str | Path, project_id: str, summary: dict[str, Any],
                    audit_rows, anomalies, app_check) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_split_audit_{project_id}.json"
    json_path.write_text(
        json.dumps({**summary, "anomalies": anomalies}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    paths = {"audit_json": str(json_path)}
    for name, cols, rows, key in (
        (f"nevo_v2_split_audit_{project_id}.csv", AUDIT_CSV_COLUMNS, audit_rows,
         "audit_csv"),
        (f"nevo_v2_split_audit_anomalies_{project_id}.csv", ANOMALY_CSV_COLUMNS,
         anomalies, "anomalies_csv"),
        (f"nevo_v2_split_app_check_{project_id}.csv", APP_CHECK_CSV_COLUMNS,
         app_check, "app_check_csv"),
    ):
        path = out / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
        paths[key] = str(path)
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "audit_nevo_v2_protein_split",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--proposals", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        proposals = _read_csv(args.proposals, "split proposals")
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

    records, names = _load(store, project_key)
    summary, audit_rows, anomalies, app_check = audit_split(
        records=records, proposals=proposals, names=names)
    summary = {"project_id": _s(args.project_id), "generated_at": generated_at,
               **summary}
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary,
                            audit_rows, anomalies, app_check)

    print("# NEVO V2 split audit (READ-ONLY — no database writes)")
    print(f"  project={summary['project_id']} status={summary['audit_status']} "
          f"recommendation={summary['recommendation']}")
    print(f"  v2_protein={summary['total_v2_protein_count']} "
          f"would_split={summary['proposal_would_split_count']} "
          f"needs_review={summary['proposal_needs_review_count']} "
          f"applied_products={summary['applied_split_product_count']} "
          f"plant={summary['plant_split_record_count']} "
          f"animal={summary['animal_split_record_count']}")
    print(f"  matched={summary['matched_would_split_count']} "
          f"missing={summary['missing_split_count']} "
          f"unexpected={summary['unexpected_split_count']} "
          f"duplicate={summary['duplicate_split_count']} "
          f"sum_mismatch={summary['sum_mismatch_count']} "
          f"manual_conflict={summary['manual_conflict_count']} "
          f"v1_conflict={summary['v1_conflict_count']}")
    for label, key in (("Audit JSON", "audit_json"), ("Audit CSV", "audit_csv"),
                       ("Anomalies CSV", "anomalies_csv"),
                       ("App-check CSV", "app_check_csv")):
        print(f"  {label}: {paths[key]}")
    print("READ-ONLY — no database writes were made.")
    return {"pass": 0, "warn": 1, "fail": 2}[summary["audit_status"]]


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase Quality-V2-Z — dry-run plant/animal split PROPOSALS (no DB writes).

Reads the V2 total-protein records and each product's Protein Tracker
classification, and proposes a plant/animal split per the policy in
``nevo_v2_protein_split``. Writes CSV + JSON only — never the DB, never a route.

    python -m altera_api.classification_v2.propose_nevo_v2_protein_split \
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
    SPLIT_ACTIONS,
    has_existing_split,
    is_manual,
    is_v2_total_protein,
    split_proposal,
)

PROPOSAL_CSV_COLUMNS = [
    "product_id", "product_name", "total_protein_g_per_100g", "pt_group",
    "proposed_plant_protein_g_per_100g", "proposed_animal_protein_g_per_100g",
    "split_action", "split_reason",
]


def _num(value: Any) -> str:
    return "" if value is None else str(value)


def build_proposals(
    *, records: list[Any], classifications: dict[str, Any],
    names: dict[str, str],
) -> list[dict[str, Any]]:
    by_pid: dict[str, list[Any]] = {}
    for r in records:
        by_pid.setdefault(_s(getattr(r, "product_id", "")), []).append(r)

    rows: list[dict[str, Any]] = []
    for pid, recs in by_pid.items():
        v2_totals = [r for r in recs if is_v2_total_protein(r)]
        if not v2_totals:
            continue
        total = getattr(v2_totals[0], "enriched_value", None)
        clf = classifications.get(pid)
        pt_group = getattr(clf, "pt_group", None) if clf is not None else None
        manual = any(is_manual(r) for r in recs)
        existing_split = has_existing_split(recs)

        proposal = split_proposal(
            pt_group=pt_group, total_protein=total,
            has_manual_override=manual, has_classification=clf is not None,
        )
        # A would_split that already has a split is informational, not a new
        # proposal — flag it as needs_review so apply won't duplicate.
        if proposal["action"] == "would_split" and existing_split:
            proposal = {"action": "needs_review", "plant": None, "animal": None,
                        "reason": "product already has a plant/animal split"}

        rows.append({
            "product_id": pid,
            "product_name": names.get(pid, ""),
            "total_protein_g_per_100g": _num(total),
            "pt_group": _s(getattr(pt_group, "value", pt_group)),
            "proposed_plant_protein_g_per_100g": _num(proposal["plant"]),
            "proposed_animal_protein_g_per_100g": _num(proposal["animal"]),
            "split_action": proposal["action"],
            "split_reason": proposal["reason"],
        })
    rows.sort(key=lambda r: r["product_id"])
    return rows


def build_summary(rows: list[dict[str, Any]], *, project_id: str,
                  generated_at: str | None) -> dict[str, Any]:
    counts = {a: sum(1 for r in rows if r["split_action"] == a)
              for a in SPLIT_ACTIONS}
    return {
        "project_id": project_id,
        "generated_at": generated_at,
        "total_v2_protein_records": len(rows),
        "split_action_counts": counts,
        "would_split_count": counts["would_split"],
    }


def _load_state(store: Any, project_key: Any):
    records = list(store.list_enrichment_records_for_project(project_key))
    names: dict[str, str] = {}
    lister = getattr(store, "list_products_for_project", None)
    if callable(lister):
        try:
            for p in lister(project_key):
                names[_s(getattr(p, "id", ""))] = _s(
                    getattr(p, "product_name", ""))
        except Exception:  # noqa: BLE001 — names best-effort
            names = {}
    classifications: dict[str, Any] = {}
    getter = getattr(store, "get_pt_classification", None)
    for pid in {_s(getattr(r, "product_id", "")) for r in records}:
        if callable(getter):
            try:
                clf = getter(UUID(pid))
            except (ValueError, TypeError):
                clf = getter(pid)
            except Exception:  # noqa: BLE001
                clf = None
            if clf is not None:
                classifications[pid] = clf
    return records, classifications, names


def write_artifacts(out_dir: str | Path, project_id: str,
                    summary: dict[str, Any],
                    rows: list[dict[str, Any]]) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_protein_split_proposals_{project_id}.json"
    csv_path = out / f"nevo_v2_protein_split_proposals_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "proposals": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PROPOSAL_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in PROPOSAL_CSV_COLUMNS})
    return {"proposals_json": str(json_path), "proposals_csv": str(csv_path)}


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "propose_nevo_v2_protein_split",
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

    records, classifications, names = _load_state(store, project_key)
    rows = build_proposals(records=records, classifications=classifications,
                           names=names)
    summary = build_summary(rows, project_id=_s(args.project_id),
                            generated_at=generated_at)
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary, rows)

    c = summary["split_action_counts"]
    print("# NEVO V2 plant/animal split PROPOSALS (DRY-RUN — no DB writes)")
    print(f"  project={summary['project_id']} "
          f"v2_protein_records={summary['total_v2_protein_records']}")
    print(f"  would_split={c['would_split']} needs_review={c['needs_review']} "
          f"skip_missing_class={c['skip_missing_class']} "
          f"skip_manual_override={c['skip_manual_override']}")
    print(f"  Proposals JSON: {paths['proposals_json']}")
    print(f"  Proposals CSV:  {paths['proposals_csv']}")
    print("DRY-RUN — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

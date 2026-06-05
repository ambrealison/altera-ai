"""Phase Quality-V2-Z — guarded apply of V2 plant/animal split records.

The split is representable in the EXISTING schema: it is surfaced to the
calculation as sibling ENRICHED enrichment records (``nutrient='plant_protein_pct'``
+ ``nutrient='animal_protein_pct'``, same ``source=nevo`` as the V2 total). So no
migration is needed beyond 0037 (which the records' ``source_version`` reuses).

Same safety posture as ``apply_nevo_v2_plan``: dry-run by default; a real write
needs BOTH ``--confirm-apply-split`` AND the 0037 provenance columns; it never
overwrites a manual record and never re-writes an existing split. Not imported
by any route; V1 stays default.

    python -m altera_api.classification_v2.apply_nevo_v2_protein_split \
        --proposals .../nevo_v2_protein_split_proposals_<id>.csv \
        --project-id <uuid>                       # default DRY-RUN
        # add --confirm-apply-split to write (only if columns exist)
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2.apply_nevo_v2_plan import (
    ApplyError,
    _read_csv,
    _s,
    provenance_columns_present,
)
from altera_api.classification_v2.nevo_v2_protein_split import (
    SPLIT_SOURCE_VERSION,
    has_existing_split,
    is_manual,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

_MATCH_METHOD = "ai_assisted"
_CONSERVATIVE_CONFIDENCE = Decimal("0.9")
_UNIT = "g_per_100g"

RESULT_CSV_COLUMNS = [
    "product_id", "product_name", "status", "pt_group",
    "plant_protein_g_per_100g", "animal_protein_g_per_100g", "detail",
]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _split_records(
    *, product_id: UUID, plant: Decimal, animal: Decimal,
    metadata: dict[str, Any], now_iso: str,
) -> list[NutritionEnrichmentRecord]:
    from datetime import datetime

    created = datetime.fromisoformat(now_iso)
    out = []
    for nutrient, value in (("plant_protein_pct", plant),
                            ("animal_protein_pct", animal)):
        out.append(NutritionEnrichmentRecord(
            product_id=product_id, nutrient=nutrient, original_value=None,
            enriched_value=value, unit=_UNIT,
            source=NutritionEnrichmentSource.NEVO,
            confidence=_CONSERVATIVE_CONFIDENCE,
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="NEVO V2 plant/animal split (from PT classification)",
            created_at=created, created_by=None, match_method=_MATCH_METHOD,
            source_version=SPLIT_SOURCE_VERSION,
            source_metadata={**metadata, "nutrient": nutrient},
        ))
    return out


def apply_splits(
    *, proposals: list[dict[str, Any]], store: Any, write: bool,
    metadata_base: dict[str, Any], now_iso: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in proposals:
        if _s(row.get("split_action")) != "would_split":
            continue
        base = {
            "product_id": _s(row.get("product_id")),
            "product_name": _s(row.get("product_name")),
            "pt_group": _s(row.get("pt_group")),
            "plant_protein_g_per_100g": _s(
                row.get("proposed_plant_protein_g_per_100g")),
            "animal_protein_g_per_100g": _s(
                row.get("proposed_animal_protein_g_per_100g")),
            "detail": "",
        }
        try:
            pid = UUID(base["product_id"])
        except (ValueError, TypeError):
            results.append({**base, "status": "error",
                            "detail": "invalid product_id"})
            continue
        plant = _decimal(base["plant_protein_g_per_100g"])
        animal = _decimal(base["animal_protein_g_per_100g"])
        if plant is None or animal is None:
            results.append({**base, "status": "error",
                            "detail": "non-numeric split value"})
            continue

        existing = store.get_enrichment_records_for_product(pid)
        if any(is_manual(r) for r in existing):
            results.append({**base, "status": "skipped_manual"})
            continue
        if has_existing_split(existing):
            results.append({**base, "status": "skipped_existing_split"})
            continue
        if not write:
            results.append({**base, "status": "would_write"})
            continue
        try:
            metadata = {**metadata_base, "pt_group": base["pt_group"],
                        "total_protein_g_per_100g": _s(
                            row.get("total_protein_g_per_100g"))}
            for record in _split_records(product_id=pid, plant=plant,
                                         animal=animal, metadata=metadata,
                                         now_iso=now_iso):
                store.add_enrichment_record(record)
        except Exception as exc:  # noqa: BLE001
            results.append({**base, "status": "error", "detail": str(exc)})
            continue
        results.append({**base, "status": "written"})
    return results


def summarize(results: list[dict[str, Any]], *, project_id: str,
              dry_run: bool, confirmation_present: bool, columns_present: bool,
              proposals_path: str, blocked_reason: str | None,
              generated_at: str | None) -> dict[str, Any]:
    def n(status: str) -> int:
        return sum(1 for r in results if r["status"] == status)

    return {
        "project_id": project_id,
        "generated_at": generated_at,
        "dry_run": dry_run,
        "confirmation_present": confirmation_present,
        "provenance_columns_present": columns_present,
        "proposals": proposals_path,
        "blocked_reason": blocked_reason,
        "total_would_split": len(results),
        "would_write_count": n("would_write"),
        "written_pairs_count": n("written"),
        "records_written_count": n("written") * 2,
        "skipped_existing_split_count": n("skipped_existing_split"),
        "skipped_manual_count": n("skipped_manual"),
        "error_count": n("error"),
    }


def write_artifacts(out_dir: str | Path, project_id: str,
                    summary: dict[str, Any],
                    results: list[dict[str, Any]]) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"nevo_v2_split_apply_result_{project_id}.json"
    csv_path = out / f"nevo_v2_split_apply_result_{project_id}.csv"
    json_path.write_text(
        json.dumps({**summary, "results": results}, indent=2,
                   ensure_ascii=False),
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
        prog="python -m altera_api.classification_v2."
             "apply_nevo_v2_protein_split",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--proposals", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--confirm-apply-split", action="store_true",
                    help="REQUIRED for a real write (also needs migration 0037 "
                         "columns).")
    ap.add_argument("--limit-apply", type=int, default=None)
    return ap


def main(argv: list[str] | None = None, *, store: Any = None,
         generated_at: str | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    write = bool(args.confirm_apply_split)
    try:
        proposals = _read_csv(args.proposals, "split proposals")
    except ApplyError as exc:
        print(f"FATAL: {exc}")
        return 2

    would = [r for r in proposals if _s(r.get("split_action")) == "would_split"]
    if args.limit_apply is not None:
        keep = {id(r) for r in would[: args.limit_apply]}
        proposals = [r for r in proposals
                     if _s(r.get("split_action")) != "would_split"
                     or id(r) in keep]

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()
    if generated_at is None:
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC).isoformat()

    columns_present = provenance_columns_present(store)
    effective_write = write and columns_present
    blocked_reason = None
    if write and not columns_present:
        blocked_reason = ("provenance columns missing (migration 0037 not "
                          "applied) — wrote nothing")

    metadata_base = {"derived_from": "v2_embeddings",
                     "split_basis": "pt_classification_group",
                     "split_apply_path": True, "applied_by_cli": True}
    results = apply_splits(proposals=proposals, store=store,
                           write=effective_write, metadata_base=metadata_base,
                           now_iso=generated_at)
    summary = summarize(
        results, project_id=_s(args.project_id), dry_run=not effective_write,
        confirmation_present=bool(args.confirm_apply_split),
        columns_present=columns_present, proposals_path=str(args.proposals),
        blocked_reason=blocked_reason, generated_at=generated_at,
    )
    paths = write_artifacts(args.output_dir, _s(args.project_id), summary,
                            results)

    mode = "APPLY (writing)" if effective_write else "DRY-RUN (no writes)"
    print(f"# NEVO V2 plant/animal split apply — {mode}")
    print(f"  project={summary['project_id']} "
          f"would_split={summary['total_would_split']} "
          f"provenance_columns_present={columns_present}")
    print(f"  written_pairs={summary['written_pairs_count']} "
          f"records_written={summary['records_written_count']} "
          f"would_write={summary['would_write_count']} "
          f"skipped_existing_split={summary['skipped_existing_split_count']} "
          f"skipped_manual={summary['skipped_manual_count']} "
          f"errors={summary['error_count']}")
    if blocked_reason:
        print(f"  BLOCKED: {blocked_reason}")
    print(f"  Result JSON: {paths['result_json']}")
    print(f"  Result CSV:  {paths['result_csv']}")
    if not effective_write:
        if not args.confirm_apply_split:
            print("DRY-RUN — pass --confirm-apply-split to write (needs "
                  "migration 0037 columns).")
        print("No database writes were made.")
    return 2 if (write and not columns_present) else 0


if __name__ == "__main__":
    raise SystemExit(main())

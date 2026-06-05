"""Phase Quality-V2-AF — validate a FILLED batch review package (read-only).

Validates reviewer decisions on the consolidated batch review package and emits
the correction-loop outputs: errors / warnings, approved candidates (resolved to
an effective NEVO source), gold-dataset candidates, and alias/rule candidates.

It does NOT create an apply plan — that is a later phase. Strictly read-only: it
reads one CSV and writes report artifacts. No DB writes, no routes.

    python -m altera_api.classification_v2.validate_nevo_v2_batch_review_package \
        --input .../nevo_v2_batch_review_package_<project>_<run>.csv \
        --output-dir /tmp/altera-quality --project-id <uuid>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import (
    ApplyError,
    _read_csv,
    _s,
)

_APPROVE_DECISIONS = frozenset({
    "approve_existing_candidate", "approve_existing_v2", "replace",
})
ALLOWED_DECISIONS = _APPROVE_DECISIONS | {"reject", "needs_more_info",
                                          "out_of_scope", ""}

ISSUE_CSV_COLUMNS = ["product_id", "product_name", "review_source",
                     "manual_decision", "review_priority", "message"]
APPROVED_CSV_COLUMNS = [
    "product_id", "product_name", "review_source", "manual_decision", "source",
    "effective_nevo_code", "effective_nevo_name", "effective_protein_g_per_100g",
    "reviewer_notes",
]
ALIAS_RULE_CSV_COLUMNS = [
    "product_name", "alias_candidate", "rule_candidate", "approved_nevo_code",
    "approved_nevo_name", "reviewer_notes", "status",
]


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _batch_cn(row: dict[str, Any]) -> tuple[str, str]:
    return (_s(row.get("batch_nevo_code")) or _s(row.get("nevo_code")),
            _s(row.get("batch_nevo_name")) or _s(row.get("nevo_food_name")))


def validate_row(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    decision = _s(row.get("manual_decision")).lower()
    if decision not in ALLOWED_DECISIONS:
        errors.append(
            f"invalid manual_decision {decision!r} (allowed: "
            "approve_existing_candidate, approve_existing_v2, replace, reject, "
            "needs_more_info, out_of_scope, blank)")
        return errors, warnings

    notes_u = _s(row.get("reviewer_notes")).upper()
    has_notes = bool(_s(row.get("reviewer_notes")))
    priority = _s(row.get("review_priority")).upper()
    source = _s(row.get("review_source"))
    action = _s(row.get("safety_action"))
    diff_bucket = _s(row.get("diff_bucket"))
    appr_code = _s(row.get("approved_nevo_code"))
    appr_name = _s(row.get("approved_nevo_name"))
    appr_prot = _s(row.get("approved_protein_g_per_100g"))
    batch_code, batch_name = _batch_cn(row)
    ex_code = _s(row.get("existing_v2_nevo_code"))
    ex_name = _s(row.get("existing_v2_nevo_name"))

    if appr_prot and not _is_number(appr_prot):
        errors.append(f"approved_protein_g_per_100g must be numeric, got "
                      f"{appr_prot!r}")

    is_approve = decision in _APPROVE_DECISIONS
    if is_approve and priority == "P0" and "OVERRIDE" not in notes_u:
        errors.append("P0 row cannot be approved without an OVERRIDE marker in "
                      "reviewer_notes")

    if decision == "replace":
        if not appr_code or not appr_name:
            errors.append("replace requires approved_nevo_code and "
                          "approved_nevo_name")
    elif decision == "approve_existing_candidate":
        if not batch_code or not batch_name:
            errors.append("approve_existing_candidate requires the batch "
                          "nevo_code and nevo_food_name")
        if source == "safety_downgrade":
            if action == "skip_state_mismatch" and "OVERRIDE_SAFE_STATE" not in notes_u:
                warnings.append("approved a state-mismatch downgrade as-is "
                                "(add OVERRIDE_SAFE_STATE to confirm)")
            elif action == "skip_proxy_too_broad" and "OVERRIDE_SAFE_PROXY" not in notes_u:
                warnings.append("approved a proxy-too-broad downgrade as-is "
                                "(add OVERRIDE_SAFE_PROXY to confirm)")
            elif action not in ("skip_state_mismatch", "skip_proxy_too_broad"):
                warnings.append(f"approved a safety-downgrade ({action}) as-is "
                                "— confirm before enriching")
        if source == "existing_v2_diff" and diff_bucket == "safety_downgraded_current_batch":
            warnings.append("approving the current batch over the existing V2, "
                            "but the batch result is itself safety-downgraded")
    elif decision == "approve_existing_v2":
        if not ex_code or not ex_name:
            errors.append("approve_existing_v2 requires existing_v2_nevo_code "
                          "and existing_v2_nevo_name")
    elif decision in ("reject", "out_of_scope") and appr_code and not has_notes:
        warnings.append(f"{decision} includes an approved_nevo_code but no "
                        "reviewer_notes explaining why")
    return errors, warnings


def _effective(row: dict[str, Any], decision: str) -> dict[str, str]:
    appr_prot = _s(row.get("approved_protein_g_per_100g"))
    batch_code, batch_name = _batch_cn(row)
    if decision == "approve_existing_candidate":
        return {"source": "batch_candidate", "effective_nevo_code": batch_code,
                "effective_nevo_name": batch_name,
                "effective_protein_g_per_100g": appr_prot
                or _s(row.get("protein_g_per_100g"))}
    if decision == "approve_existing_v2":
        return {"source": "existing_v2",
                "effective_nevo_code": _s(row.get("existing_v2_nevo_code")),
                "effective_nevo_name": _s(row.get("existing_v2_nevo_name")),
                "effective_protein_g_per_100g": appr_prot}
    return {"source": "replacement",
            "effective_nevo_code": _s(row.get("approved_nevo_code")),
            "effective_nevo_name": _s(row.get("approved_nevo_name")),
            "effective_protein_g_per_100g": appr_prot}


def validate_package(rows: list[dict[str, Any]], *, project_id: str | None,
                     run_id: str | None, input_path: str) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    gold: list[dict[str, Any]] = []
    alias_rule: list[dict[str, Any]] = []
    decision_counts: dict[str, int] = {}
    pending = 0

    for row in rows:
        decision = _s(row.get("manual_decision")).lower()
        decision_counts[decision or "pending"] = decision_counts.get(
            decision or "pending", 0) + 1
        if not decision:
            pending += 1
        errs, warns = validate_row(row)
        meta = {
            "product_id": _s(row.get("product_id")),
            "product_name": _s(row.get("product_name")),
            "review_source": _s(row.get("review_source")),
            "manual_decision": decision,
            "review_priority": _s(row.get("review_priority")),
        }
        errors.extend({**meta, "message": m} for m in errs)
        warnings.extend({**meta, "message": m} for m in warns)

        if not errs and decision in _APPROVE_DECISIONS:
            approved.append({
                "product_id": meta["product_id"],
                "product_name": meta["product_name"],
                "review_source": meta["review_source"],
                "manual_decision": decision, **_effective(row, decision),
                "reviewer_notes": _s(row.get("reviewer_notes")),
            })

        if decision and not errs:
            action = _s(row.get("safety_action"))
            should_auto = (decision in _APPROVE_DECISIONS
                           and action == "would_enrich")
            should_review = (decision == "needs_more_info"
                             or (decision in _APPROVE_DECISIONS
                                 and action != "would_enrich"))
            batch_code, batch_name = _batch_cn(row)
            gold.append({
                "product_name": meta["product_name"],
                "product_context": " | ".join(
                    x for x in (_s(row.get("brand")), _s(row.get("category")),
                                _s(row.get("ingredients"))) if x),
                "batch_nevo_code": batch_code, "batch_nevo_name": batch_name,
                "existing_v2_nevo_code": _s(row.get("existing_v2_nevo_code")),
                "existing_v2_nevo_name": _s(row.get("existing_v2_nevo_name")),
                "approved_nevo_code": _s(row.get("approved_nevo_code")),
                "approved_nevo_name": _s(row.get("approved_nevo_name")),
                "decision": decision,
                "reason": _s(row.get("reviewer_notes")),
                "should_auto_enrich": should_auto,
                "should_review": should_review,
                "safety_action": action,
                "review_source": meta["review_source"],
                "notes": _s(row.get("reviewer_notes")),
            })

        if _s(row.get("alias_candidate")) or _s(row.get("rule_candidate")):
            alias_rule.append({
                "product_name": meta["product_name"],
                "alias_candidate": _s(row.get("alias_candidate")),
                "rule_candidate": _s(row.get("rule_candidate")),
                "approved_nevo_code": _s(row.get("approved_nevo_code")),
                "approved_nevo_name": _s(row.get("approved_nevo_name")),
                "reviewer_notes": _s(row.get("reviewer_notes")),
                "status": "proposed",
            })

    if errors:
        recommendation = "blocked_by_errors"
    elif pending:
        recommendation = "review_incomplete"
    elif approved:
        recommendation = "ready_for_apply_planning_later"
    else:
        recommendation = "ready_for_gold_import"

    summary = {
        "project_id": project_id, "run_id": run_id, "input_path": input_path,
        "row_count": len(rows), "decision_counts": decision_counts,
        "pending_count": pending, "error_count": len(errors),
        "warning_count": len(warnings),
        "approved_candidate_count": len(approved),
        "gold_candidate_count": len(gold),
        "alias_rule_candidate_count": len(alias_rule),
        "recommendation": recommendation,
    }
    return {"summary": summary, "errors": errors, "warnings": warnings,
            "approved": approved, "gold": gold, "alias_rule": alias_rule}


def _write_csv(path: Path, cols: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "validate_nevo_v2_batch_review_package",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--project-id", default=None)
    ap.add_argument("--run-id", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        rows = _read_csv(args.input, "review package")
    except ApplyError as exc:
        print(f"FATAL: {exc}")
        return 2
    if not rows or "manual_decision" not in rows[0]:
        print("FATAL: not a filled review package (no manual_decision column)")
        return 2

    pid = args.project_id or _s(rows[0].get("project_id"))
    run_id = args.run_id or _s(rows[0].get("run_id"))
    if not run_id:
        m = re.search(r"_([^_]+)\.csv$", Path(args.input).name)
        run_id = m.group(1) if m else "run"

    result = validate_package(rows, project_id=pid, run_id=run_id,
                              input_path=str(args.input))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"{pid}_{run_id}"
    paths = {
        "summary": out / f"nevo_v2_batch_review_validation_summary_{suffix}.json",
        "errors": out / f"nevo_v2_batch_review_errors_{suffix}.csv",
        "warnings": out / f"nevo_v2_batch_review_warnings_{suffix}.csv",
        "approved": out / f"nevo_v2_batch_review_approved_candidates_{suffix}.csv",
        "gold": out / f"nevo_v2_batch_review_gold_candidates_{suffix}.json",
        "alias": out / f"nevo_v2_batch_review_alias_rule_candidates_{suffix}.csv",
    }
    paths["summary"].write_text(
        json.dumps(result["summary"], indent=2, ensure_ascii=False),
        encoding="utf-8")
    _write_csv(paths["errors"], ISSUE_CSV_COLUMNS, result["errors"])
    _write_csv(paths["warnings"], ISSUE_CSV_COLUMNS, result["warnings"])
    _write_csv(paths["approved"], APPROVED_CSV_COLUMNS, result["approved"])
    paths["gold"].write_text(
        json.dumps({"candidates": result["gold"]}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    _write_csv(paths["alias"], ALIAS_RULE_CSV_COLUMNS, result["alias_rule"])

    s = result["summary"]
    print("# NEVO V2 batch review validation (READ-ONLY — no database writes)")
    print(f"  project={s['project_id']} run_id={s['run_id']} rows={s['row_count']}")
    print(f"  decisions={s['decision_counts']} pending={s['pending_count']}")
    print(f"  errors={s['error_count']} warnings={s['warning_count']} "
          f"approved={s['approved_candidate_count']} "
          f"gold={s['gold_candidate_count']} "
          f"alias_rule={s['alias_rule_candidate_count']}")
    print(f"  RECOMMENDATION: {s['recommendation']}")
    for label, key in (("Summary", "summary"), ("Errors", "errors"),
                       ("Warnings", "warnings"), ("Approved", "approved"),
                       ("Gold", "gold"), ("Alias/rule", "alias")):
        print(f"  {label}: {paths[key]}")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

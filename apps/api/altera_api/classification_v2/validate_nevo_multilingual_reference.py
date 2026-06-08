"""Phase Quality-V2-AI (Part D) — validate the multilingual NEVO reference.

QA gate before any retrieval experiment. Confirms the generated artifact is
additive + safe: original codes preserved, no duplicate codes, no missing
original name, nutrition unchanged (vs an optional baseline), FR/DE coverage,
aliases free of commercial fields, and — critically — that no food state/form
was collapsed in translation (a "drink" must stay a drink, "dried" stay dried,
an oil/vinegar keep its type). Read-only: reads one CSV, writes a summary +
issues CSV. No DB writes, no routes.

    python -m altera_api.classification_v2.validate_nevo_multilingual_reference \
        --input /tmp/altera-quality/nevo_reference_multilingual.csv \
        --output-dir /tmp/altera-quality
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_multilingual_reference import (
    NUTRITION_COLUMN,
    OIL_TYPES,
    STATE_CHECKS,
    VINEGAR_TYPES,
    _tokens,
    parse_aliases,
)

ISSUE_COLUMNS = ["nevo_code", "nevo_food_name", "issue_type", "severity",
                 "message"]
#: Commercial/product WORDS that must never appear as a token in a food alias
#: (matched whole-word so "beans" does not trip on the "ean" substring).
_FORBIDDEN_ALIAS_WORDS = frozenset(
    {"price", "sales", "volume", "margin", "revenue", "turnover", "ean", "sku",
     "barcode", "units", "sold", "quantity"})
#: Currency symbols — matched as substrings (never valid in a food name).
_FORBIDDEN_ALIAS_SYMBOLS = ("€", "$", "£")
_ALIAS_WORD_RE = re.compile(r"[a-z0-9]+")


def _read_rows(path: str) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _has_marker(text: str, markers) -> bool:
    low = text.lower()
    return any(m in low for m in markers)


def state_collapse_issues(row: dict[str, Any]) -> list[str]:
    """Return high-risk state/form collapse issues for a row (empty if clean).

    For each high-risk state present in the English name, the FR and DE
    translation (name + aliases) must keep a corresponding marker.
    """
    name = _s(row.get("nevo_food_name"))
    en_tokens = set(_tokens(name))
    if not en_tokens:
        return []
    fr = (_s(row.get("nevo_food_name_fr")) + " "
          + " ".join(parse_aliases(row.get("search_aliases_fr")))).lower()
    de = (_s(row.get("nevo_food_name_de")) + " "
          + " ".join(parse_aliases(row.get("search_aliases_de")))).lower()
    has_fr = bool(_s(row.get("nevo_food_name_fr")))
    has_de = bool(_s(row.get("nevo_food_name_de")))
    issues: list[str] = []

    for label, spec in STATE_CHECKS.items():
        if not (en_tokens & spec["en"]):
            continue
        if has_fr and not _has_marker(fr, spec["fr"]):
            issues.append(f"state '{label}' collapsed in FR translation")
        if has_de and not _has_marker(de, spec["de"]):
            issues.append(f"state '{label}' collapsed in DE translation")

    if "oil" in en_tokens:
        for typ, mk in OIL_TYPES.items():
            if typ in en_tokens:
                if has_fr and not _has_marker(fr, mk["fr"]):
                    issues.append(f"oil type '{typ}' missing in FR translation")
                if has_de and not _has_marker(de, mk["de"]):
                    issues.append(f"oil type '{typ}' missing in DE translation")
    if "vinegar" in en_tokens:
        for typ, mk in VINEGAR_TYPES.items():
            if typ in en_tokens:
                if has_fr and not _has_marker(fr, mk["fr"]):
                    issues.append(
                        f"vinegar type '{typ}' missing in FR translation")
                if has_de and not _has_marker(de, mk["de"]):
                    issues.append(
                        f"vinegar type '{typ}' missing in DE translation")
    return issues


def _forbidden_alias(row: dict[str, Any]) -> str | None:
    for col in ("search_aliases_fr", "search_aliases_de", "search_aliases_en"):
        for alias in parse_aliases(row.get(col)):
            low = alias.lower()
            for sym in _FORBIDDEN_ALIAS_SYMBOLS:
                if sym in alias:
                    return f"alias {alias!r} contains forbidden symbol {sym!r}"
            words = set(_ALIAS_WORD_RE.findall(low))
            hit = words & _FORBIDDEN_ALIAS_WORDS
            if hit:
                return (f"alias {alias!r} contains forbidden word "
                        f"{sorted(hit)[0]!r}")
    return None


def validate_rows(rows: list[dict[str, Any]], *,
                  baseline: list[dict[str, Any]] | None = None,
                  ) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def add(row, itype, severity, message):
        issues.append({
            "nevo_code": _s(row.get("nevo_code")),
            "nevo_food_name": _s(row.get("nevo_food_name")),
            "issue_type": itype, "severity": severity, "message": message})

    # Duplicate / missing checks.
    seen: dict[str, int] = {}
    for row in rows:
        code = _s(row.get("nevo_code"))
        if code:
            seen[code] = seen.get(code, 0) + 1
        if not _s(row.get("nevo_food_name")):
            add(row, "missing_name", "high", "missing original nevo_food_name")
    for row in rows:
        if seen.get(_s(row.get("nevo_code")), 0) > 1:
            add(row, "duplicate_code", "high",
                f"nevo_code {row.get('nevo_code')!r} appears "
                f"{seen[_s(row.get('nevo_code'))]} times")
            seen[_s(row.get("nevo_code"))] = -1  # report once.

    # Baseline preservation (codes + nutrition unchanged), if provided.
    high_risk = 0
    if baseline is not None:
        base_by_code = {_s(b.get("nevo_code")): b for b in baseline
                        if _s(b.get("nevo_code"))}
        present = {_s(r.get("nevo_code")) for r in rows}
        for code in base_by_code:
            if code not in present:
                issues.append({
                    "nevo_code": code, "nevo_food_name": "",
                    "issue_type": "dropped_code", "severity": "high",
                    "message": "original NEVO code missing from artifact"})
                high_risk += 1
        for row in rows:
            base = base_by_code.get(_s(row.get("nevo_code")))
            if base is None:
                continue
            b_protein = _s(base.get(NUTRITION_COLUMN) or base.get("protein"))
            r_protein = _s(row.get(NUTRITION_COLUMN))
            if b_protein and r_protein and b_protein != r_protein:
                add(row, "nutrition_changed", "high",
                    f"protein changed {b_protein!r} -> {r_protein!r}")
                high_risk += 1

    # Per-row state-collapse + commercial-alias checks.
    for row in rows:
        for msg in state_collapse_issues(row):
            add(row, "state_collapse", "high", msg)
            high_risk += 1
        forbidden = _forbidden_alias(row)
        if forbidden:
            add(row, "commercial_alias", "high", forbidden)
            high_risk += 1

    def nonblank(field):
        return sum(1 for r in rows if _s(r.get(field)))

    needs_review = sum(1 for r in rows
                       if _s(r.get("translation_review_status"))
                       == "needs_review")
    rows_with_fr = nonblank("nevo_food_name_fr")
    rows_with_de = nonblank("nevo_food_name_de")
    total = len(rows)
    fr_cov = rows_with_fr / total if total else 0.0
    de_cov = rows_with_de / total if total else 0.0

    if high_risk:
        recommendation = "blocked_by_high_risk_translation_issues"
    elif issues or fr_cov < 0.5 or de_cov < 0.5 or (
            total and needs_review / total > 0.5):
        recommendation = "needs_translation_review"
    else:
        recommendation = "ready_for_retrieval_experiment"

    summary = {
        "phase": "quality-v2-ai",
        "total_rows": total,
        "rows_with_fr": rows_with_fr,
        "rows_with_de": rows_with_de,
        "rows_with_aliases_fr": nonblank("search_aliases_fr"),
        "rows_with_aliases_de": nonblank("search_aliases_de"),
        "fr_coverage": round(fr_cov, 4),
        "de_coverage": round(de_cov, 4),
        "needs_review_count": needs_review,
        "review_status_distribution": _dist(rows,
                                            "translation_review_status"),
        "issue_count": len(issues),
        "high_risk_translation_issue_count": high_risk,
        "recommendation": recommendation,
    }
    return {"summary": summary, "issues": issues}


def _dist(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[_s(r.get(field)) or "(blank)"] = out.get(
            _s(r.get(field)) or "(blank)", 0) + 1
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "validate_nevo_multilingual_reference",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--baseline-reference-source", choices=["fixture", "nevo"],
                    default=None,
                    help="optional: verify codes/nutrition vs the original.")
    ap.add_argument("--baseline-reference", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}")
        return 2
    rows = _read_rows(args.input)
    baseline = None
    if args.baseline_reference_source:
        baseline = load_nevo_reference(args.baseline_reference_source,
                                       path=args.baseline_reference)
        baseline = [{"nevo_code": b.get("nevo_code"),
                     "nevo_food_name": b.get("food_name_en"),
                     NUTRITION_COLUMN: b.get(NUTRITION_COLUMN)}
                    for b in baseline]

    result = validate_rows(rows, baseline=baseline)
    s = result["summary"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    issues_path = out_dir / "nevo_reference_multilingual_validation_issues.csv"
    with issues_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ISSUE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in result["issues"]:
            w.writerow(row)
    summary_path = (out_dir
                    / "nevo_reference_multilingual_validation_summary.json")
    summary_path.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    print("# NEVO multilingual reference validation (read-only — no DB writes)")
    print(f"  rows={s['total_rows']} fr={s['rows_with_fr']} "
          f"de={s['rows_with_de']} needs_review={s['needs_review_count']}")
    print(f"  issues={s['issue_count']} "
          f"high_risk={s['high_risk_translation_issue_count']}")
    print(f"  RECOMMENDATION: {s['recommendation']}")
    print(f"  Issues CSV: {issues_path}")
    print(f"  Summary JSON: {summary_path}")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase Quality-V2-F — NEVO fixture ↔ reference alignment validator.

Reports, per fixture case, whether its expected NEVO reference actually
exists in the chosen reference source, plus the closest existing names and
a suggested action — so fixtures can be aligned with real NEVO 2025
without inventing references or weakening safety traps.

Runnable as a package module (ships in the Render image)::

    python -m altera_api.classification_v2.validate_nevo_fixtures \
        --fixture altera_api/data/eval/nevo/nevo_dataset_embeddings.json \
        --reference-source nevo \
        --output-csv /tmp/altera-quality/nevo_fixture_alignment.csv

Offline-only; reads the shipped CSV. No embeddings/network required.
"""

from __future__ import annotations

import argparse
import csv
import difflib
from pathlib import Path
from typing import Any

from altera_api.classification_v2.evaluation import load_fixture
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_rules import concept_of

ALIGNMENT_CSV_COLUMNS = [
    "fixture_id", "product_name", "should_match", "expected_name",
    "expected_code", "expected_concept", "code_exists", "name_exists",
    "concept_present_in_reference", "closest_reference_names",
    "suggested_action", "notes",
]


def _suggested_action(
    *, should_match: bool, has_expected: bool, code_exists: bool,
    name_exists: bool, concept_present: bool, has_close: bool,
) -> str:
    if not should_match:
        return "valid_should_abstain"
    if not has_expected:
        return "mark_should_abstain"
    if code_exists or name_exists:
        return "valid"
    if concept_present:
        return "update_expected_to_existing_reference"
    if has_close:
        return "ambiguous"
    return "expected_reference_absent"


def validate(
    cases: list[dict[str, Any]], references: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ref_names = [str(r.get("food_name_en", "")) for r in references]
    ref_names_lower = [n.lower() for n in ref_names]
    ref_codes = {str(r.get("nevo_code", "")) for r in references if r.get("nevo_code")}
    by_concept: dict[str, list[str]] = {}
    for n in ref_names:
        c = concept_of(n)
        if c:
            by_concept.setdefault(c, []).append(n)

    rows: list[dict[str, Any]] = []
    for case in cases:
        expected = case.get("expected_match") or {}
        should_match = bool(case.get("should_match", bool(expected)))
        exp_name = str(expected.get("food_name_en", ""))
        exp_code = str(expected.get("nevo_code", ""))
        exp_concept = concept_of(exp_name) if exp_name else None

        code_exists = bool(exp_code) and exp_code in ref_codes
        name_exists = bool(exp_name) and exp_name.lower() in ref_names_lower
        concept_present = exp_concept is not None and exp_concept in by_concept

        # Closest names: prefer same-concept references, then lexical fuzzy.
        closest: list[str] = []
        if exp_concept and exp_concept in by_concept:
            closest = by_concept[exp_concept][:5]
        elif exp_name:
            idxs = difflib.get_close_matches(
                exp_name.lower(), ref_names_lower, n=5, cutoff=0.5
            )
            closest = [ref_names[ref_names_lower.index(m)] for m in idxs]

        rows.append(
            {
                "fixture_id": str(case.get("id", "")),
                "product_name": case.get("product_name", ""),
                "should_match": should_match,
                "expected_name": exp_name,
                "expected_code": exp_code,
                "expected_concept": exp_concept or "",
                "code_exists": code_exists,
                "name_exists": name_exists,
                "concept_present_in_reference": concept_present,
                "closest_reference_names": " | ".join(closest),
                "suggested_action": _suggested_action(
                    should_match=should_match, has_expected=bool(expected),
                    code_exists=code_exists, name_exists=name_exists,
                    concept_present=concept_present, has_close=bool(closest),
                ),
                "notes": case.get("notes", ""),
            }
        )
    return rows


def write_alignment_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ALIGNMENT_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.validate_nevo_fixtures",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--fixture",
        default="altera_api/data/eval/nevo/nevo_dataset_embeddings.json",
    )
    ap.add_argument("--reference-source", choices=["fixture", "nevo"], default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--output-csv", default="/tmp/altera-quality/nevo_fixture_alignment.csv")
    args = ap.parse_args(argv)

    cases = load_fixture(args.fixture)
    references = load_nevo_reference(args.reference_source, path=args.reference)
    rows = validate(cases, references)
    write_alignment_csv(args.output_csv, rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["suggested_action"]] = counts.get(r["suggested_action"], 0) + 1
    print(f"# NEVO fixture alignment ({len(cases)} cases vs "
          f"{len(references)} {args.reference_source} foods)")
    for action, n in sorted(counts.items()):
        print(f"  {action}: {n}")
    absent = [r for r in rows if r["suggested_action"] == "expected_reference_absent"]
    if absent:
        print("\nExpected reference absent (needs attention):")
        for r in absent:
            print(f"  {r['fixture_id']} | {r['product_name']} | "
                  f"exp {r['expected_name']!r} | closest: {r['closest_reference_names']}")
    print(f"\nWrote {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

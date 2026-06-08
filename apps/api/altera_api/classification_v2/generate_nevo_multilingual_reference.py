"""Phase Quality-V2-AI (Part C) — generate the multilingual NEVO reference.

Materializes FR/DE NEVO names + search aliases ONCE into
``nevo_reference_multilingual.csv`` (+ summary JSON + review sample). The
original NEVO name/code/nutrition are preserved exactly; translations are never
generated per search. Read-only: reads the NEVO reference + writes artifacts; no
DB writes, no routes, no V2 default change. Only the public NEVO English food
name is translated — no retailer commercial data is sent anywhere.

    python -m altera_api.classification_v2.generate_nevo_multilingual_reference \
        --reference-source nevo --output-dir /tmp/altera-quality \
        --languages fr,de --max-aliases-per-language 8 --no-llm

``--no-llm`` (default behaviour when no working LLM translator is configured)
uses the deterministic rule-based translator: curated FR/DE for known foods
(auto_validated), a state-preserving compositional fallback (needs_review), else
unavailable/needs_review. State/form words (raw/cooked/dried/drink/instant/
powder/oil type/vinegar type) are never collapsed.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_multilingual_reference import (
    ML_COLUMNS,
    CompositionalTranslator,
    DeterministicTranslator,
    generate_rows,
)

OUTPUT_CSV = "nevo_reference_multilingual.csv"
SUMMARY_JSON = "nevo_reference_multilingual_summary.json"
REVIEW_SAMPLE_CSV = "nevo_reference_multilingual_review_sample.csv"


def _load_existing(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return {(_s(r.get("nevo_code"))): r for r in csv.DictReader(fh)
                if _s(r.get("nevo_code"))}


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]],
               ) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[_s(r.get(field)) or "(blank)"] = out.get(
            _s(r.get(field)) or "(blank)", 0) + 1
    return out


def generate(*, reference_source: str, input_reference: str | None,
             languages: tuple[str, ...], max_aliases: int, limit: int | None,
             resume_from: str | None, only_missing: bool,
             translator: Any) -> list[dict[str, Any]]:
    references = load_nevo_reference(reference_source, path=input_reference)
    existing = _load_existing(resume_from)
    return generate_rows(
        references, translator=translator, languages=languages,
        max_aliases=max_aliases, existing_by_code=existing,
        only_missing=only_missing, limit=limit)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "generate_nevo_multilingual_reference",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--reference-source", choices=["fixture", "nevo"],
                    default="nevo")
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--languages", default="fr,de")
    ap.add_argument("--max-aliases-per-language", type=int, default=8)
    ap.add_argument("--input-reference", default=None)
    ap.add_argument("--output-reference", default=None)
    ap.add_argument("--limit-rows", type=int, default=None)
    ap.add_argument("--llm-provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-llm", action="store_true",
                    help="use the deterministic translator (no network).")
    ap.add_argument("--translator", choices=["deterministic", "compositional"],
                    default="deterministic",
                    help="deterministic (default, curated only) or "
                         "compositional (curated + safe token composition).")
    ap.add_argument("--expand-compositional", action="store_true",
                    help="shorthand for --translator compositional: safe "
                         "deterministic FR/DE coverage expansion. Default off.")
    ap.add_argument("--coverage-target", type=float, default=0.50,
                    help="reported target; does not change generation.")
    ap.add_argument("--resume-from-existing", default=None)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--review-sample-size", type=int, default=50)
    return ap


def main(argv: list[str] | None = None, *, translator: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out_dir = Path(args.output_dir)
    languages = tuple(s.strip() for s in args.languages.split(",") if s.strip())

    llm_requested = bool(args.llm_provider) and not args.no_llm
    expand = args.expand_compositional or args.translator == "compositional"
    if translator is None:
        # No working LLM translator is wired into this build; the deterministic
        # translator is always available and never sends data anywhere. The
        # default is the curated-only DeterministicTranslator (behaviour
        # unchanged); --expand-compositional opts into safe token composition.
        translator = (CompositionalTranslator() if expand
                      else DeterministicTranslator())
        if llm_requested:
            print(f"NOTE: --llm-provider {args.llm_provider!r} requested but no "
                  "LLM translator is configured; using the deterministic "
                  "translator. Pass --no-llm to silence this.")

    rows = generate(
        reference_source=args.reference_source,
        input_reference=args.input_reference, languages=languages,
        max_aliases=args.max_aliases_per_language, limit=args.limit_rows,
        resume_from=args.resume_from_existing, only_missing=args.only_missing,
        translator=translator)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = Path(args.output_reference) if args.output_reference else (
        out_dir / OUTPUT_CSV)
    _write_csv(out_csv, ML_COLUMNS, rows)

    needs_review = [r for r in rows
                    if _s(r.get("translation_review_status")) == "needs_review"]
    sample = needs_review[: max(0, args.review_sample_size)]
    sample_path = out_dir / REVIEW_SAMPLE_CSV
    _write_csv(sample_path, ML_COLUMNS, sample)

    def _nonblank(field: str) -> int:
        return sum(1 for r in rows if _s(r.get(field)))

    summary = {
        "phase": "quality-v2-ai",
        "reference_source": args.reference_source,
        "translator": getattr(translator, "name", "custom"),
        "llm_requested": llm_requested,
        "languages": list(languages),
        "max_aliases_per_language": args.max_aliases_per_language,
        "expand_compositional": expand,
        "total_rows": len(rows),
        "rows_with_fr": _nonblank("nevo_food_name_fr"),
        "rows_with_de": _nonblank("nevo_food_name_de"),
        "fr_coverage": round(_nonblank("nevo_food_name_fr") / len(rows), 4)
        if rows else 0.0,
        "de_coverage": round(_nonblank("nevo_food_name_de") / len(rows), 4)
        if rows else 0.0,
        "coverage_target": args.coverage_target,
        "coverage_target_reached": bool(
            rows and _nonblank("nevo_food_name_fr") / len(rows)
            >= args.coverage_target),
        "rows_with_aliases_fr": _nonblank("search_aliases_fr"),
        "rows_with_aliases_de": _nonblank("search_aliases_de"),
        "count_by_translation_source": _counts(rows, "translation_source"),
        "count_by_review_status": _counts(rows, "translation_review_status"),
        "needs_review_count": len(needs_review),
        "output_paths": {
            "multilingual_reference_csv": str(out_csv),
            "review_sample_csv": str(sample_path),
        },
    }
    summary_path = out_dir / SUMMARY_JSON
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    print("# NEVO multilingual reference (read-only — no database writes)")
    print(f"  source={args.reference_source} translator={summary['translator']}"
          f" languages={','.join(languages)} rows={len(rows)}")
    print(f"  with_fr={summary['rows_with_fr']} with_de={summary['rows_with_de']}"
          f" needs_review={summary['needs_review_count']}")
    print(f"  by_source={summary['count_by_translation_source']}")
    print(f"  Reference CSV: {out_csv}")
    print(f"  Review sample: {sample_path}")
    print(f"  Summary JSON:  {summary_path}")
    print("Next: validate with validate_nevo_multilingual_reference, then pass "
          "--multilingual-reference to a V2 dry-run to experiment.")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

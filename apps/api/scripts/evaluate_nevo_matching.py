#!/usr/bin/env python
"""Phase Quality-V2-A — NEVO matching evaluator (precision-first).

Runs a NEVO fixture through the V2 candidate gates and reports
coverage, high-confidence precision, abstain rate, and — most
importantly — the false-positive count (forbidden candidates that
slipped through). Wrapper around
``altera_api.classification_v2.evaluation.evaluate_nevo``.

    .venv/bin/python scripts/evaluate_nevo_matching.py \\
        --fixture altera_api/data/eval/nevo/nevo_composite_traps.json \\
        --mismatches-csv /tmp/nevo_mismatches.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from altera_api.classification_v2.evaluation import (  # noqa: E402
    evaluate_nevo,
    load_fixture,
    write_mismatches_csv,
)
from altera_api.quality_config import MatcherVersion  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fixture",
        default="altera_api/data/eval/nevo/nevo_simple_exact.json",
    )
    ap.add_argument("--matcher-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--mismatches-csv", default=None)
    ap.add_argument("--auto-accept-threshold", type=float, default=0.90)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cases = load_fixture(args.fixture)
    metrics = evaluate_nevo(
        cases,
        matcher_version=MatcherVersion(args.matcher_version),
        auto_accept_threshold=args.auto_accept_threshold,
    )
    if args.mismatches_csv:
        write_mismatches_csv(args.mismatches_csv, metrics.mismatches)

    summary = metrics.as_dict()
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"# NEVO matching evaluation — fixture={args.fixture}")
        for k, v in summary.items():
            print(f"- {k}: {v}")
    # Non-zero exit if any forbidden candidate slipped through.
    return 1 if metrics.false_positive_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

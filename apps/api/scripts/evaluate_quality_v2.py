#!/usr/bin/env python
"""Phase Quality-V2-A — unified V1/V2 quality evaluator.

Runs a classification (PT/WWF) or NEVO fixture through the selected
pipeline version and prints metrics. Optional mismatch CSV.

Examples
--------
    .venv/bin/python scripts/evaluate_quality_v2.py \\
        --task pt --pipeline-version v2 \\
        --fixture altera_api/data/eval/classification/pt/pt_dataset_100.json

    .venv/bin/python scripts/evaluate_quality_v2.py \\
        --task nevo --matcher-version v2 \\
        --fixture altera_api/data/eval/nevo/nevo_composite_traps.json \\
        --mismatches-csv /tmp/nevo_mismatches.csv

The V2 paths require no embeddings/network. ``--pipeline-version v1``
runs the existing V1 readable-fallback baseline for comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from altera_api.classification_v2.evaluation import (  # noqa: E402
    evaluate_classification,
    evaluate_nevo,
    load_fixture,
    write_mismatches_csv,
)
from altera_api.quality_config import (  # noqa: E402
    MatcherVersion,
    PipelineVersion,
)

_DEFAULT_FIXTURES = {
    "pt": "altera_api/data/eval/classification/pt/pt_dataset_100.json",
    "wwf": "altera_api/data/eval/classification/wwf/wwf_dataset_100.json",
    "nevo": "altera_api/data/eval/nevo/nevo_simple_exact.json",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", choices=["pt", "wwf", "nevo"], required=True)
    ap.add_argument("--pipeline-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--matcher-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--fixture", type=str, default=None)
    ap.add_argument("--mismatches-csv", type=str, default=None)
    ap.add_argument("--auto-accept-threshold", type=float, default=0.90)
    ap.add_argument("--strict-threshold", type=float, default=0.0)
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    fixture = args.fixture or _DEFAULT_FIXTURES[args.task]
    cases = load_fixture(fixture)

    if args.task == "nevo":
        metrics = evaluate_nevo(
            cases,
            matcher_version=MatcherVersion(args.matcher_version),
            auto_accept_threshold=args.auto_accept_threshold,
        )
    else:
        metrics = evaluate_classification(
            args.task,
            cases,
            pipeline_version=PipelineVersion(args.pipeline_version),
            auto_accept_threshold=args.auto_accept_threshold,
        )

    if args.mismatches_csv:
        write_mismatches_csv(args.mismatches_csv, metrics.mismatches)

    summary = metrics.as_dict()
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"# Quality-V2 evaluation — task={args.task} fixture={fixture}")
        for k, v in summary.items():
            print(f"- {k}: {v}")
        if metrics.mismatches:
            print(f"\n## Mismatches ({len(metrics.mismatches)})")
            for mm in metrics.mismatches[:20]:
                print(
                    f"- {mm.product_name}: expected {mm.expected}, "
                    f"got {mm.actual} (rule={mm.rule_id}; {mm.notes})"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

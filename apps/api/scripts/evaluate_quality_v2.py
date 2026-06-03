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
    compare_classification,
    evaluate_classification,
    evaluate_nevo,
    load_fixture,
    nevo_gates,
    pt_gates,
    write_improvements_csv,
    write_mismatches_csv,
    wwf_gates,
)
from altera_api.quality_config import (  # noqa: E402
    MatcherVersion,
    PipelineVersion,
)

_DEFAULT_FIXTURES = {
    "pt": "altera_api/data/eval/classification/pt/pt_dataset_v2b.json",
    "wwf": "altera_api/data/eval/classification/wwf/wwf_dataset_v2b.json",
    "nevo": "altera_api/data/eval/nevo/nevo_dataset_v2b.json",
}


def _run_compare(task: str, fixture: str, args) -> int:
    """Phase Quality-V2-B — V1 vs V2 comparison table + quality gates."""
    cases = load_fixture(fixture)
    if task == "nevo":
        # No offline V1 matcher — report V2 metrics + absolute gates.
        v2 = evaluate_nevo(cases, matcher_version=MatcherVersion.V2,
                           auto_accept_threshold=args.auto_accept_threshold)
        gates = nevo_gates(v2)
        if args.json:
            print(json.dumps({"v2": v2.as_dict(), "gates": gates}, indent=2))
        else:
            print(f"# NEVO V2 (fixture={fixture})")
            for k, v in v2.as_dict().items():
                print(f"- {k}: {v}")
            print(f"- GATES: {gates}")
        return 0 if gates["passed"] else 1

    cmp = compare_classification(task, cases,
                                 auto_accept_threshold=args.auto_accept_threshold)
    gates = (pt_gates if task == "pt" else wwf_gates)(cmp)
    if args.mismatches_csv:
        write_mismatches_csv(args.mismatches_csv, cmp.v2.mismatches)
        write_mismatches_csv(args.mismatches_csv.replace(".csv", ".v1.csv"), cmp.v1.mismatches)
    if args.improvements_csv:
        write_improvements_csv(args.improvements_csv, cmp.deltas)
    if args.json:
        print(json.dumps({**cmp.as_dict(), "gates": gates}, indent=2, ensure_ascii=False))
    else:
        print(f"# {task.upper()} V1 vs V2 (fixture={fixture}, n={cmp.v1.total})")
        print(f"- accuracy:        V1={cmp.v1.accuracy:.3f}  V2={cmp.v2.accuracy:.3f}  "
              f"Δ={cmp.v2.accuracy - cmp.v1.accuracy:+.3f}")
        print(f"- wrong_accepted:  V1={cmp.v1.wrong_accepted}  V2={cmp.v2.wrong_accepted}")
        print(f"- unknown_readable:V1={cmp.v1.unknown_readable}  V2={cmp.v2.unknown_readable}")
        if task == "wwf":
            b1 = cmp.v1.composite_bucket_correct / (cmp.v1.composite_bucket_total or 1)
            b2 = cmp.v2.composite_bucket_correct / (cmp.v2.composite_bucket_total or 1)
            print(f"- composite_bucket:V1={b1:.3f}  V2={b2:.3f}")
        print(f"- improvements={len(cmp.improvements)}  regressions={len(cmp.regressions)}")
        print(f"- GATES: {gates}")
    return 0 if gates["passed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", choices=["pt", "wwf", "nevo"], required=True)
    ap.add_argument("--pipeline-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--matcher-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--fixture", type=str, default=None)
    ap.add_argument("--mismatches-csv", type=str, default=None)
    ap.add_argument("--improvements-csv", type=str, default=None)
    ap.add_argument("--auto-accept-threshold", type=float, default=0.90)
    ap.add_argument("--strict-threshold", type=float, default=0.0)
    ap.add_argument(
        "--compare", action="store_true",
        help="Compare V1 vs V2 + compute quality gates (Phase Quality-V2-B).",
    )
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    fixture = args.fixture or _DEFAULT_FIXTURES[args.task]

    if args.compare:
        return _run_compare(args.task, fixture, args)

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

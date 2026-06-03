#!/usr/bin/env python
"""Phase Quality-V2-D — one-command real Voyage NEVO evaluation.

Thin convenience wrapper around the benchmark harness for a SINGLE real
Voyage model. Requires ``VOYAGE_API_KEY`` (and the ``voyageai`` SDK:
``pip install voyageai``). Prints metrics incl. token usage + estimated
cost, and writes mismatch + candidate CSVs to a git-ignored dir. Fails
clearly if the key is missing — never silently uses the fake provider.

OFFLINE/EVALUATOR-ONLY. Nothing here touches production; V1 stays the
app default regardless of these env vars.

    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \
    .venv/bin/python scripts/evaluate_nevo_voyage.py \
        --model voyage-4 --reference-source nevo --top-k 20

This delegates to ``benchmark_nevo_embeddings.py --require-voyage`` so a
missing key is a hard error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from altera_api.classification_v2.benchmark_nevo_embeddings import (  # noqa: E402
    main as benchmark_main,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("ALTERA_EMBEDDING_MODEL", "voyage-4"))
    ap.add_argument("--reference-source", choices=["fixture", "nevo"], default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--fixture", default="altera_api/data/eval/nevo/nevo_dataset_embeddings.json")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--price-per-1m", type=float, default=0.06)
    ap.add_argument("--output-dir", "--out-dir", dest="output_dir", default="/tmp/altera-quality")
    args = ap.parse_args()

    if not os.environ.get("VOYAGE_API_KEY"):
        print(
            "FATAL: VOYAGE_API_KEY is not set. Export it (and ensure the "
            "voyageai SDK is installed) before running the real Voyage eval.",
            file=sys.stderr,
        )
        return 2

    # Delegate to the package benchmark for a SINGLE model, with
    # --require-voyage so a missing key / SDK is a hard error.
    argv = [
        "--models", args.model,
        "--reference-source", args.reference_source,
        "--fixture", args.fixture,
        "--top-k", str(args.top_k),
        "--price-per-1m", str(args.price_per_1m),
        "--output-dir", args.output_dir,
        "--require-voyage",
    ]
    if args.reference:
        argv += ["--reference", args.reference]
    return benchmark_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

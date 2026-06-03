#!/usr/bin/env python
"""Phase Quality-V2-A/C — NEVO matching evaluator (precision-first).

Compares NEVO matching pipelines on a fixture and reports coverage,
high-confidence precision, abstain rate, and — most importantly — the
false-positive count (forbidden candidates that slipped through).

    # rules-only (offline, no embeddings)
    .venv/bin/python scripts/evaluate_nevo_matching.py --matcher-version v2 \\
        --fixture altera_api/data/eval/nevo/nevo_dataset_v2b.json

    # rules + embeddings, fake provider (offline, deterministic)
    .venv/bin/python scripts/evaluate_nevo_matching.py \\
        --matcher-version v2-embeddings --embedding-provider fake \\
        --fixture altera_api/data/eval/nevo/nevo_dataset_embeddings.json \\
        --candidates-csv /tmp/nevo_candidates.csv

    # rules + embeddings, real Voyage (needs VOYAGE_API_KEY) — manual smoke
    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \\
    .venv/bin/python scripts/evaluate_nevo_matching.py \\
        --matcher-version v2-embeddings --embedding-provider voyage \\
        --embedding-model voyage-4-lite
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
    nevo_gates,
    write_mismatches_csv,
)
from altera_api.quality_config import MatcherVersion  # noqa: E402

_DEFAULT_REFERENCE = "altera_api/data/eval/nevo/nevo_reference.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fixture", default="altera_api/data/eval/nevo/nevo_dataset_v2b.json"
    )
    ap.add_argument(
        "--matcher-version", choices=["v1", "v2", "v2-embeddings"], default="v2"
    )
    ap.add_argument("--embedding-provider", choices=["fake", "voyage"], default="fake")
    ap.add_argument("--embedding-model", default=None)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--reference", default=_DEFAULT_REFERENCE)
    ap.add_argument("--mismatches-csv", default=None)
    ap.add_argument("--candidates-csv", default=None)
    ap.add_argument("--auto-accept-threshold", type=float, default=0.90)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cases = load_fixture(args.fixture)

    if args.matcher_version == "v2-embeddings":
        # Imported lazily so the embeddings stack is only touched here.
        from altera_api.classification_v2.nevo_eval_embeddings import (
            evaluate_nevo_embeddings,
            write_candidates_csv,
        )
        from altera_api.embeddings.provider import build_embedding_provider

        references = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        references = references.get("references", references)
        # build_embedding_provider raises a clear error if voyage is
        # selected without VOYAGE_API_KEY — no silent fall-back.
        provider = build_embedding_provider(
            args.embedding_provider, model=args.embedding_model
        )
        metrics, rows = evaluate_nevo_embeddings(
            cases, references, provider,
            provider_name=args.embedding_provider, top_k=args.top_k,
            auto_accept_threshold=args.auto_accept_threshold,
        )
        if args.candidates_csv:
            write_candidates_csv(args.candidates_csv, rows)
    else:
        metrics = evaluate_nevo(
            cases,
            matcher_version=MatcherVersion(args.matcher_version),
            auto_accept_threshold=args.auto_accept_threshold,
        )

    if args.mismatches_csv:
        write_mismatches_csv(args.mismatches_csv, metrics.mismatches)

    gates = nevo_gates(metrics)
    summary = metrics.as_dict()
    if args.json:
        print(json.dumps({**summary, "gates": gates}, indent=2, ensure_ascii=False))
    else:
        print(f"# NEVO matching — matcher={args.matcher_version} fixture={args.fixture}")
        for k, v in summary.items():
            print(f"- {k}: {v}")
        print(f"- gates: {gates}")
    # Non-zero exit if any forbidden candidate slipped through OR a gate fails.
    return 0 if (metrics.false_positive_count == 0 and gates["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

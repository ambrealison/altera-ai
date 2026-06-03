#!/usr/bin/env python
"""Phase Quality-V2-D — benchmark NEVO embeddings: fake vs Voyage models.

Runs the V2 rules+embeddings NEVO pipeline for one or more embedding
models over a fixture, and prints a comparison table (coverage,
high-confidence precision, false positives, forbidden rejection, top-k
recall, abstain rate, embedding calls, token usage, estimated cost).
Per-model candidate + mismatch CSVs are written to a git-ignored dir.

OFFLINE-SAFE: ``fake`` runs with no key/network. Voyage models require
``VOYAGE_API_KEY``; without it they are SKIPPED (the table notes it),
unless ``--require-voyage`` is set. Nothing here touches production.

Examples
--------
    # offline, deterministic (fake only)
    .venv/bin/python scripts/benchmark_nevo_embeddings.py --models fake

    # real benchmark on Render shell (key in env). Install the SDK first:
    #   pip install voyageai
    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \
    .venv/bin/python scripts/benchmark_nevo_embeddings.py \
        --models fake,voyage-4,voyage-4-lite \
        --reference-source nevo --top-k 20 --price-per-1m 0.06
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from altera_api.classification_v2.evaluation import (  # noqa: E402
    load_fixture,
    nevo_gates,
    write_mismatches_csv,
)
from altera_api.classification_v2.nevo_eval_embeddings import (  # noqa: E402
    evaluate_nevo_embeddings,
    summarize_candidates,
    write_candidates_csv,
)
from altera_api.classification_v2.nevo_index import load_nevo_reference  # noqa: E402
from altera_api.embeddings.provider import (  # noqa: E402
    EmbeddingProviderError,
    build_embedding_provider,
)

_DEFAULT_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"


def _pct(x: float | None) -> str:
    return f"{x*100:5.1f}%" if x is not None else "   —  "


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="fake",
                    help="comma list: fake, voyage-4, voyage-4-lite, …")
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"], default="fixture")
    ap.add_argument("--reference", default=None, help="explicit reference path")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--price-per-1m", type=float, default=0.06,
                    help="USD per 1M tokens (estimate; configure per model).")
    ap.add_argument("--out-dir", default="local_data/quality")
    ap.add_argument("--require-voyage", action="store_true",
                    help="fail if a voyage model is requested without a key.")
    args = ap.parse_args()

    cases = load_fixture(args.fixture)
    references = load_nevo_reference(args.reference_source, path=args.reference)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("# NEVO embeddings benchmark")
    print(f"  fixture={args.fixture} ({len(cases)} cases)")
    print(f"  reference={args.reference_source} ({len(references)} foods)  top_k={args.top_k}\n")

    table: list[tuple] = []
    gate_failed = False
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        provider_name = "fake" if model == "fake" else "voyage"
        try:
            provider = build_embedding_provider(
                provider_name, model=None if model == "fake" else model
            )
        except EmbeddingProviderError as exc:
            if args.require_voyage:
                print(f"FATAL: {model}: {exc}")
                return 1
            print(f"  [skip] {model}: {exc}")
            table.append((model, None))
            continue

        m, rows = evaluate_nevo_embeddings(
            cases, references, provider, provider_name=provider_name, top_k=args.top_k
        )
        slug = model.replace(".", "_")
        write_candidates_csv(out_dir / f"nevo_candidates_{slug}.csv", rows)
        write_mismatches_csv(out_dir / f"nevo_mismatches_{slug}.csv", m.mismatches)
        tax = summarize_candidates(cases, rows)
        gates = nevo_gates(m)
        if not gates["passed"]:
            gate_failed = True
        d = m.as_dict()
        d["cost"] = round(m.token_total / 1_000_000 * args.price_per_1m, 4)
        d["taxonomy"] = tax
        d["gates"] = gates
        table.append((model, d))
        print(f"  [done] {model}: gates={gates['passed']} taxonomy={tax}")

    # ---- comparison table ----
    print("\n" + "=" * 96)
    hdr = (f"{'Model':14} {'Coverage':>9} {'HC-FP':>6} {'Forbid-rej':>11} "
           f"{'top1':>7} {'top5':>7} {'top20':>7} {'abstain':>8} {'calls':>6} "
           f"{'tokens':>8} {'cost$':>8}")
    print(hdr)
    print("-" * 96)
    for model, d in table:
        if d is None:
            print(f"{model:14} {'(skipped — no VOYAGE_API_KEY)':>40}")
            continue
        forbid = d["forbidden_rejection_rate"]
        abstain = d["abstain_count"] / d["total"] if d["total"] else 0.0
        print(
            f"{model:14} {_pct(d['coverage']):>9} {d['false_positive_count']:>6} "
            f"{_pct(forbid):>11} {_pct(d['expected_top1']):>7} "
            f"{_pct(d['expected_top5']):>7} {_pct(d['expected_top20']):>7} "
            f"{_pct(abstain):>8} {d['embedding_calls']:>6} {d['token_total']:>8} "
            f"{d['cost']:>8.4f}"
        )
    print("=" * 96)
    print(f"CSVs written to {out_dir}/ (git-ignored).")
    print("Cost is an ESTIMATE — set --price-per-1m to the model's real price.")
    return 1 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

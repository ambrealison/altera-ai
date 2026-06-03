"""Phase Quality-V2-D (hotfix) — NEVO embeddings benchmark, package CLI.

Runnable as a module so it works inside the Render runtime image, which
copies ``altera_api/`` but NOT the top-level ``scripts/`` directory::

    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
    python -m altera_api.classification_v2.benchmark_nevo_embeddings \
        --models fake,voyage-4,voyage-4-lite \
        --reference-source nevo --top-k 20 \
        --price-per-1m 0.06 --output-dir /tmp/altera-quality

No ``pip install``, no ``PYTHONPATH``, no ``scripts/`` file required.

It runs the V2 rules+embeddings NEVO pipeline for one or more embedding
models over a fixture and prints a comparison table (coverage,
high-confidence false positives, forbidden rejection, top-k recall,
abstain rate, embedding calls, token usage, estimated cost). Per-model
candidate + mismatch CSVs are written to a writable output dir
(default ``/tmp/altera-quality`` — ``/app`` may be read-only in Render).

OFFLINE-SAFE / EVALUATOR-ONLY
-----------------------------
* ``fake`` runs with no key, no network, no SDK — the CI/default path.
* ``voyage-*`` models require BOTH ``ALTERA_ENABLE_EMBEDDINGS=true`` and
  ``VOYAGE_API_KEY``; without them they are SKIPPED with a clear reason
  (or FATAL under ``--require-voyage``). Nothing here is wired into a
  production route; V1 remains the default pipeline and embeddings stay
  disabled by default.
* No secrets are printed — only token counts and an estimated cost.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from altera_api.classification_v2.evaluation import (
    load_fixture,
    nevo_gates,
    write_mismatches_csv,
)
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
    write_candidates_csv,
)
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.embeddings.provider import (
    EmbeddingProviderError,
    build_embedding_provider,
)
from altera_api.quality_config import embeddings_enabled

_DEFAULT_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"
#: ``/app`` is frequently read-only in the Render runtime image, so the
#: benchmark defaults to a writable temp dir.
_DEFAULT_OUTPUT_DIR = "/tmp/altera-quality"


def _pct(x: float | None) -> str:
    return f"{x*100:5.1f}%" if x is not None else "   —  "


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.benchmark_nevo_embeddings",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--models", default="fake",
        help="comma list: fake, voyage-4, voyage-4-lite, …",
    )
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE)
    ap.add_argument(
        "--reference-source", choices=["fixture", "nevo"], default="fixture"
    )
    ap.add_argument("--reference", default=None, help="explicit reference path")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument(
        "--price-per-1m", type=float, default=0.06,
        help="USD per 1M tokens (estimate; configure per model).",
    )
    # ``--output-dir`` is canonical; ``--out-dir`` kept as a back-compat
    # alias for the old top-level script invocation.
    ap.add_argument(
        "--output-dir", "--out-dir", dest="output_dir",
        default=_DEFAULT_OUTPUT_DIR,
        help="writable dir for the candidate/mismatch CSVs "
             f"(default {_DEFAULT_OUTPUT_DIR}).",
    )
    ap.add_argument(
        "--require-voyage", action="store_true",
        help="fail (non-zero) if a voyage model cannot run (embeddings "
             "disabled, missing key, or SDK absent).",
    )
    return ap


def _resolve_provider(model: str, *, require_voyage: bool) -> tuple[Any | None, str | None]:
    """Build the provider for ``model`` or return a clear skip reason.

    Returns ``(provider, None)`` on success or ``(None, reason)`` when the
    model cannot run. ``fake`` always succeeds offline; ``voyage-*`` needs
    embeddings enabled AND a key AND the SDK.
    """
    if model == "fake":
        return build_embedding_provider("fake"), None

    # A voyage model is requested. It must be explicitly enabled — the
    # benchmark never silently makes network calls.
    if not embeddings_enabled():
        return None, (
            "embeddings are disabled — set ALTERA_ENABLE_EMBEDDINGS=true to "
            "run the voyage provider."
        )
    try:
        provider = build_embedding_provider("voyage", model=model)
    except EmbeddingProviderError as exc:
        # Clear, secret-free messages (missing key / SDK) come straight
        # from the provider.
        return None, str(exc)
    return provider, None


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    cases = load_fixture(args.fixture)
    references = load_nevo_reference(args.reference_source, path=args.reference)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("# NEVO embeddings benchmark")
    print(f"  fixture={args.fixture} ({len(cases)} cases)")
    print(
        f"  reference={args.reference_source} ({len(references)} foods)  "
        f"top_k={args.top_k}"
    )
    print(f"  output-dir={out_dir} (writable; CSVs are not committed)\n")

    table: list[tuple[str, dict[str, Any] | None]] = []
    gate_failed = False
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        provider_name = "fake" if model == "fake" else "voyage"
        provider, skip_reason = _resolve_provider(
            model, require_voyage=args.require_voyage
        )
        if provider is None:
            if args.require_voyage:
                print(f"FATAL: {model}: {skip_reason}")
                return 1
            print(f"  [skip] {model}: {skip_reason}")
            table.append((model, None))
            continue

        m, rows = evaluate_nevo_embeddings(
            cases, references, provider,
            provider_name=provider_name, top_k=args.top_k,
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
    hdr = (
        f"{'Model':14} {'Coverage':>9} {'HC-FP':>6} {'Forbid-rej':>11} "
        f"{'top1':>7} {'top5':>7} {'top20':>7} {'abstain':>8} {'calls':>6} "
        f"{'tokens':>8} {'cost$':>8}"
    )
    print(hdr)
    print("-" * 96)
    for model, d in table:
        if d is None:
            print(f"{model:14} {'(skipped — see reason above)':>40}")
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
    print(f"CSVs written to {out_dir}/.")
    print("Cost is an ESTIMATE — set --price-per-1m to the model's real price.")
    return 1 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

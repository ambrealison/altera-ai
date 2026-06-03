"""Phase Quality-V2-D/E — NEVO embeddings benchmark, package CLI.

Runnable as a module so it works inside the Render runtime image, which
copies ``altera_api/`` but NOT the top-level ``scripts/`` directory.

Recommended full-NEVO run (voyage-4-lite is the default model)::

    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
    python -m altera_api.classification_v2.benchmark_nevo_embeddings \
        --models voyage-4-lite --reference-source nevo --top-k 20 \
        --batch-size 64 --cache-dir /tmp/altera-quality/cache \
        --output-dir /tmp/altera-quality

Quick smoke (subset, still real Voyage)::

    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
    python -m altera_api.classification_v2.benchmark_nevo_embeddings \
        --models voyage-4-lite --reference-source nevo \
        --limit-references 200 --limit-cases 10 --top-k 20 \
        --batch-size 64 --output-dir /tmp/altera-quality

Phase V2-E makes the full-NEVO run usable on Render: references embed in
BATCHES (one call per batch), progress is printed and flushed, a
PERSISTENT cache (``--cache-dir``) lets an interrupted run resume without
re-embedding, ``--limit-references``/``--limit-cases`` enable fast smokes,
and a Voyage rate-limit prints a friendly message + non-zero exit (with
the cache kept intact).

OFFLINE-SAFE / EVALUATOR-ONLY
-----------------------------
* ``fake`` runs with no key, no network, no SDK — the CI/default path.
* ``voyage-*`` models require BOTH ``ALTERA_ENABLE_EMBEDDINGS=true`` and
  ``VOYAGE_API_KEY``; otherwise they are SKIPPED with a clear reason (or
  FATAL under ``--require-voyage``). Nothing here is wired into a
  production route; V1 remains the default pipeline.
* No secrets are printed — only token counts and an estimated cost.
"""

from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from typing import Any

from altera_api.classification_v2.evaluation import (
    load_fixture,
    nevo_gates,
    write_mismatches_csv,
)
from altera_api.classification_v2.nevo_diagnostics import (
    build_diagnosis_rows,
    inspect_rank_misses,
    print_console_diagnostics,
    print_rank_inspection,
    write_failure_reports,
    write_rank_inspection_reports,
)
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
    write_candidates_csv,
)
from altera_api.classification_v2.nevo_index import BuildProgress, NevoVectorIndex
from altera_api.embeddings.cache import FileEmbeddingCache, InMemoryEmbeddingCache
from altera_api.embeddings.provider import (
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    build_embedding_provider,
)
from altera_api.quality_config import embeddings_enabled

_DEFAULT_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"
#: ``/app`` is frequently read-only in the Render runtime image, so the
#: benchmark defaults to writable temp dirs.
_DEFAULT_OUTPUT_DIR = "/tmp/altera-quality"
_DEFAULT_CACHE_DIR = "/tmp/altera-quality/cache"
#: Print a query-progress line at most this often (plus the final one).
_QUERY_PROGRESS_EVERY = 25


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
        help="comma list: fake, voyage-4-lite, voyage-4, …",
    )
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE)
    ap.add_argument(
        "--reference-source", choices=["fixture", "nevo"], default="fixture"
    )
    ap.add_argument("--reference", default=None, help="explicit reference path")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64,
                    help="document embedding batch size (default 64).")
    ap.add_argument("--limit-references", type=int, default=None,
                    help="cap reference foods (fast smoke).")
    ap.add_argument("--limit-cases", type=int, default=None,
                    help="cap fixture cases (fast smoke).")
    ap.add_argument(
        "--cache-dir", default=_DEFAULT_CACHE_DIR,
        help="persistent embedding cache dir (resumable). Empty string "
             f"disables the on-disk cache. Default {_DEFAULT_CACHE_DIR}.",
    )
    ap.add_argument(
        "--price-per-1m", type=float, default=0.06,
        help="USD per 1M tokens (estimate; configure per model).",
    )
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
    ap.add_argument(
        "--debug", action="store_true",
        help="print full tracebacks on provider/rate-limit errors.",
    )
    return ap


def _resolve_provider(model: str) -> tuple[Any | None, str | None]:
    """Build the provider for ``model`` or return a clear skip reason."""
    if model == "fake":
        return build_embedding_provider("fake"), None
    if not embeddings_enabled():
        return None, (
            "embeddings are disabled — set ALTERA_ENABLE_EMBEDDINGS=true to "
            "run the voyage provider."
        )
    try:
        provider = build_embedding_provider("voyage", model=model)
    except EmbeddingProviderError as exc:
        return None, str(exc)
    return provider, None


def _make_cache(cache_dir: str, provider_name: str, model: str) -> Any:
    if not cache_dir:
        return InMemoryEmbeddingCache()
    slug = f"{provider_name}-{model}".replace("/", "_").replace(".", "_")
    return FileEmbeddingCache(Path(cache_dir) / f"embeddings-{slug}.json")


def _run_model(
    model: str,
    cases: list[dict[str, Any]],
    references: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Build the index (batched, cached, observable) then evaluate."""
    provider_name = "fake" if model == "fake" else "voyage"
    provider, skip_reason = _resolve_provider(model)
    if provider is None:
        raise _SkipModel(skip_reason or "unavailable")

    cache = _make_cache(args.cache_dir, provider_name, model)
    t0 = time.monotonic()

    def build_progress(ev: BuildProgress) -> None:
        if ev.stage == "start":
            print(
                f"[model {model}] embedding {ev.references} references in "
                f"{ev.batches} batches of {ev.batch_size}",
                flush=True,
            )
            if ev.batches == 0:
                print(
                    f"[model {model}] 0 to embed — all references served "
                    "from cache",
                    flush=True,
                )
        else:
            elapsed = time.monotonic() - t0
            print(
                f"[model {model}] docs batch {ev.batch_index}/{ev.batches} done "
                f"· {ev.embedded}/{ev.to_embed} · elapsed {elapsed:.1f}s",
                flush=True,
            )

    index = NevoVectorIndex.load_or_build(
        references,
        provider=provider, provider_name=provider_name, top_k=args.top_k,
        cache=cache, batch_size=args.batch_size, progress=build_progress,
    )
    print(
        f"[model {model}] cache hits {getattr(cache, 'hits', 0)} · "
        f"misses {getattr(cache, 'misses', 0)}",
        flush=True,
    )
    print(f"[model {model}] evaluating {len(cases)} queries", flush=True)

    def query_progress(done: int, total: int) -> None:
        if done == total or done % _QUERY_PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - t0
            print(
                f"[model {model}] queries {done}/{total} · elapsed "
                f"{elapsed:.1f}s",
                flush=True,
            )

    decisions: list[tuple[dict[str, Any], Any]] = []
    m, rows = evaluate_nevo_embeddings(
        cases, references, provider,
        provider_name=provider_name, top_k=args.top_k,
        model=model, index=index, query_progress=query_progress,
        decisions_sink=decisions,
    )
    cache.flush()

    out_dir = Path(args.output_dir)
    slug = model.replace(".", "_")
    write_candidates_csv(out_dir / f"nevo_candidates_{slug}.csv", rows)
    write_mismatches_csv(out_dir / f"nevo_mismatches_{slug}.csv", m.mismatches)

    # Phase Quality-V2-F — focused failure reports + console diagnostics.
    diag_rows = build_diagnosis_rows(decisions, references)
    diag_counts = write_failure_reports(out_dir, model, diag_rows)
    print_console_diagnostics(diag_rows)

    # Phase Quality-V2-G — inspect rank-misses + retrieved-but-rejected
    # (pre-reranker analysis; counts mirror the taxonomy's rank-2–20 +
    # retrieved-but-rejected buckets).
    rank_miss_rows, rejected_rows = inspect_rank_misses(decisions)
    rank_counts = write_rank_inspection_reports(
        out_dir, model, rank_miss_rows, rejected_rows
    )
    print_rank_inspection(rank_miss_rows, rejected_rows)

    print(
        f"\n[model {model}] reports: "
        + ", ".join(f"{k}={v}" for k, v in {**diag_counts, **rank_counts}.items()),
        flush=True,
    )

    tax = summarize_candidates(cases, rows, references)
    gates = nevo_gates(m)
    d = m.as_dict()
    d["cost"] = round(m.token_total / 1_000_000 * args.price_per_1m, 4)
    d["taxonomy"] = tax
    d["gates"] = gates
    elapsed = time.monotonic() - t0
    print(
        f"[done] {model} gates={gates['passed']} · elapsed {elapsed:.1f}s · "
        f"taxonomy={tax}",
        flush=True,
    )
    return d


class _SkipModel(Exception):
    """A model that cannot run (no key / SDK / embeddings disabled)."""


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    cases = load_fixture(args.fixture)
    references = load_nevo_reference_with_limit(args)
    if args.limit_cases is not None:
        cases = cases[: args.limit_cases]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("# NEVO embeddings benchmark")
    print(f"  fixture={args.fixture} ({len(cases)} cases)")
    print(
        f"  reference={args.reference_source} ({len(references)} foods)  "
        f"top_k={args.top_k}  batch_size={args.batch_size}"
    )
    print(f"  output-dir={out_dir}  cache-dir={args.cache_dir or '(disabled)'}\n")

    table: list[tuple[str, dict[str, Any] | None]] = []
    gate_failed = False
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        try:
            d = _run_model(model, cases, references, args)
        except _SkipModel as skip:
            if args.require_voyage:
                print(f"FATAL: {model}: {skip}")
                return 1
            print(f"  [skip] {model}: {skip}")
            table.append((model, None))
            continue
        except EmbeddingRateLimitError as exc:
            print(
                f"\nRATE LIMIT: {model}: {exc}\n"
                "The on-disk cache was kept — re-run the SAME command to "
                "resume from where it stopped (already-embedded batches are "
                "served from the cache).",
                flush=True,
            )
            if args.debug:
                traceback.print_exc()
            return 2
        except EmbeddingProviderError as exc:
            print(f"\nERROR: {model}: {exc}", flush=True)
            if args.debug:
                traceback.print_exc()
            return 2
        if not d["gates"]["passed"]:
            gate_failed = True
        table.append((model, d))

    _print_table(table, out_dir)
    return 1 if gate_failed else 0


def load_nevo_reference_with_limit(args: argparse.Namespace) -> list[dict[str, Any]]:
    from altera_api.classification_v2.nevo_index import load_nevo_reference

    refs = load_nevo_reference(args.reference_source, path=args.reference)
    if args.limit_references is not None:
        refs = refs[: args.limit_references]
    return refs


def _print_table(
    table: list[tuple[str, dict[str, Any] | None]], out_dir: Path
) -> None:
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


if __name__ == "__main__":
    raise SystemExit(main())

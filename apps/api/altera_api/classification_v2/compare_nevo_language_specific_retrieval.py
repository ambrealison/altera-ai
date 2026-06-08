"""Phase Quality-V2-AI — language-specific NEVO retrieval benchmark (CLI).

The raw mixed EN+FR+DE multilingual reference degrades retrieval globally. The
next hypothesis: a retailer declares the upload language, and retrieval uses a
*language-only* auxiliary index for that language (FR-only or DE-only), with the
canonical English index remaining the default/canonical decision path. The
language index is auxiliary, never a replacement — the conservative decision
layer only accepts a language candidate when it clearly + safely rescues a
baseline failure.

Read-only experiment: builds a baseline EN index + a language-only index from the
generated multilingual reference, runs both over the project's products, then
applies the existing conservative decision layer between baseline and the
language candidate. No DB writes; no routes; V1 default; embeddings opt-in only.

    ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \
    python -m altera_api.classification_v2.\
compare_nevo_language_specific_retrieval \
        --project-id <uuid> --reference-source nevo \
        --language-reference /tmp/altera-quality/nevo_reference_multilingual.csv \
        --retailer-language fr --output-dir /tmp/altera-quality --top-k 20 \
        --cache-dir /tmp/altera-quality/cache --require-voyage

Missing-language strategy: NEVO rows without a name in the requested language are
EXCLUDED from the language index (mixing canonical EN into a FR-only index would
re-introduce the mixed-language noise). The count is reported as
``language_reference_rows_missing`` and folded into ``language_reference_coverage``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from altera_api.classification_v2 import compare_nevo_multilingual_retrieval as bench
from altera_api.classification_v2 import nevo_multilingual_conservative as conservative
from altera_api.classification_v2 import nevo_v2_project_batch_dry_run as proj
from altera_api.classification_v2.apply_nevo_v2_plan import _s
from altera_api.classification_v2.compare_nevo_v1_v2 import _make_cache
from altera_api.classification_v2.nevo_index import NevoVectorIndex
from altera_api.classification_v2.nevo_matcher import get_nevo_matcher
from altera_api.classification_v2.nevo_multilingual_reference import (
    build_language_reference_text,
    language_name_present,
    load_multilingual_nevo_reference,
    multilingual_reference_checksum,
)
from altera_api.classification_v2.nevo_v2_batch_dry_run import (
    DEFAULT_EMBEDDING_MODEL,
    _build_matcher,
    _write_csv,
)
from altera_api.embeddings.provider import build_embedding_provider
from altera_api.quality_config import embeddings_enabled

LANGUAGE_CSV_COLUMNS = [
    "product_id", "product_name",
    "baseline_bucket", "language_bucket", "conservative_bucket",
    "baseline_nevo_code", "language_nevo_code", "conservative_nevo_code",
    "baseline_top1", "language_top1", "conservative_top1",
    "baseline_confidence", "language_confidence", "conservative_confidence",
    "baseline_matches_existing_v2", "language_matches_existing_v2",
    "conservative_matches_existing_v2",
    "conservative_decision", "conservative_reason",
    "retailer_language", "language_reference_coverage",
    "language_reference_rows_used", "language_reference_rows_missing",
]


def _base_args(args: argparse.Namespace) -> SimpleNamespace:
    """Args namespace for the canonical EN baseline matcher (no multilingual)."""
    return SimpleNamespace(
        evaluator_fake=args.evaluator_fake, require_voyage=args.require_voyage,
        embedding_model=args.embedding_model,
        reference_source=args.reference_source, reference=None,
        multilingual_reference=None, cache_dir=args.cache_dir,
        top_k=args.top_k, batch_size=args.batch_size, debug=args.debug)


def _build_language_matcher(args: argparse.Namespace, *, language: str,
                            provider_name: str, model: str,
                            used_refs: list[dict[str, Any]]) -> Any:
    """A v2-embeddings matcher over a language-only auxiliary index."""
    provider = (build_embedding_provider("voyage", model=model)
                if embeddings_enabled() else build_embedding_provider("fake"))
    tag = f"lang-{language}-" + multilingual_reference_checksum(used_refs)
    cache = _make_cache(args.cache_dir, provider_name, model, tag)
    index = NevoVectorIndex.load_or_build(
        used_refs, provider=provider, provider_name=provider_name,
        top_k=args.top_k, cache=cache, batch_size=args.batch_size,
        text_builder=lambda r: build_language_reference_text(r,
                                                             language=language))
    cache.flush()
    return get_nevo_matcher("v2-embeddings", index=index,
                            evaluator_mode=args.evaluator_fake)


def language_recommendation(cons_summary: dict[str, Any], *,
                            coverage: float | None) -> str:
    base = cons_summary["baseline_counts"]
    consc = cons_summary["conservative_counts"]
    agree = cons_summary["agreement_with_existing_v2"]
    regressed = cons_summary["conservative_regressed_count"]
    improved = cons_summary["conservative_improved_count"]
    tol = max(1, math.ceil(0.02 * max(agree["baseline"], 1)))
    agreement_ok = agree["conservative"] >= agree["baseline"] - tol

    if (regressed > 0 or consc["true_high_risk"] > base["true_high_risk"]
            or not agreement_ok):
        return "reject_due_to_regressions"
    if (coverage is not None and coverage >= 0.5
            and consc["auto_ready"] >= base["auto_ready"]
            and consc["no_match"] <= base["no_match"] and improved >= 1):
        return "adopt_language_specific_candidate"
    if coverage is not None and coverage < 0.5:
        return "needs_more_coverage"
    return "neutral_no_lift"


def _language_rows(cons_rows: list[dict[str, Any]], *, language: str,
                   coverage: float, used: int, missing: int,
                   ) -> list[dict[str, Any]]:
    out = []
    for r in cons_rows:
        out.append({
            "product_id": _s(r["product_id"]),
            "product_name": _s(r["product_name"]),
            "baseline_bucket": r["baseline_bucket"],
            "language_bucket": r["multilingual_bucket"],
            "conservative_bucket": r["conservative_bucket"],
            "baseline_nevo_code": r["baseline_nevo_code"],
            "language_nevo_code": r["multilingual_nevo_code"],
            "conservative_nevo_code": r["conservative_nevo_code"],
            "baseline_top1": r["baseline_top1"],
            "language_top1": r["multilingual_top1"],
            "conservative_top1": r["conservative_top1"],
            "baseline_confidence": r["baseline_confidence"],
            "language_confidence": r["multilingual_confidence"],
            "conservative_confidence": r["conservative_confidence"],
            "baseline_matches_existing_v2": r["baseline_matches_existing_v2"],
            "language_matches_existing_v2": r["multilingual_matches_existing_v2"],
            "conservative_matches_existing_v2": r[
                "conservative_matches_existing_v2"],
            "conservative_decision": r["conservative_decision"],
            "conservative_reason": r["conservative_reason"],
            "retailer_language": language,
            "language_reference_coverage": coverage,
            "language_reference_rows_used": used,
            "language_reference_rows_missing": missing,
        })
    return out


def _build_summary(*, project_id: str, language: str, reference_source: str,
                   language_reference_path: str, total: int, used: int,
                   missing: int, coverage: float, raw_summary: dict[str, Any],
                   cons_summary: dict[str, Any]) -> dict[str, Any]:
    base = cons_summary["baseline_counts"]
    raw_lang = cons_summary["raw_multilingual_counts"]
    cons = cons_summary["conservative_counts"]
    agree = cons_summary["agreement_with_existing_v2"]

    def trio(bucket: str) -> dict[str, int]:
        return {f"baseline_{bucket}": base[bucket],
                f"raw_language_{bucket}": raw_lang[bucket],
                f"conservative_{bucket}": cons[bucket]}

    summary: dict[str, Any] = {
        "phase": "quality-v2-ai",
        "project_id": project_id,
        "retailer_language": language,
        "reference_source": reference_source,
        "language_reference_path": language_reference_path,
        "language_reference_rows_total": total,
        "language_reference_rows_used": used,
        "language_reference_rows_missing": missing,
        "language_reference_coverage": coverage,
        "baseline_counts": base,
        "raw_language_counts": raw_lang,
        "conservative_counts": cons,
    }
    for bucket in ("auto_ready", "safety_downgrade", "needs_review", "no_match",
                   "true_high_risk"):
        summary.update(trio(bucket))
    summary.update({
        "true_high_risk_delta": cons_summary["true_high_risk_delta"],
        "raw_language_rows_improved": raw_summary["rows_improved"],
        "raw_language_rows_regressed": raw_summary["rows_regressed"],
        "conservative_switch_count": cons_summary["conservative_switch_count"],
        "conservative_kept_baseline_count": cons_summary[
            "conservative_kept_baseline_count"],
        "conservative_blocked_regression_count": cons_summary[
            "conservative_blocked_regression_count"],
        "conservative_improved_count": cons_summary[
            "conservative_improved_count"],
        "conservative_regressed_count": cons_summary[
            "conservative_regressed_count"],
        "baseline_existing_v2_agreement": agree["baseline"],
        "raw_language_existing_v2_agreement": agree["raw"],
        "conservative_existing_v2_agreement": agree["conservative"],
        "recommendation": language_recommendation(cons_summary,
                                                  coverage=coverage),
    })
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "compare_nevo_language_specific_retrieval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"],
                    default="nevo")
    ap.add_argument("--language-reference", required=True)
    ap.add_argument("--retailer-language", choices=["fr", "de", "en"],
                    required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--cache-dir", default="/tmp/altera-quality/cache")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-products", type=int, default=None)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--require-voyage", action="store_true")
    ap.add_argument("--evaluator-fake", action="store_true")
    ap.add_argument("--min-confidence", type=float,
                    default=conservative.DEFAULT_MIN_CONFIDENCE)
    ap.add_argument("--allow-language-overwrite-auto-ready",
                    action="store_true")
    ap.add_argument("--debug", action="store_true")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    language = args.retailer_language
    if store is None:
        from altera_api.api.store_factory import get_store
        store = get_store()

    project_id = UUID(_s(args.project_id))
    store.get_project(project_id)
    products = list(store.list_products_for_project(project_id))
    if args.limit_products:
        products = products[: args.limit_products]
    records = list(store.list_enrichment_records_for_project(project_id))
    groups = proj.dedupe_products(products, enabled=True)
    existing = proj._existing_v2_index(records)

    # 1) baseline canonical EN matcher (default decision path, unchanged).
    base_built = _build_matcher(_base_args(args))
    if isinstance(base_built, int):
        return base_built
    base_matcher, provider_name, model = base_built

    # 2) language-only auxiliary index (missing-language rows excluded).
    all_refs = load_multilingual_nevo_reference(args.language_reference)
    used_refs = [r for r in all_refs if language_name_present(r, language)]
    total, used = len(all_refs), len(used_refs)
    missing = total - used
    coverage = round(used / total, 4) if total else 0.0
    if not used_refs:
        print(f"FATAL: no NEVO rows have a {language!r} name in "
              f"{args.language_reference} — nothing to compare.")
        return 2
    lang_matcher = _build_language_matcher(
        args, language=language, provider_name=provider_name, model=model,
        used_refs=used_refs)

    # 3) run both over the same products.
    base_results = proj.build_results(groups, matcher=base_matcher,
                                      top_k=args.top_k, existing=existing)
    lang_results = proj.build_results(groups, matcher=lang_matcher,
                                      top_k=args.top_k, existing=existing)

    # 4) raw language metrics, then 5) conservative decision layer.
    raw = bench.compare(baseline_rows=base_results,
                        multilingual_rows=lang_results)
    cons = conservative.conservative_decisions(
        raw["rows"], coverage=coverage,
        allow_overwrite_auto_ready=args.allow_language_overwrite_auto_ready,
        min_confidence=args.min_confidence)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = _s(args.project_id)
    stem = f"nevo_language_specific_retrieval_{language}_{pid}"
    rows = _language_rows(cons["rows"], language=language, coverage=coverage,
                          used=used, missing=missing)
    csv_path = out_dir / f"{stem}.csv"
    _write_csv(csv_path, LANGUAGE_CSV_COLUMNS, rows)
    summary = _build_summary(
        project_id=pid, language=language,
        reference_source=args.reference_source,
        language_reference_path=str(args.language_reference), total=total,
        used=used, missing=missing, coverage=coverage,
        raw_summary=raw["summary"], cons_summary=cons["summary"])
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print("# NEVO language-specific retrieval (read-only — no DB writes)")
    print(f"  project={pid} retailer_language={language} "
          f"provider={provider_name}")
    print(f"  language reference: used={used}/{total} missing={missing} "
          f"coverage={coverage}")
    print(f"  baseline      ={summary['baseline_counts']}")
    print(f"  raw_language  ={summary['raw_language_counts']}")
    print(f"  conservative  ={summary['conservative_counts']}")
    print(f"  switches={summary['conservative_switch_count']} "
          f"blocked_regressions={summary['conservative_blocked_regression_count']}"
          f" improved={summary['conservative_improved_count']} "
          f"regressed={summary['conservative_regressed_count']} "
          f"true_high_risk_delta={summary['true_high_risk_delta']}")
    print(f"  agreement baseline={summary['baseline_existing_v2_agreement']} "
          f"raw={summary['raw_language_existing_v2_agreement']} "
          f"conservative={summary['conservative_existing_v2_agreement']}")
    print(f"  RECOMMENDATION: {summary['recommendation']}")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

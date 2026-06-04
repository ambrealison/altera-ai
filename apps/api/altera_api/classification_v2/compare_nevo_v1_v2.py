"""Phase Quality-V2-I — read-only NEVO V1-vs-V2 shadow comparison.

Compares the current PRODUCTION V1 NEVO matcher (the deterministic
``NevoProvider`` path) against the offline V2 embeddings matcher on the
real products of a project, and writes comparison CSVs under
``/tmp/altera-quality``. It is STRICTLY READ-ONLY: it reads the project,
its products and the NEVO reference, and never calls any store write
method — no enrichment records, no classifications, no runs, no review
items, no product updates.

    python -m altera_api.classification_v2.compare_nevo_v1_v2 \
        --project-id <uuid> --output-dir /tmp/altera-quality \
        --top-k 20 --cache-dir /tmp/altera-quality/cache

The V2 matcher runs in evaluator/dev mode. It uses the real Voyage
provider only when ``ALTERA_ENABLE_EMBEDDINGS=true`` (+ ``VOYAGE_API_KEY``);
otherwise it falls back to the deterministic FAKE provider (a present
``VOYAGE_API_KEY`` alone changes nothing). No commercial field is ever
embedded — only the product descriptor (name / category / ingredients /
labels). Nothing here is wired into a production route; V1 stays the
production default and embeddings stay disabled by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from altera_api.classification_v2.nevo_index import NevoVectorIndex, load_nevo_reference
from altera_api.classification_v2.nevo_matcher import get_nevo_matcher
from altera_api.classification_v2.nevo_rules import (
    _primary_head,
    _significant_tokens,
    concept_of,
)
from altera_api.embeddings.cache import FileEmbeddingCache, InMemoryEmbeddingCache
from altera_api.embeddings.provider import (
    EmbeddingProviderError,
    build_embedding_provider,
)
from altera_api.enrichment.providers.nevo import NevoProvider
from altera_api.quality_config import DEFAULT_EMBEDDING_MODEL, embeddings_enabled

_DEFAULT_OUTPUT_DIR = "/tmp/altera-quality"
_DEFAULT_CACHE_DIR = "/tmp/altera-quality/cache"

COMPARISON_CSV_COLUMNS = [
    "product_id", "product_name", "retailer_category", "retailer_subcategory",
    "ingredients_present", "v1_outcome", "v1_reference_name", "v1_reference_code",
    "v1_confidence", "v1_notes", "v2_outcome", "v2_reference_name",
    "v2_reference_code", "v2_confidence", "v2_match_type", "v2_review_required",
    "v2_top_5_candidates", "v2_rejection_reasons_summary", "agreement_bucket",
    "risk_bucket", "notes",
]


# ---------------------------------------------------------------------------
# Pure bucket logic (unit-tested).
# ---------------------------------------------------------------------------
def _specificity(v1_name: str, v2_name: str) -> str | None:
    t1 = set(_significant_tokens(v1_name or ""))
    t2 = set(_significant_tokens(v2_name or ""))
    if t2 > t1:
        return "v2_more_specific"
    if t1 > t2:
        return "v1_more_specific"
    return None


def agreement_bucket(
    *,
    v1_matched: bool,
    v1_code: str | None,
    v1_name: str | None,
    v2_matched: bool,
    v2_code: str | None,
    v2_name: str | None,
) -> str:
    if not v1_matched and not v2_matched:
        return "both_no_match"
    if v1_matched and not v2_matched:
        return "v1_only"
    if v2_matched and not v1_matched:
        return "v2_only"
    # Both produced a candidate.
    if v1_code and v2_code and str(v1_code) == str(v2_code):
        return "same_code"
    c1 = concept_of(v1_name or "")
    c2 = concept_of(v2_name or "")
    if c1 is not None and c1 == c2:
        return "same_concept"
    return "disagreement_needs_review"


def _v1_off_concept(product_name: str | None, v1_name: str | None) -> bool:
    """True when the product resolves to a concept that V1's reference does
    NOT share — i.e. V1 likely matched the wrong food (a false positive)."""
    pc = concept_of(product_name or "")
    if not v1_name or pc is None:
        return False
    return concept_of(v1_name) != pc


def _v2_on_concept(
    product_name: str | None, v2_name: str | None, v2_matched: bool
) -> bool:
    """True when V2 matched a reference that shares the product's concept."""
    pc = concept_of(product_name or "")
    return bool(v2_matched) and pc is not None and concept_of(v2_name or "") == pc


def _heads_agree(product_name: str | None, v2_name: str | None) -> bool:
    """True when the product and V2's reference lead with the same head
    token (e.g. "Biscuits Apéritif" ↔ "Biscuits assorted") — a safe exact
    match even when neither resolves to a mapped concept."""
    ph = _primary_head(product_name or "")
    ch = _primary_head(v2_name or "")
    return ph is not None and ph == ch


def risk_bucket(
    *,
    agreement: str,
    product_name: str | None,
    v1_name: str | None,
    v2_name: str | None,
    v2_matched: bool,
    v2_review_required: bool,
) -> str:
    if agreement == "same_code":
        return "safe_agreement"
    if agreement == "same_concept":
        return _specificity(v1_name or "", v2_name or "") or "safe_agreement"
    if agreement == "both_no_match":
        return "safe_agreement"
    if v2_review_required:
        # V2 surfaced a review-only candidate — a human will confirm it.
        return "v2_review_only"
    # Phase Quality-V2-J/K — a V2 auto-accept is gate-validated: it shares
    # the product's concept OR exactly matches its head token. Such a match,
    # where V1 matched a different concept (or nothing), is a V2 win — not a
    # potential false positive.
    v2_consistent = bool(v2_matched) and (
        _v2_on_concept(product_name, v2_name, v2_matched)
        or _heads_agree(product_name, v2_name)
    )
    if v2_consistent and (
        _v1_off_concept(product_name, v1_name)
        or agreement in ("v2_only", "disagreement_needs_review")
    ):
        return "v2_better_than_v1"
    if v2_matched and not v2_consistent:
        # V2 accepted something that is neither concept- nor head-consistent
        # with the product (rare given the gate) → inspect.
        return "v2_potential_false_positive"
    return "manual_inspection_needed"  # e.g. v1_only (V2 missed it)


# ---------------------------------------------------------------------------
# V1 + V2 single-product matching (read-only).
# ---------------------------------------------------------------------------
def _v1_match(product: Any, nevo: NevoProvider) -> dict[str, Any]:
    """Current production V1 NEVO match (deterministic path only — no LLM,
    no cost, no writes)."""
    result = nevo.match(
        food_name=product.product_name,
        food_group=product.retailer_category,
    )
    if result is None:
        return {"matched": False, "name": "", "code": "", "confidence": "",
                "notes": "no_match"}
    return {
        "matched": True,
        "name": result.entry.food_name_en,
        "code": result.entry.nevo_code,
        "confidence": float(result.confidence),
        "notes": result.match_type,
    }


def _v2_query(product: Any) -> dict[str, Any]:
    # Descriptor-only — never any commercial field (enforced by the builder).
    labels = list(product.labels) if getattr(product, "labels", None) else None
    return {
        "product_name": product.product_name,
        "retailer_category": product.retailer_category,
        "ingredients_text": product.ingredients_text,
        "labels": labels,
    }


def _v2_match(product: Any, matcher: Any, top_k: int) -> dict[str, Any]:
    decision = matcher.decide(_v2_query(product), top_k=top_k)
    if not decision.matched:
        outcome = "no_match"
    elif decision.review_required:
        outcome = "review"
    else:
        outcome = "match"
    top5 = list(decision.top_candidates)[:5]
    rejections = sorted({t.rejection_reason for t in top5 if t.rejection_reason})
    return {
        "matched": decision.matched,
        "outcome": outcome,
        "name": decision.food_name_en or "",
        "code": decision.nevo_code or "",
        "confidence": round(decision.confidence, 4),
        "match_type": decision.match_type,
        "review_required": decision.review_required,
        "top_5": " | ".join(t.candidate_name for t in top5),
        "rejections": " ;; ".join(rejections),
    }


def build_comparison_rows(
    products: list[Any], nevo: NevoProvider, matcher: Any, *, top_k: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in products:
        v1 = _v1_match(p, nevo)
        v2 = _v2_match(p, matcher, top_k)
        agreement = agreement_bucket(
            v1_matched=v1["matched"], v1_code=v1["code"], v1_name=v1["name"],
            v2_matched=v2["matched"], v2_code=v2["code"], v2_name=v2["name"],
        )
        risk = risk_bucket(
            agreement=agreement, product_name=p.product_name,
            v1_name=v1["name"], v2_name=v2["name"],
            v2_matched=v2["matched"], v2_review_required=v2["review_required"],
        )
        note_parts: list[str] = []
        if risk == "v2_better_than_v1":
            note_parts.append("V2 own-concept match")
        if _v1_off_concept(p.product_name, v1["name"]):
            note_parts.append(
                f"V1 likely false positive (V1 concept "
                f"{concept_of(v1['name'])!r} != product concept "
                f"{concept_of(p.product_name)!r})"
            )
        rows.append(
            {
                "product_id": str(p.id),
                "product_name": p.product_name,
                "retailer_category": p.retailer_category or "",
                "retailer_subcategory": getattr(p, "retailer_subcategory", None) or "",
                "ingredients_present": bool(getattr(p, "ingredients_text", None)),
                "v1_outcome": "match" if v1["matched"] else "no_match",
                "v1_reference_name": v1["name"],
                "v1_reference_code": v1["code"],
                "v1_confidence": v1["confidence"],
                "v1_notes": v1["notes"],
                "v2_outcome": v2["outcome"],
                "v2_reference_name": v2["name"],
                "v2_reference_code": v2["code"],
                "v2_confidence": v2["confidence"],
                "v2_match_type": v2["match_type"],
                "v2_review_required": v2["review_required"],
                "v2_top_5_candidates": v2["top_5"],
                "v2_rejection_reasons_summary": v2["rejections"],
                "agreement_bucket": agreement,
                "risk_bucket": risk,
                "notes": " ;; ".join(note_parts),
            }
        )
    return rows


def write_comparison_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COMPARISON_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Phase Quality-V2-N — readiness summary + recommendation.
# ---------------------------------------------------------------------------
_AGREEMENT_BUCKETS = (
    "same_code", "same_concept", "v1_only", "v2_only", "both_no_match",
    "disagreement_needs_review",
)
_RISK_BUCKETS = (
    "safe_agreement", "v2_more_specific", "v1_more_specific", "v2_review_only",
    "v2_better_than_v1", "v2_potential_false_positive", "manual_inspection_needed",
)
#: filtered CSV name → row predicate.
_FILTERED_SPECS: dict[str, Any] = {
    "nevo_v2_better_than_v1": lambda r: r["risk_bucket"] == "v2_better_than_v1",
    "nevo_v2_review_only": lambda r: r["v2_outcome"] == "review",
    "nevo_v2_high_risk": lambda r: r["risk_bucket"] == "v2_potential_false_positive",
}


def _recommendation(
    *,
    product_count: int,
    potential_high_risk: int,
    v2_better: int,
    v2_auto_accept: int,
    threshold: str,
) -> tuple[str, list[str]]:
    """Decide keep_off | internal_shadow_ok | admin_opt_in_candidate.

    A high-risk V2 accept blocks everything. Admin-opt-in needs a
    meaningful corpus (≥50 products), real V2 wins, and at least
    ``min_ratio`` of products auto-accepted (0.5 auto / 0.6 conservative).
    Otherwise the run is safe for internal shadow review only."""
    reasons: list[str] = []
    if potential_high_risk > 0:
        reasons.append(
            f"{potential_high_risk} potential high-risk V2 accept(s) present"
        )
        return "keep_off", reasons

    min_ratio = 0.6 if threshold == "conservative" else 0.5
    auto_ok = product_count > 0 and v2_auto_accept >= min_ratio * product_count
    if product_count >= 50 and v2_better > 0 and auto_ok:
        reasons.append("no high-risk V2 accepts")
        reasons.append(f"{v2_better} V2-better-than-V1 wins")
        reasons.append(
            f"{v2_auto_accept}/{product_count} auto-accepted "
            f"(≥{int(min_ratio * 100)}%) over ≥50 products"
        )
        return "admin_opt_in_candidate", reasons

    if product_count < 50:
        reasons.append(f"only {product_count} products inspected (<50)")
    if v2_better == 0:
        reasons.append("no V2-better-than-V1 wins")
    if not auto_ok:
        reasons.append(
            f"auto-accept {v2_auto_accept}/{product_count} below "
            f"{int(min_ratio * 100)}%"
        )
    reasons.append("no high-risk V2 accepts — safe for internal shadow review")
    return "internal_shadow_ok", reasons


def build_summary(
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    top_k: int,
    provider: str,
    model: str,
    generated_at: str | None,
    threshold: str = "auto",
) -> dict[str, Any]:
    n = len(rows)
    ag = {b: 0 for b in _AGREEMENT_BUCKETS}
    rk = {b: 0 for b in _RISK_BUCKETS}
    for r in rows:
        ag[r["agreement_bucket"]] = ag.get(r["agreement_bucket"], 0) + 1
        rk[r["risk_bucket"]] = rk.get(r["risk_bucket"], 0) + 1

    v2_auto = sum(1 for r in rows if r["v2_outcome"] == "match")
    v2_review = sum(1 for r in rows if r["v2_outcome"] == "review")
    v2_better = rk["v2_better_than_v1"]
    v1_fp = sum(1 for r in rows if "V1 likely false positive" in r["notes"])
    # "potential high risk" = a V2 auto-accept that is NOT concept/head
    # consistent with the product. v1_only (V2 missed it) is a coverage gap,
    # not a V2 risk, so it is NOT counted here.
    potential_high_risk = rk["v2_potential_false_positive"]

    recommendation, reasons = _recommendation(
        product_count=n, potential_high_risk=potential_high_risk,
        v2_better=v2_better, v2_auto_accept=v2_auto, threshold=threshold,
    )
    return {
        "project_id": project_id,
        "product_count": n,
        "top_k": top_k,
        "provider": provider,
        "model": model,
        "generated_at": generated_at,
        "agreement_bucket_counts": ag,
        "risk_bucket_counts": rk,
        "v2_auto_accept_count": v2_auto,
        "v2_review_required_count": v2_review,
        "v2_better_than_v1_count": v2_better,
        "v1_likely_false_positive_count": v1_fp,
        "potential_high_risk_count": potential_high_risk,
        "recommendation": recommendation,
        "recommendation_threshold": threshold,
        "recommendation_reasons": reasons,
        "admin_opt_in_gates": {
            "potential_high_risk_zero": potential_high_risk == 0,
            "v2_better_than_v1_positive": v2_better > 0,
            "v1_default_unchanged": True,    # this tool never changes the default
            "embeddings_cli_only": True,     # no production route uses V2/embeddings
        },
    }


def write_summary_json(path: str | Path, summary: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def write_filtered_csvs(
    out_dir: str | Path, project_id: str, rows: list[dict[str, Any]]
) -> dict[str, int]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for stem, pred in _FILTERED_SPECS.items():
        fname = f"{stem}_{project_id}.csv"
        subset = [r for r in rows if pred(r)]
        with (out / fname).open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COMPARISON_CSV_COLUMNS)
            w.writeheader()
            for r in subset:
                w.writerow(r)
        counts[fname] = len(subset)
    return counts


def print_summary(
    summary: dict[str, Any],
    csv_path: Path,
    *,
    json_path: Path | None = None,
    filtered_counts: dict[str, int] | None = None,
) -> None:
    ag = summary["agreement_bucket_counts"]
    print("\n" + "=" * 72)
    print(
        f"NEVO V1-vs-V2 shadow comparison — {summary['product_count']} "
        "products inspected"
    )
    print("-" * 72)
    for b in _AGREEMENT_BUCKETS:
        print(f"  {b:28} {ag[b]:>6}")
    print("-" * 72)
    print(f"  V2 auto_accept                {summary['v2_auto_accept_count']:>6}")
    print(f"  V2 review_required            {summary['v2_review_required_count']:>6}")
    print(f"  V2 better than V1             {summary['v2_better_than_v1_count']:>6}")
    print(f"  V1 likely false positives     {summary['v1_likely_false_positive_count']:>6}")
    print(f"  potential high-risk           {summary['potential_high_risk_count']:>6}")
    print("-" * 72)
    print(f"  RECOMMENDATION: {summary['recommendation']}")
    for reason in summary["recommendation_reasons"]:
        print(f"    - {reason}")
    print("  Admin opt-in gates:")
    for gate, ok in summary["admin_opt_in_gates"].items():
        print(f"    [{'x' if ok else ' '}] {gate}")
    print("=" * 72)
    print(f"CSV:  {csv_path}")
    if json_path is not None:
        print(f"JSON: {json_path}")
    if filtered_counts:
        for fname, count in filtered_counts.items():
            print(f"      {fname} ({count} rows)")
    print("Read-only — no database writes were made.")


# ---------------------------------------------------------------------------
# Provider / index construction (read-only, evaluator mode).
# ---------------------------------------------------------------------------
def _build_v2_provider(model: str, *, require_voyage: bool) -> tuple[Any, str]:
    """Voyage when embeddings are explicitly enabled; otherwise the
    deterministic fake (evaluator/dev mode). A present VOYAGE_API_KEY alone
    does NOT enable Voyage."""
    if embeddings_enabled():
        provider = build_embedding_provider("voyage", model=model)  # raises w/o key
        return provider, "voyage"
    if require_voyage:
        raise EmbeddingProviderError(
            "--require-voyage was set but ALTERA_ENABLE_EMBEDDINGS is not true; "
            "refusing to run V2 with the fake provider."
        )
    return build_embedding_provider("fake"), "fake"


def _make_cache(cache_dir: str, provider_name: str, model: str) -> Any:
    if not cache_dir:
        return InMemoryEmbeddingCache()
    slug = f"{provider_name}-{model}".replace("/", "_").replace(".", "_")
    return FileEmbeddingCache(Path(cache_dir) / f"embeddings-{slug}.json")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.compare_nevo_v1_v2",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    ap.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-products", type=int, default=None)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"], default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--require-voyage", action="store_true")
    ap.add_argument("--debug", action="store_true")
    # Phase Quality-V2-N — readiness artifacts.
    ap.add_argument(
        "--write-summary-json", action=argparse.BooleanOptionalAction, default=True,
        help="write nevo_v1_v2_comparison_<project>.json (default: on).",
    )
    ap.add_argument(
        "--write-filtered-csvs", action=argparse.BooleanOptionalAction, default=True,
        help="write per-bucket filtered CSVs (default: on).",
    )
    ap.add_argument(
        "--recommendation-threshold", choices=["auto", "conservative"],
        default="auto",
        help="'auto' = ≥50% auto-accept; 'conservative' = ≥60% (default: auto).",
    )
    return ap


def _eligible(product: Any) -> bool:
    """A product eligible for NEVO / Protein-Tracker nutrition enrichment:
    the Protein Tracker methodology produced fields for it."""
    return getattr(product, "pt_fields", None) is not None


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        project_id = UUID(str(args.project_id))
    except (ValueError, TypeError):
        print(f"FATAL: invalid --project-id {args.project_id!r}")
        return 2

    if store is None:
        from altera_api.api.store_factory import get_store

        store = get_store()

    project = store.get_project(project_id)
    if project is None:
        print(f"FATAL: project {project_id} not found")
        return 2

    products = list(store.list_products_for_project(project_id))
    eligible = [p for p in products if _eligible(p)]
    if not eligible:
        print(
            f"  note: no PT/NEVO-eligible products found among {len(products)}; "
            "comparing all products."
        )
        eligible = products
    if args.limit_products is not None:
        eligible = eligible[: args.limit_products]

    print("# NEVO V1-vs-V2 shadow comparison (read-only)")
    print(f"  project={project_id}  products={len(eligible)}  top_k={args.top_k}")

    # V1 — deterministic production NEVO provider over the store's NEVO table.
    nevo_entries = list(store.list_nevo_entries())
    nevo = NevoProvider.from_entries(nevo_entries)
    print(f"  V1 NEVO entries={len(nevo_entries)}")

    # V2 — embeddings matcher (evaluator/dev mode; never wired to a route).
    try:
        provider, provider_name = _build_v2_provider(
            args.embedding_model, require_voyage=args.require_voyage
        )
    except EmbeddingProviderError as exc:
        print(f"FATAL: {exc}")
        if args.debug:
            traceback.print_exc()
        return 2

    references = load_nevo_reference(args.reference_source, path=args.reference)
    cache = _make_cache(args.cache_dir, provider_name, args.embedding_model)
    print(
        f"  V2 provider={provider_name} model={args.embedding_model} "
        f"reference={args.reference_source} ({len(references)} foods)"
    )
    try:
        index = NevoVectorIndex.load_or_build(
            references, provider=provider, provider_name=provider_name,
            top_k=args.top_k, cache=cache, batch_size=args.batch_size,
        )
    except EmbeddingProviderError as exc:
        print(f"FATAL: V2 index build failed: {exc}")
        if args.debug:
            traceback.print_exc()
        return 2
    cache.flush()
    matcher = get_nevo_matcher("v2-embeddings", index=index, evaluator_mode=True)

    rows = build_comparison_rows(eligible, nevo, matcher, top_k=args.top_k)

    out_dir = Path(args.output_dir)
    csv_path = out_dir / f"nevo_v1_v2_comparison_{project_id}.csv"
    write_comparison_csv(csv_path, rows)

    summary = build_summary(
        rows, project_id=str(project_id), top_k=args.top_k,
        provider=provider_name, model=args.embedding_model,
        generated_at=datetime.now(UTC).isoformat(),
        threshold=args.recommendation_threshold,
    )
    json_path: Path | None = None
    if args.write_summary_json:
        json_path = out_dir / f"nevo_v1_v2_comparison_{project_id}.json"
        write_summary_json(json_path, summary)
    filtered_counts: dict[str, int] | None = None
    if args.write_filtered_csvs:
        filtered_counts = write_filtered_csvs(out_dir, str(project_id), rows)

    print_summary(summary, csv_path, json_path=json_path, filtered_counts=filtered_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

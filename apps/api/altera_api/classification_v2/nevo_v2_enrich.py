"""Phase Quality-V2-O — admin/internal NEVO V2 opt-in (dry-run).

A strictly controlled, admin/internal path to run the NEVO V2 matcher over a
project's products and see exactly what it WOULD enrich — without touching
production. It is NOT wired into any app route; V1 stays the production
default and embeddings stay disabled by default.

Activation model (Part A)
-------------------------
The matcher is chosen by ``ALTERA_NEVO_MATCHER_VERSION`` (or
``--matcher-version``): ``v1`` (default, deterministic ``NevoProvider``) or
``v2-embeddings``. ``v2-embeddings`` runs ONLY when
``ALTERA_ENABLE_EMBEDDINGS=true`` (+ ``VOYAGE_API_KEY`` for the real Voyage
provider); otherwise it fails clearly — there is no silent fake provider.
The deterministic fake is available for evaluator/dev runs only via the
explicit ``--evaluator-fake`` flag. (``v2-rules`` has no candidate
generator, so it cannot drive enrichment and is rejected here.)

Dry-run first (Part C/D)
------------------------
Default is DRY-RUN: it reads the project + products + NEVO reference and
writes enrichment PROPOSALS (CSV + JSON) under the output dir, persisting
nothing. ``--apply`` is accepted but intentionally GATED: persisting
V2-tagged enrichment records needs a Supabase migration to add a V2
``match_method``/source tag (the DB CHECK currently allows only
``deterministic|ai_assisted|manual|none``), which is out of scope — so
``--apply`` refuses with a clear message and writes nothing. Rollback is
trivial: unset ``ALTERA_NEVO_MATCHER_VERSION`` (or set ``v1``).

    ALTERA_NEVO_MATCHER_VERSION=v2-embeddings ALTERA_ENABLE_EMBEDDINGS=true \
    VOYAGE_API_KEY=$VOYAGE_API_KEY \
    python -m altera_api.classification_v2.nevo_v2_enrich \
        --project-id <uuid> --output-dir /tmp/altera-quality \
        --top-k 20 --cache-dir /tmp/altera-quality/cache
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

from altera_api.classification_v2.compare_nevo_v1_v2 import (
    _eligible,
    _make_cache,
    _v2_query,
)
from altera_api.classification_v2.nevo_index import NevoVectorIndex, load_nevo_reference
from altera_api.classification_v2.nevo_matcher import (
    NevoMatcherError,
    NevoMatcherVersion,
    get_nevo_matcher,
    resolve_nevo_matcher_version,
)
from altera_api.classification_v2.nevo_nutrition_safety import (
    _AUTO_ACCEPT_THRESHOLD,
    NUTRITION_SAFETY_ACTIONS,
    nutrition_safety_action,
)
from altera_api.classification_v2.nevo_nutrition_safety import (
    base_safety_action as _safety_action,  # noqa: F401  (re-export: stage-1 gate)
)
from altera_api.embeddings.provider import (
    EmbeddingProviderError,
    build_embedding_provider,
)
from altera_api.enrichment.providers.nevo import NevoProvider
from altera_api.quality_config import DEFAULT_EMBEDDING_MODEL, embeddings_enabled

_DEFAULT_OUTPUT_DIR = "/tmp/altera-quality"
_DEFAULT_CACHE_DIR = "/tmp/altera-quality/cache"

PROPOSAL_CSV_COLUMNS = [
    "product_id", "product_name", "matcher_version", "embedding_provider",
    "embedding_model", "matcher_outcome", "matcher_confidence", "nevo_code",
    "nevo_food_name", "enriched_protein_g_per_100g", "match_type",
    "review_required", "nutrition_safety_action", "nutrition_safety_reason",
    "would_persist", "top_5_candidates", "rejection_reasons_summary", "notes",
]

# Blank columns appended to the filtered review-package CSVs so a human can
# triage each row (Quality-V2-Q Part A).
REVIEWER_COLUMNS = [
    "manual_decision", "reviewer_notes", "approved_nevo_code",
    "approved_nevo_name",
]
FILTERED_REVIEW_COLUMNS = PROPOSAL_CSV_COLUMNS + REVIEWER_COLUMNS

#: (artifact key, filename template, row predicate). Buckets are mutually
#: exclusive (the actions are), so a row lands in at most one bucket.
_FILTERED_REVIEW_SPECS = (
    ("would_enrich", "nevo_v2_enrich_would_enrich_{pid}.csv",
     lambda r: r["nutrition_safety_action"] == "would_enrich"),
    ("state_mismatch", "nevo_v2_enrich_state_mismatch_{pid}.csv",
     lambda r: r["nutrition_safety_action"] == "skip_state_mismatch"),
    ("proxy_too_broad", "nevo_v2_enrich_proxy_too_broad_{pid}.csv",
     lambda r: r["nutrition_safety_action"] == "skip_proxy_too_broad"),
    ("no_match", "nevo_v2_enrich_no_match_{pid}.csv",
     lambda r: r["matcher_outcome"] == "no_match"),
    ("review", "nevo_v2_enrich_review_{pid}.csv",
     lambda r: r["nutrition_safety_action"] == "route_to_review"),
)


def _protein_of(entry: Any) -> float | None:
    val = getattr(entry, "protein_g_per_100g", None)
    return float(val) if val is not None else None


def _proposal_v1(product: Any, nevo: NevoProvider) -> dict[str, Any]:
    r = nevo.match(food_name=product.product_name, food_group=product.retailer_category)
    if r is None:
        matched, code, food, conf, mt, protein = False, "", "", 0.0, "no_match", None
        review = True
    else:
        matched, code, food = True, r.entry.nevo_code, r.entry.food_name_en
        conf, mt = float(r.confidence), r.match_type
        protein = _protein_of(r.entry)
        review = conf < _AUTO_ACCEPT_THRESHOLD
    return {
        "matched": matched, "code": code, "food": food, "confidence": conf,
        "match_type": mt, "review_required": review, "protein": protein,
        "top_5": "", "rejections": "",
    }


def _proposal_v2(
    product: Any, matcher: Any, nevo_by_code: dict[str, Any], top_k: int
) -> dict[str, Any]:
    decision = matcher.decide(_v2_query(product), top_k=top_k)
    entry = nevo_by_code.get(str(decision.nevo_code or ""))
    top5 = list(decision.top_candidates)[:5]
    rejections = sorted({t.rejection_reason for t in top5 if t.rejection_reason})
    return {
        "matched": decision.matched,
        "code": decision.nevo_code or "",
        "food": decision.food_name_en or "",
        "confidence": round(decision.confidence, 4),
        "match_type": decision.match_type,
        "review_required": decision.review_required,
        "protein": _protein_of(entry) if entry is not None else None,
        "top_5": " | ".join(t.candidate_name for t in top5),
        "rejections": " ;; ".join(rejections),
    }


def build_proposals(
    products: list[Any], *, version: str, matcher: Any, nevo: NevoProvider | None,
    nevo_by_code: dict[str, Any], provider_name: str, model: str, top_k: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in products:
        if version == NevoMatcherVersion.V1:
            d = _proposal_v1(p, nevo)  # type: ignore[arg-type]
            emb_provider, emb_model = "", ""
        else:
            d = _proposal_v2(p, matcher, nevo_by_code, top_k)
            emb_provider, emb_model = provider_name, model
        # Stage 2 — nutrition safety (state/beverage/proxy) layered on the
        # matcher result. Matcher-accepted does NOT imply safe-to-enrich.
        action, reason = nutrition_safety_action(
            matched=d["matched"], review_required=d["review_required"],
            confidence=d["confidence"], protein=d["protein"],
            product_name=p.product_name, ref_name=d["food"],
        )
        matcher_outcome = (
            "no_match" if not d["matched"]
            else ("review" if d["review_required"] else "match")
        )
        rows.append(
            {
                "product_id": str(p.id),
                "product_name": p.product_name,
                "matcher_version": version,
                "embedding_provider": emb_provider,
                "embedding_model": emb_model,
                "matcher_outcome": matcher_outcome,
                "matcher_confidence": d["confidence"],
                "nevo_code": d["code"],
                "nevo_food_name": d["food"],
                "enriched_protein_g_per_100g": (
                    d["protein"] if d["protein"] is not None else ""
                ),
                "match_type": d["match_type"],
                "review_required": d["review_required"],
                "nutrition_safety_action": action,
                "nutrition_safety_reason": reason,
                "would_persist": action == "would_enrich",
                "top_5_candidates": d["top_5"],
                "rejection_reasons_summary": d["rejections"],
                "notes": "",
            }
        )
    return rows


def write_proposals_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PROPOSAL_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_filtered_review_csvs(
    out_dir: str | Path, project_id: str, rows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Write one CSV per review bucket (Part A). Each row carries all proposal
    columns plus blank reviewer columns. Returns ``{key: {path, count}}``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, Any]] = {}
    for key, template, predicate in _FILTERED_REVIEW_SPECS:
        selected = [r for r in rows if predicate(r)]
        path = out / template.format(pid=project_id)
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FILTERED_REVIEW_COLUMNS)
            w.writeheader()
            for r in selected:
                row = {**r, **dict.fromkeys(REVIEWER_COLUMNS, "")}
                w.writerow(row)
        artifacts[key] = {"path": str(path), "count": len(selected)}
    return artifacts


def build_dry_run_summary(
    rows: list[dict[str, Any]], *, project_id: str, version: str,
    provider: str, model: str, top_k: int, generated_at: str | None,
) -> dict[str, Any]:
    # Stage 1 — what the MATCHER decided (concept correctness).
    matcher_outcome_counts = {
        o: sum(1 for r in rows if r["matcher_outcome"] == o)
        for o in ("match", "review", "no_match")
    }
    # Stage 2 — what the NUTRITION-safety policy decided. A matcher "match"
    # does NOT imply it is safe to enrich nutrition from it.
    nutrition_safety_counts = {
        a: sum(1 for r in rows if r["nutrition_safety_action"] == a)
        for a in NUTRITION_SAFETY_ACTIONS
    }
    # A few concrete examples of state/proxy mismatches that were downgraded
    # despite a concept-correct matcher result (Part D observability).
    skipped_examples = [
        {
            "product_name": r["product_name"],
            "nevo_food_name": r["nevo_food_name"],
            "matcher_outcome": r["matcher_outcome"],
            "nutrition_safety_action": r["nutrition_safety_action"],
            "nutrition_safety_reason": r["nutrition_safety_reason"],
        }
        for r in rows
        if r["nutrition_safety_action"]
        in ("skip_state_mismatch", "skip_proxy_too_broad")
    ][:10]
    return {
        "project_id": project_id,
        "product_count": len(rows),
        "matcher_version": version,
        "embedding_provider": provider,
        "embedding_model": model,
        "top_k": top_k,
        "safety_mode": "dry_run",
        "persisted_writes": 0,
        "generated_at": generated_at,
        "matcher_match_count": matcher_outcome_counts["match"],
        "matcher_outcome_counts": matcher_outcome_counts,
        "nutrition_would_enrich": nutrition_safety_counts["would_enrich"],
        "nutrition_safety_counts": nutrition_safety_counts,
        # Part C — headline review accounting. enrich_ready is what would be
        # auto-enriched after the final filters; everything else needs a human.
        "enrich_ready_count": nutrition_safety_counts["would_enrich"],
        "manual_review_required_count": (
            len(rows) - nutrition_safety_counts["would_enrich"]
        ),
        "skipped_examples": skipped_examples,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.nevo_v2_enrich",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    ap.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-products", type=int, default=None)
    ap.add_argument(
        "--matcher-version", choices=["v1", "v2-rules", "v2-embeddings"],
        default=None, help="overrides ALTERA_NEVO_MATCHER_VERSION (default: env).",
    )
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"], default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument(
        "--evaluator-fake", action="store_true",
        help="dev/CI only: allow the deterministic FAKE provider for "
             "v2-embeddings when embeddings are disabled.",
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="GATED: persisting V2 enrichment records is not enabled (needs a "
             "migration to tag V2 records); --apply refuses and writes nothing.",
    )
    ap.add_argument("--overwrite-v1", action="store_true",
                    help="(reserved for the future apply path; no effect today)")
    ap.add_argument("--debug", action="store_true")
    return ap


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        project_id = UUID(str(args.project_id))
    except (ValueError, TypeError):
        print(f"FATAL: invalid --project-id {args.project_id!r}")
        return 2

    version = resolve_nevo_matcher_version(args.matcher_version)

    # Part D — the persisted write path is gated (needs a migration to tag V2
    # records). Refuse --apply BEFORE doing any work; never write.
    if args.apply:
        print(
            "FATAL: --apply is gated. Persisting V2-tagged NEVO enrichment "
            "records requires a Supabase migration to add a V2 match_method/"
            "source tag (the DB CHECK currently allows only deterministic|"
            "ai_assisted|manual|none). Re-run without --apply for a dry-run."
        )
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
        print(f"  note: no PT/NEVO-eligible products among {len(products)}; "
              "using all products.")
        eligible = products
    if args.limit_products is not None:
        eligible = eligible[: args.limit_products]

    nevo_entries = list(store.list_nevo_entries())
    nevo_by_code = {str(e.nevo_code): e for e in nevo_entries}

    matcher: Any = None
    nevo: NevoProvider | None = None
    provider_name, model = "", ""

    if version == NevoMatcherVersion.V2_RULES:
        print("FATAL: v2-rules has no candidate generator for enrichment; "
              "use v1 or v2-embeddings.")
        return 2

    if version == NevoMatcherVersion.V2_EMBEDDINGS:
        # Activation gate (Part A): raises if embeddings are not enabled and
        # we are not in explicit evaluator-fake mode.
        try:
            get_nevo_matcher("v2-embeddings", evaluator_mode=args.evaluator_fake)
        except NevoMatcherError as exc:
            print(f"FATAL: {exc}")
            if args.debug:
                traceback.print_exc()
            return 2
        try:
            if embeddings_enabled():
                provider = build_embedding_provider("voyage", model=args.embedding_model)
                provider_name = "voyage"
            else:  # only reachable with --evaluator-fake (gate passed above)
                provider = build_embedding_provider("fake")
                provider_name = "fake"
        except EmbeddingProviderError as exc:
            print(f"FATAL: {exc}")
            if args.debug:
                traceback.print_exc()
            return 2
        model = args.embedding_model
        references = load_nevo_reference(args.reference_source, path=args.reference)
        cache = _make_cache(args.cache_dir, provider_name, args.embedding_model)
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
        matcher = get_nevo_matcher(
            "v2-embeddings", index=index, evaluator_mode=args.evaluator_fake
        )
    else:  # v1
        nevo = NevoProvider.from_entries(nevo_entries)

    # Part B/E — clear activation + safety-mode log line.
    print("# NEVO V2 admin enrichment (DRY-RUN — no database writes)")
    print(
        f"  project={project_id} products={len(eligible)} "
        f"matcher_version={version} embedding_provider={provider_name or 'n/a'} "
        f"embedding_model={model or 'n/a'} top_k={args.top_k} safety_mode=dry_run"
    )

    rows = build_proposals(
        eligible, version=version, matcher=matcher, nevo=nevo,
        nevo_by_code=nevo_by_code, provider_name=provider_name, model=model,
        top_k=args.top_k,
    )

    out_dir = Path(args.output_dir)
    csv_path = out_dir / f"nevo_v2_enrich_proposals_{project_id}.csv"
    write_proposals_csv(csv_path, rows)
    summary = build_dry_run_summary(
        rows, project_id=str(project_id), version=str(version),
        provider=provider_name, model=model, top_k=args.top_k,
        generated_at=datetime.now(UTC).isoformat(),
    )
    # Part A — filtered review-package CSVs (one per bucket, reviewer columns).
    summary["filtered_artifacts"] = write_filtered_review_csvs(
        out_dir, str(project_id), rows
    )
    json_path = out_dir / f"nevo_v2_enrich_proposals_{project_id}.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    print("-" * 64)
    print("STAGE 1 — matcher outcome (concept correctness):")
    for outcome, count in summary["matcher_outcome_counts"].items():
        print(f"  {outcome:28} {count:>6}")
    print("STAGE 2 — nutrition safety (a matcher 'match' is NOT, by itself,")
    print("          safe to enrich nutrition from):")
    for action, count in summary["nutrition_safety_counts"].items():
        print(f"  {action:28} {count:>6}")
    print(
        f"  => matcher matches={summary['matcher_match_count']}  "
        f"nutrition would_enrich={summary['nutrition_would_enrich']}"
    )
    if summary["skipped_examples"]:
        print("-" * 64)
        print("Examples downgraded on physical-state / proxy mismatch:")
        for ex in summary["skipped_examples"]:
            print(
                f"  [{ex['nutrition_safety_action']}] "
                f"{ex['product_name']!r} vs {ex['nevo_food_name']!r}"
            )
            print(f"      {ex['nutrition_safety_reason']}")
    print("-" * 64)
    print("REVIEW PACKAGE (one CSV per bucket, with reviewer columns):")
    for key, info in summary["filtered_artifacts"].items():
        print(f"  {key:18} {info['count']:>6}  {info['path']}")
    print(
        f"  => enrich_ready={summary['enrich_ready_count']}  "
        f"manual_review_required={summary['manual_review_required_count']}"
    )
    print("-" * 64)
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")
    print("DRY-RUN — no database writes were made. Rollback: unset "
          "ALTERA_NEVO_MATCHER_VERSION (or set v1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

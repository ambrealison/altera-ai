"""Phase Quality-V2-Y — NEVO V2 retailer-scale (≈30k rows) readiness baseline.

This is a DESIGN artifact, not an implementation. The pilot applied 49 records;
real retailer feeds are ~30k product rows. Before building a bulk pipeline we
pin down the strategy and the exact next artifacts. ``scale_baseline_report()``
returns the structured baseline so the audit CLI can emit it alongside a
post-apply audit. Nothing here runs a bulk pipeline or writes the DB.
"""

from __future__ import annotations

from typing import Any

#: Target retailer-feed size the pipeline must handle.
TARGET_ROW_COUNT = 30_000


def scale_baseline_report() -> dict[str, Any]:
    return {
        "target_row_count": TARGET_ROW_COUNT,
        "status": "design_only",
        "pilot": {
            "applied_v2_count": 49,
            "idempotent_rerun": "existing_v2=49, writable=0",
            "split_limitation": (
                "V2 writes total protein only; plant/animal split stays blank "
                "and falls back to the classification assumption"
            ),
        },
        "deduplication_strategy": {
            "why": (
                "30k feeds contain many near-duplicate product names "
                "(pack-size / promo variants); embedding + matching each is "
                "wasteful and review-noisy"
            ),
            "approach": [
                "normalize with the existing _norm (NFKD + ASCII fold + "
                "ligature expansion) and strip size/quantity/promo tokens",
                "group by canonical_product_key (below); match ONE "
                "representative per group, fan the decision back to members",
                "track group size so review effort is spent on high-volume "
                "groups first",
            ],
        },
        "canonical_product_key": {
            "definition": (
                "sha1 of _norm(product_name) with size/qty tokens removed, "
                "plus concept_of() when resolvable, plus retailer_category"
            ),
            "stored_as": (
                "source_metadata.canonical_product_key on each written record "
                "(no schema change needed — JSONB)"
            ),
        },
        "batch_embedding_cache": {
            "reuse": (
                "the existing NevoVectorIndex.load_or_build persistent cache "
                "keyed by (provider, model, input_type, text)"
            ),
            "plan": [
                "embed unique canonical keys only (post-dedup), not raw rows",
                "batch at --batch-size; persist the cache between runs so "
                "re-feeds are near-zero-cost",
            ],
        },
        "review_prioritization": {
            "order": [
                "P0 — never-auto (non-food/pet that nonetheless matched)",
                "P1 — would_enrich at lower confidence, state/proxy downgrades, "
                "large dedup groups",
                "P2 — high-confidence would_enrich, safe abstains",
                "P3 — non-food / policy-excluded",
            ],
            "goal": "bound human review to the rows that change the most kg",
        },
        "no_match_feedback_loop": {
            "capture": (
                "persist no_match canonical keys + top-5 rejected candidates to "
                "a review CSV; cluster recurring no-matches"
            ),
            "act": (
                "recurring food no-matches become new concept/alias rules "
                "(nevo_rules) — measured against the existing benchmark gates "
                "(HC-FP=0, forbidden=100%, dangerous=0) before shipping"
            ),
        },
        "apply_batching_strategy": {
            "rules": [
                "reuse apply_nevo_v2_plan guards (dry-run default, "
                "--confirm-apply-v2, provenance-column probe, never overwrite "
                "manual/V1, idempotent skip of existing V2)",
                "chunk confirmed applies (e.g. --limit-apply / offset windows) "
                "and re-audit per chunk",
                "stop-on-anomaly: a chunk whose audit is fail halts the run",
            ],
        },
        "monitoring_metrics": [
            "dedup_ratio (unique canonical keys / raw rows)",
            "embedding_cache_hit_rate",
            "matcher match / no_match / review counts",
            "nutrition would_enrich vs skip_state_mismatch / skip_proxy_too_broad",
            "apply written / skipped_v1 / skipped_manual / skipped_existing_v2 / "
            "error",
            "audit_status distribution per chunk (pass/warn/fail)",
            "coverage: % products with a usable protein value after V2",
        ],
        "next_artifacts": [
            "dedup_nevo_v2_feed.py — canonical key + group representatives "
            "(dry-run CSV only)",
            "nevo_v2_feed_dedup_<feed>.csv — groups + representative + size",
            "batch_match_nevo_v2.py — match representatives via the cached "
            "index (dry-run proposals only)",
            "nevo_v2_no_match_clusters_<feed>.csv — recurring no-match clusters "
            "for the rule feedback loop",
            "chunked apply driver wrapping apply_nevo_v2_plan + "
            "audit_nevo_v2_apply per chunk",
            "split-enrichment design — how/whether to source plant/animal "
            "split for V2 (currently total protein only)",
        ],
    }

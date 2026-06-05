# NEVO V2 — retailer-scale (≈30k) readiness baseline (Quality-V2-Y)

**Status: DESIGN ONLY.** The pilot applied 49 V2 records correctly (idempotent
re-run: `existing_v2=49, writable=0`). Real retailer feeds are ~30k product
rows. This document pins the strategy and the **exact next artifacts** before
any bulk pipeline is built. No bulk pipeline is implemented here; nothing writes
the DB; V1 stays default and embeddings stay off.

The structured version of this baseline is emitted by
`nevo_v2_scale_baseline.scale_baseline_report()` and can be written next to a
post-apply audit via `audit_nevo_v2_apply --write-scale-baseline`.

## Why pilot ≠ 30k

- 49 hand-reviewed rows → ~30k means human review must be *bounded* and
  *prioritised*, not row-by-row.
- 30k feeds carry heavy near-duplication (pack-size / promo / multipack
  variants). Embedding + matching every raw row is wasteful and floods review.
- A no-match at 30k scale is a *signal* (a missing concept/alias), not just a
  skipped row — it should feed back into the rules.

## Deduplication strategy

- Normalise with the existing `_norm` (NFKD + ASCII fold + œ/æ ligature
  expansion) and strip size/quantity/promo tokens (`1kg`, `x6`, `lot de`, …).
- Group by **canonical product key** (below); embed + match ONE representative
  per group; fan the decision back to all members.
- Carry `group_size` so review effort targets high-volume groups first.

## Canonical product key

- `sha1(_norm(product_name) minus size/qty tokens)` + `concept_of()` when
  resolvable + `retailer_category`.
- Stored as `source_metadata.canonical_product_key` on each written record — no
  schema change needed (it's JSONB).

## Batch embedding cache

- Reuse `NevoVectorIndex.load_or_build`'s persistent cache keyed by
  `(provider, model, input_type, text)`.
- Embed unique canonical keys only (post-dedup), batched at `--batch-size`;
  persist between runs so re-feeds are near-zero-cost.

## Review prioritisation

Order review by the existing P0–P3 scheme (from V2-R), refined for scale:

- **P0** — never-auto (non-food / pet that nonetheless matched).
- **P1** — lower-confidence `would_enrich`, state/proxy downgrades, and **large
  dedup groups** (one decision moves many products).
- **P2** — high-confidence `would_enrich`, safe abstains.
- **P3** — non-food / policy-excluded.

Goal: bound human review to the rows that change the most kg.

## No-match feedback loop

- Persist no-match canonical keys + their top-5 rejected candidates to a review
  CSV; cluster recurring no-matches.
- Recurring *food* no-matches become new concept/alias rules in `nevo_rules`,
  each measured against the standing benchmark gates (HC-FP=0,
  forbidden-rejection=100%, dangerous=0) before shipping.

## Apply batching strategy

- Reuse `apply_nevo_v2_plan` guards verbatim: dry-run default,
  `--confirm-apply-v2`, the migration-0037 column probe, never overwrite
  manual/V1, idempotent skip of existing V2.
- Chunk confirmed applies (`--limit-apply` / offset windows) and **re-audit per
  chunk** with `audit_nevo_v2_apply`.
- **Stop-on-anomaly:** a chunk whose audit is `fail` halts the run.

## Monitoring metrics

- `dedup_ratio` (unique canonical keys / raw rows)
- `embedding_cache_hit_rate`
- matcher `match` / `no_match` / `review` counts
- nutrition `would_enrich` vs `skip_state_mismatch` / `skip_proxy_too_broad`
- apply `written` / `skipped_v1` / `skipped_manual` / `skipped_existing_v2` /
  `error`
- per-chunk `audit_status` distribution (pass/warn/fail)
- coverage: % of products with a usable protein value after V2

## Exact next artifacts (not built yet)

1. `dedup_nevo_v2_feed.py` — canonical key + group representatives (dry-run CSV
   only) → `nevo_v2_feed_dedup_<feed>.csv` (groups + representative + size).
2. `batch_match_nevo_v2.py` — match representatives via the cached index
   (dry-run proposals only).
3. `nevo_v2_no_match_clusters_<feed>.csv` — recurring no-match clusters for the
   rule feedback loop.
4. A chunked apply driver wrapping `apply_nevo_v2_plan` + `audit_nevo_v2_apply`
   per chunk (stop-on-fail).
5. Split-enrichment design — how/whether to source the plant/animal protein
   split for V2 (today V2 writes **total protein only**, so the UI split stays
   blank and falls back to the classification assumption).

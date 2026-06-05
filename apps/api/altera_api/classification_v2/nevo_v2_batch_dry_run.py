"""Phase Quality-V2-AC — retailer-scale (≈30k) NEVO V2 batch DRY-RUN.

Reads a raw retailer product CSV and produces a review-ready matching report —
**deduplicated**, with **sensitive commercial columns excluded**, and **never
writing the DB**. It is the industrial dry-run that must run before any bulk
apply. No route imports it, V1 stays default, embeddings stay off by default.

Data minimisation (hard rule): only product-identification fields
(product_name, brand, category, ingredients, labels, pack-size) are read.
Commercial volume/sales/price/margin columns are detected and excluded from the
embedding text AND from every output artifact — and the embedding text builder
(``build_nevo_query_text``) is a second backstop that raises on any commercial
field.

    python -m altera_api.classification_v2.nevo_v2_batch_dry_run \
        --input retailer_products.csv --output-dir /tmp/altera-quality \
        --top-k 20 --cache-dir /tmp/altera-quality/cache \
        --matcher-version v2-embeddings --require-voyage
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import traceback
from pathlib import Path
from typing import Any

from altera_api.classification_v2.compare_nevo_v1_v2 import _make_cache
from altera_api.classification_v2.nevo_index import (
    NevoVectorIndex,
    load_nevo_reference,
)
from altera_api.classification_v2.nevo_matcher import (
    NevoMatcherError,
    get_nevo_matcher,
)
from altera_api.classification_v2.nevo_nutrition_safety import (
    nutrition_safety_action,
)
from altera_api.classification_v2.nevo_review_workflow import (
    REVIEWER_BLANK_COLUMNS,
    annotate,
    classify_product_policy,
)
from altera_api.classification_v2.nevo_rules import _norm
from altera_api.embeddings.provider import (
    EmbeddingProviderError,
    build_embedding_provider,
)
from altera_api.quality_config import DEFAULT_EMBEDDING_MODEL, embeddings_enabled

# --- sensitive commercial columns (Part B) --------------------------------
_SENSITIVE_TOKENS = (
    "volume", "sales", "revenue", "turnover", "price", "margin", "units sold",
    "quantity sold", "market share", "sellout", "sell in", "sell through",
    "store count", "distribution", "velocity",
)

_PROTEIN_SENTINEL = 1.0  # dry-run: assume a matched NEVO entry has a value;
#                          the real number is resolved at apply-time from the DB.

RESULT_CSV_COLUMNS = [
    "canonical_product_key", "representative_product_name", "brand",
    "category", "duplicate_count", "v2_outcome", "safety_action", "nevo_code",
    "nevo_food_name", "protein_g_per_100g", "confidence", "match_type",
    "top_5_candidate_names", "top_5_candidate_codes", "top_5_similarities",
    "rejection_summary", "review_priority", "suggested_action",
]
DEDUP_CSV_COLUMNS = [
    "canonical_product_key", "representative_product_name", "duplicate_count",
    "raw_row_indices", "safe_fields_used",
]
SENSITIVE_CSV_COLUMNS = ["column_name", "detected_reason", "action"]

#: (filename slug, partition bucket). ``high_risk`` is kept as a back-compat
#: alias of ``true_high_risk``.
_REVIEW_PACKAGES = (
    ("auto_ready", "auto_ready"),
    ("safety_downgrade", "safety_downgrade"),
    ("needs_review", "needs_review"),
    ("no_match", "no_match"),
    ("true_high_risk", "true_high_risk"),
    ("high_risk", "true_high_risk"),
)
_PARTITION_BUCKETS = ("auto_ready", "safety_downgrade", "needs_review",
                      "no_match", "true_high_risk")


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def detect_sensitive_columns(fieldnames: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for col in fieldnames:
        norm = _norm_col(col)
        for tok in _SENSITIVE_TOKENS:
            if tok in norm:
                out.append({"column_name": col,
                            "detected_reason": f"name contains '{tok}'",
                            "action": "excluded"})
                break
    return out


def _detect_roles(safe_cols: list[str]) -> dict[str, str]:
    roles: dict[str, str] = {}

    def role_of(norm: str) -> str | None:
        if "ingredient" in norm:
            return "ingredients"
        if "brand" in norm or "marque" in norm:
            return "brand"
        if any(k in norm for k in ("categor", "rayon", "famille", "department",
                                   "segment")):
            return "category"
        if "label" in norm or "claim" in norm:
            return "labels"
        if any(k in norm for k in ("pack", "format", "grammage",
                                   "conditionnement")) or norm.endswith("size"):
            return "pack_size"
        if ("product" in norm and "name" in norm) or norm in (
                "name", "product", "designation", "libelle", "nom", "article",
                "title", "product label", "product description"):
            return "product_name"
        return None

    for col in safe_cols:
        role = role_of(_norm_col(col))
        if role and role not in roles:
            roles[role] = col
    return roles


def _safe_descriptor(row: dict[str, Any], roles: dict[str, str]) -> dict[str, Any]:
    def val(role: str) -> str:
        col = roles.get(role)
        return (row.get(col) or "").strip() if col else ""

    labels_raw = val("labels")
    labels = [s.strip() for s in re.split(r"[;,|]", labels_raw) if s.strip()] \
        if labels_raw else []
    return {
        "product_name": val("product_name"),
        "brand": val("brand"),
        "category": val("category"),
        "ingredients": val("ingredients"),
        "labels": labels,
        "pack_size": val("pack_size"),
    }


def canonical_key(descriptor: dict[str, Any]) -> str:
    ingr = descriptor["ingredients"]
    ingr_hash = (hashlib.sha256(_norm(ingr).encode()).hexdigest()[:12]
                 if ingr else "")
    parts = [
        _norm(descriptor["product_name"]).strip(),
        _norm(descriptor["brand"]).strip(),
        _norm(descriptor["category"]).strip(),
        ingr_hash,
        _norm(descriptor["pack_size"]).strip(),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _safe_fields_used(descriptor: dict[str, Any]) -> list[str]:
    used = []
    for field in ("product_name", "brand", "category", "ingredients",
                  "pack_size"):
        if descriptor[field]:
            used.append(field)
    if descriptor["labels"]:
        used.append("labels")
    return used


def dedupe(rows: list[dict[str, Any]], roles: dict[str, str], *, enabled: bool,
           ) -> list[dict[str, Any]]:
    """Group raw rows by canonical key (safe fields only)."""
    groups: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        desc = _safe_descriptor(row, roles)
        key = canonical_key(desc) if enabled else f"row-{idx}"
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "canonical_product_key": key, "descriptor": desc,
                "representative_product_name": desc["product_name"],
                "raw_row_indices": [idx],
                "safe_fields_used": _safe_fields_used(desc),
            }
        else:
            g["raw_row_indices"].append(idx)
    return list(groups.values())


def _query_from_descriptor(desc: dict[str, Any]) -> dict[str, Any]:
    # ONLY identification fields — never a commercial value.
    return {
        "product_name": desc["product_name"],
        "retailer_category": desc["category"] or None,
        "ingredients_text": desc["ingredients"] or None,
        "labels": desc["labels"] or None,
    }


def _match_group(group: dict[str, Any], *, matcher: Any, top_k: int,
                 ) -> dict[str, Any]:
    desc = group["descriptor"]
    name = desc["product_name"]
    dup = len(group["raw_row_indices"])
    base = {
        "canonical_product_key": group["canonical_product_key"],
        "representative_product_name": name, "brand": desc["brand"],
        "category": desc["category"], "duplicate_count": dup,
        "nevo_code": "", "nevo_food_name": "", "protein_g_per_100g": "",
        "confidence": "", "match_type": "", "top_5_candidate_names": "",
        "top_5_candidate_codes": "", "top_5_similarities": "",
        "rejection_summary": "",
    }

    policy = classify_product_policy(name)
    base["policy"] = policy
    if policy == "non_food":
        action, reason = "skip_no_match", "non-food / out of nutrition scope"
        row = {**base, "v2_outcome": "policy_excluded", "safety_action": action,
               "nutrition_safety_reason": reason, "matcher_outcome": "no_match",
               "matcher_confidence": 0.0}
    else:
        decision = matcher.decide(_query_from_descriptor(desc), top_k=top_k)
        top5 = list(decision.top_candidates)[:5]
        matched = decision.matched
        action, reason = nutrition_safety_action(
            matched=matched, review_required=decision.review_required,
            confidence=float(decision.confidence),
            protein=_PROTEIN_SENTINEL if matched else None,
            product_name=name, ref_name=decision.food_name_en or "")
        outcome = ("no_match" if not matched
                   else "review_required" if decision.review_required
                   else "auto_accept")
        base.update({
            "nevo_code": decision.nevo_code or "",
            "nevo_food_name": decision.food_name_en or "",
            "confidence": round(float(decision.confidence), 4),
            "match_type": decision.match_type,
            "top_5_candidate_names": " | ".join(t.candidate_name for t in top5),
            "top_5_candidate_codes": " | ".join(
                str(getattr(t, "nevo_code", "")) for t in top5),
            "top_5_similarities": " | ".join(
                str(getattr(t, "similarity", "")) for t in top5),
            "rejection_summary": " ;; ".join(sorted(
                {t.rejection_reason for t in top5
                 if getattr(t, "rejection_reason", None)})),
        })
        row = {**base, "v2_outcome": outcome, "safety_action": action,
               "nutrition_safety_reason": reason,
               "matcher_outcome": ("match" if outcome == "auto_accept"
                                   else "review" if outcome == "review_required"
                                   else "no_match"),
               "matcher_confidence": base["confidence"] or 0.0}

    ann = annotate({
        "product_name": name,
        "nutrition_safety_action": row["safety_action"],
        "nutrition_safety_reason": row.get("nutrition_safety_reason", ""),
        "matcher_outcome": row["matcher_outcome"],
        "matcher_confidence": row["matcher_confidence"],
        "top_5_candidates": row.get("top_5_candidate_names", ""),
    })
    row["review_priority"] = ann["review_priority"]
    row["suggested_action"] = ann["suggested_action"]
    return row


# Quality-V2-AE — bucket semantics. The partition is
# {auto_ready, safety_downgrade, needs_review, no_match, true_high_risk};
# policy_excluded is counted separately (it folds into no_match here).
#
#  * safety_downgrade = the matcher accepted a candidate, but the nutrition
#    safety policy correctly PREVENTS an auto-enrichment (dry pasta vs cooked,
#    compote vs syrup, …). These are review items, NOT dangerous auto-writes.
#  * true_high_risk = a row that WOULD auto-enrich (would_enrich) yet is on a
#    non-food product — i.e. a genuinely dangerous auto-write. Pet food is food
#    (it stays auto_ready / safety_downgrade like any food).
_SAFETY_DOWNGRADE_ACTIONS = frozenset({
    "skip_state_mismatch", "skip_proxy_too_broad", "skip_no_nutrition_value",
})


def batch_bucket(row: dict[str, Any]) -> str:
    outcome = row["v2_outcome"]
    action = row["safety_action"]
    if outcome == "auto_accept" and action == "would_enrich":
        # A would-enrich on a non-food is the only genuinely dangerous case.
        return "true_high_risk" if row.get("policy") == "non_food" else "auto_ready"
    if outcome in ("no_match", "policy_excluded") or action == "skip_no_match":
        return "no_match"
    if action in _SAFETY_DOWNGRADE_ACTIONS:
        return "safety_downgrade"
    return "needs_review"  # matcher review-level (route_to_review)


#: diff_bucket values. ``harmless_equivalent`` / ``safer_existing_v2`` need a
#: human judgement (the same food under a different code, or the existing being
#: the safer choice) and are left for the reviewer; the auto-assigned ones are
#: derived from the current batch's own safety/confidence below.
DIFF_BUCKETS = (
    "harmless_equivalent", "safer_existing_v2", "current_batch_better",
    "needs_manual_review", "safety_downgraded_current_batch",
)


def diff_bucket(row: dict[str, Any]) -> str:
    """Classify a product where the batch match differs from the applied V2."""
    if row.get("safety_action") != "would_enrich":
        # The current batch is itself downgraded / no-match → the existing V2
        # record should stand; this is not a reason to change anything.
        return "safety_downgraded_current_batch"
    try:
        conf = float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    # A clean, high-confidence alternative is worth a closer look; otherwise a
    # human decides whether it's equivalent / safer / better.
    return "current_batch_better" if conf >= 0.97 else "needs_manual_review"


def build_summary(
    *, run_id: str, input_path: str, raw_count: int, groups: list[dict[str, Any]],
    results: list[dict[str, Any]], sensitive: list[dict[str, str]],
    provider: str, model: str, top_k: int, generated_at: str | None,
) -> dict[str, Any]:
    buckets = [batch_bucket(r) for r in results]
    by_bucket = {b: [r for r, bk in zip(results, buckets, strict=True) if bk == b]
                 for b in _PARTITION_BUCKETS}
    policy_excluded = sum(1 for r in results if r["v2_outcome"] == "policy_excluded")

    def rows_in(*names: str) -> int:
        return sum(r["duplicate_count"] for b in names for r in by_bucket[b])

    unique = len(groups)
    dedupe_pct = round((1 - unique / raw_count) * 100, 2) if raw_count else 0.0
    true_high_risk = len(by_bucket["true_high_risk"])
    # Quality-V2-AE — investigate_high_risk only for TRUE high-risk rows.
    recommendation = ("investigate_high_risk" if true_high_risk
                      else "ready_for_human_review")
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "input_path": input_path,
        "raw_row_count": raw_count,
        "unique_product_count": unique,
        "duplicate_group_count": sum(1 for g in groups
                                     if len(g["raw_row_indices"]) > 1),
        "max_duplicate_group_size": max(
            (len(g["raw_row_indices"]) for g in groups), default=0),
        "sensitive_columns_detected": [s["column_name"] for s in sensitive],
        "embedding_provider": provider,
        "embedding_model": model,
        "top_k": top_k,
        "auto_ready_count": len(by_bucket["auto_ready"]),
        "safety_downgrade_count": len(by_bucket["safety_downgrade"]),
        "needs_review_count": len(by_bucket["needs_review"]),
        "no_match_count": len(by_bucket["no_match"]),
        "policy_excluded_count": policy_excluded,
        "true_high_risk_count": true_high_risk,
        # back-compat alias (now correctly = true high-risk only).
        "high_risk_count": true_high_risk,
        "estimated_rows_covered_by_auto_ready": rows_in("auto_ready"),
        "estimated_rows_needing_review": rows_in(
            "safety_downgrade", "needs_review", "true_high_risk"),
        "dedupe_reduction_pct": dedupe_pct,
        "recommendation": recommendation,
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def write_artifacts(out_dir: str | Path, run_id: str, *, groups, results,
                    sensitive, summary) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    dedup_rows = [{
        "canonical_product_key": g["canonical_product_key"],
        "representative_product_name": g["representative_product_name"],
        "duplicate_count": len(g["raw_row_indices"]),
        "raw_row_indices": " ".join(str(i) for i in g["raw_row_indices"]),
        "safe_fields_used": " ".join(g["safe_fields_used"]),
    } for g in groups]
    p = out / f"nevo_v2_batch_dedup_groups_{run_id}.csv"
    _write_csv(p, DEDUP_CSV_COLUMNS, dedup_rows)
    paths["dedup_groups_csv"] = str(p)

    p = out / f"nevo_v2_batch_results_{run_id}.csv"
    _write_csv(p, RESULT_CSV_COLUMNS, results)
    paths["results_csv"] = str(p)

    p = out / f"nevo_v2_batch_summary_{run_id}.json"
    p.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    paths["summary_json"] = str(p)

    review_cols = RESULT_CSV_COLUMNS + list(REVIEWER_BLANK_COLUMNS)
    for fname, bucket in _REVIEW_PACKAGES:
        sel = [{**r, **dict.fromkeys(REVIEWER_BLANK_COLUMNS, "")}
               for r in results if batch_bucket(r) == bucket]
        p = out / f"nevo_v2_batch_{fname}_{run_id}.csv"
        _write_csv(p, review_cols, sel)
        paths[f"{fname}_csv"] = str(p)

    p = out / f"nevo_v2_batch_sensitive_columns_{run_id}.csv"
    _write_csv(p, SENSITIVE_CSV_COLUMNS, sensitive)
    paths["sensitive_columns_csv"] = str(p)
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2.nevo_v2_batch_dry_run",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--cache-dir", default="/tmp/altera-quality/cache")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--project-id", default=None)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit-rows", type=int, default=None)
    ap.add_argument("--matcher-version", choices=["v2-embeddings"],
                    default="v2-embeddings")
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--reference-source", choices=["fixture", "nevo"],
                    default="nevo")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--dedupe", choices=["true", "false"], default="true")
    ap.add_argument("--sensitive-column-report", choices=["true", "false"],
                    default="true")
    ap.add_argument("--require-voyage", action="store_true",
                    help="require the real Voyage provider (embeddings enabled).")
    ap.add_argument("--evaluator-fake", action="store_true",
                    help="dev/CI only: deterministic FAKE provider.")
    ap.add_argument("--debug", action="store_true")
    return ap


def _build_matcher(args) -> tuple[Any, str, str] | int:
    try:
        get_nevo_matcher("v2-embeddings", evaluator_mode=args.evaluator_fake)
    except NevoMatcherError as exc:
        print(f"FATAL: {exc}")
        return 2
    if args.require_voyage and not embeddings_enabled():
        print("FATAL: --require-voyage needs ALTERA_ENABLE_EMBEDDINGS=true (+ "
              "VOYAGE_API_KEY).")
        return 2
    try:
        if embeddings_enabled():
            provider = build_embedding_provider("voyage", model=args.embedding_model)
            provider_name = "voyage"
        else:
            provider = build_embedding_provider("fake")
            provider_name = "fake"
        references = load_nevo_reference(args.reference_source, path=args.reference)
        cache = _make_cache(args.cache_dir, provider_name, args.embedding_model)
        index = NevoVectorIndex.load_or_build(
            references, provider=provider, provider_name=provider_name,
            top_k=args.top_k, cache=cache, batch_size=args.batch_size)
        cache.flush()
        matcher = get_nevo_matcher("v2-embeddings", index=index,
                                   evaluator_mode=args.evaluator_fake)
    except (EmbeddingProviderError, NevoMatcherError) as exc:
        print(f"FATAL: {exc}")
        if args.debug:
            traceback.print_exc()
        return 2
    return matcher, provider_name, args.embedding_model


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"FATAL: input not found: {input_path}")
        return 2
    run_id = args.run_id or re.sub(r"[^A-Za-z0-9_.-]+", "-", input_path.stem)

    with input_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    if args.limit_rows is not None:
        rows = rows[: args.limit_rows]

    sensitive = detect_sensitive_columns(fieldnames)
    sensitive_names = {s["column_name"] for s in sensitive}
    safe_cols = [c for c in fieldnames if c not in sensitive_names]
    roles = _detect_roles(safe_cols)
    if "product_name" not in roles:
        print("FATAL: no usable product-identification column "
              "(need a product_name-like column). recommendation="
              "insufficient_product_fields")
        return 2

    built = _build_matcher(args)
    if isinstance(built, int):
        return built
    matcher, provider_name, model = built

    from datetime import UTC, datetime
    generated_at = datetime.now(UTC).isoformat()

    groups = dedupe(rows, roles, enabled=args.dedupe == "true")
    results = [_match_group(g, matcher=matcher, top_k=args.top_k)
               for g in groups]
    summary = build_summary(
        run_id=run_id, input_path=str(input_path), raw_count=len(rows),
        groups=groups, results=results, sensitive=sensitive,
        provider=provider_name, model=model, top_k=args.top_k,
        generated_at=generated_at)
    paths = write_artifacts(args.output_dir, run_id, groups=groups,
                            results=results, sensitive=sensitive, summary=summary)

    print("# NEVO V2 batch DRY-RUN (no database writes)")
    print(f"  run_id={run_id} raw_rows={summary['raw_row_count']} "
          f"unique={summary['unique_product_count']} "
          f"dedupe_reduction={summary['dedupe_reduction_pct']}%")
    print(f"  sensitive_columns_excluded={summary['sensitive_columns_detected']}")
    print(f"  auto_ready={summary['auto_ready_count']} "
          f"safety_downgrade={summary['safety_downgrade_count']} "
          f"needs_review={summary['needs_review_count']} "
          f"no_match={summary['no_match_count']} "
          f"policy_excluded={summary['policy_excluded_count']} "
          f"true_high_risk={summary['true_high_risk_count']}")
    print(f"  recommendation={summary['recommendation']}")
    print(f"  Summary JSON: {paths['summary_json']}")
    print("DRY-RUN — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

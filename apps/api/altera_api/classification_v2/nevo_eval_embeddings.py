"""Phase Quality-V2-C — NEVO evaluator for the rules+embeddings pipeline.

Kept separate from ``evaluation.py`` so the lightweight rules-only
evaluator never imports the embeddings stack. Compares the V2
rules+embeddings pipeline against a fixture, reusing the same
``NevoMetrics`` shape plus embedding-specific extras (expected-match
rank, embedding-call count) and a per-candidate trace CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from altera_api.classification_v2.evaluation import Mismatch, NevoMetrics
from altera_api.classification_v2.nevo_index import NevoVectorIndex
from altera_api.classification_v2.nevo_pipeline import decide_with_embeddings
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    concept_of,
    gate_candidate,
)
from altera_api.embeddings.cache import InMemoryEmbeddingCache
from altera_api.embeddings.provider import EmbeddingProvider


def _query(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_name": case.get("product_name", ""),
        "retailer_category": case.get("retailer_category"),
        "ingredients_text": case.get("ingredients_text"),
        "labels": case.get("labels"),
    }


def _same_food(expected: dict[str, Any], code: str | None, name: str | None) -> bool:
    """A returned match is correct if its NEVO code equals the expected
    code OR it resolves to the same canonical concept (so a semantic
    retriever returning 'Milk whole' for expected 'Milk semi-skimmed'
    still counts — both are the milk concept)."""
    if code and expected.get("nevo_code") and code == expected["nevo_code"]:
        return True
    exp_concept = concept_of(str(expected.get("food_name_en", "")))
    got_concept = concept_of(str(name or ""))
    return exp_concept is not None and exp_concept == got_concept


CANDIDATES_CSV_COLUMNS = [
    "fixture_id", "product_name", "expected_match", "candidate_rank",
    "candidate_name", "similarity", "accepted", "rejection_reason",
    "final_decision",
]


def evaluate_nevo_embeddings(
    cases: list[dict[str, Any]],
    references: list[dict[str, Any]],
    provider: EmbeddingProvider,
    *,
    provider_name: str = "fake",
    top_k: int = 20,
    auto_accept_threshold: float = 0.90,
) -> tuple[NevoMetrics, list[dict[str, Any]]]:
    """Run the rules+embeddings NEVO pipeline over a fixture.

    Returns ``(metrics, candidate_rows)`` — candidate_rows feed the
    candidates CSV.
    """
    index = NevoVectorIndex(
        provider=provider, provider_name=provider_name, top_k=top_k,
        cache=InMemoryEmbeddingCache(),
    )
    index.build(references)

    m = NevoMetrics(matcher_version="v2-embeddings")
    rows: list[dict[str, Any]] = []

    for case in cases:
        m.total += 1
        name = case.get("product_name", "")
        expected = case.get("expected_match")
        should_match = bool(case.get("should_match", expected is not None))
        decision = decide_with_embeddings(_query(case), index, top_k=top_k)

        # Candidate trace rows (top 5).
        for tr in decision.top_candidates[:5]:
            rows.append(
                {
                    "fixture_id": str(case.get("id", "")),
                    "product_name": name,
                    "expected_match": (expected or {}).get("food_name_en", ""),
                    "candidate_rank": tr.rank,
                    "candidate_name": tr.candidate_name,
                    "similarity": tr.similarity,
                    "accepted": tr.accepted,
                    "rejection_reason": tr.rejection_reason,
                    "final_decision": decision.match_type,
                }
            )

        if should_match and expected:
            m.should_match_total += 1
            # Rank of the expected match among candidates → rank stats +
            # top-k recall buckets.
            for tr in decision.top_candidates:
                if _same_food(expected, tr.nevo_code, tr.candidate_name):
                    m.expected_rank_sum += tr.rank
                    m.expected_rank_count += 1
                    if tr.rank <= 1:
                        m.expected_in_top1 += 1
                    if tr.rank <= 5:
                        m.expected_in_top5 += 1
                    if tr.rank <= 10:
                        m.expected_in_top10 += 1
                    if tr.rank <= 20:
                        m.expected_in_top20 += 1
                    break
            if decision.matched:
                correct = _same_food(expected, decision.nevo_code, decision.food_name_en)
                high_conf = (
                    not decision.review_required
                    and decision.confidence >= auto_accept_threshold
                )
                if correct:
                    m.matched_correct += 1
                    if high_conf:
                        m.high_confidence_total += 1
                        m.high_confidence_correct += 1
                elif high_conf:
                    # A WRONG high-confidence (auto-accept) match — the
                    # dangerous kind the safety gate forbids.
                    m.high_confidence_total += 1
                    m.false_positive_count += 1
                    m.mismatches.append(
                        Mismatch(
                            fixture_id=str(case.get("id", "")),
                            product_name=name,
                            expected=str(expected.get("food_name_en", "")),
                            actual=str(decision.food_name_en or "—"),
                            confidence=decision.confidence,
                            source="nevo_embeddings",
                            rule_id=decision.match_type,
                            pipeline_version="v2-embeddings",
                            notes=decision.rationale,
                        )
                    )
            else:
                m.abstain_count += 1
        elif not should_match:
            # Should abstain — a high-confidence accept is a false positive.
            if decision.matched and not decision.review_required:
                m.false_positive_count += 1
                m.mismatches.append(
                    Mismatch(
                        fixture_id=str(case.get("id", "")),
                        product_name=name,
                        expected="(should abstain)",
                        actual=str(decision.food_name_en or "—"),
                        confidence=decision.confidence,
                        source="nevo_embeddings",
                        rule_id=decision.match_type,
                        pipeline_version="v2-embeddings",
                        notes="FALSE POSITIVE: matched when it should abstain",
                    )
                )
            else:
                m.abstain_count += 1

        # Forbidden candidates must be rejected by the rules.
        for forbidden in case.get("forbidden_matches", []):
            m.forbidden_total += 1
            g = gate_candidate(name, NevoCandidate("X", forbidden))
            if not g.accepted:
                m.forbidden_rejected += 1
            else:
                m.false_positive_count += 1
                m.mismatches.append(
                    Mismatch(
                        fixture_id=str(case.get("id", "")),
                        product_name=name,
                        expected="(forbidden — should reject)",
                        actual=forbidden,
                        confidence=g.confidence,
                        source="nevo_embeddings",
                        rule_id="gate_candidate",
                        pipeline_version="v2-embeddings",
                        notes="FALSE POSITIVE: forbidden candidate accepted",
                    )
                )

    m.embedding_calls = index.embedding_calls
    m.token_total = getattr(provider, "total_tokens", 0)
    return m, rows


def summarize_candidates(
    cases: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, int]:
    """Phase Quality-V2-D — failure taxonomy over the candidate trace.

    Buckets each should-match case by where its expected match landed:
    rank-1, rank 2–5, retrieved-but-rejected, missing-from-top-k; and
    counts dangerous candidates that ranked high but were correctly
    rejected (safety working as intended)."""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_case.setdefault(r["fixture_id"], []).append(r)

    tax = {
        "expected_rank_1": 0,
        "expected_rank_2_5": 0,
        "expected_retrieved_but_rejected": 0,
        "expected_missing_from_topk": 0,
        "dangerous_ranked_high_but_rejected": 0,
    }
    for case in cases:
        if not bool(case.get("should_match", case.get("expected_match") is not None)):
            continue
        expected = case.get("expected_match") or {}
        exp_name = str(expected.get("food_name_en", "")).lower()
        crows = by_case.get(str(case.get("id", "")), [])
        found = None
        for r in crows:
            if str(r["candidate_name"]).lower() == exp_name:
                found = r
                break
        if found is None:
            tax["expected_missing_from_topk"] += 1
        elif not found["accepted"]:
            tax["expected_retrieved_but_rejected"] += 1
        elif found["candidate_rank"] == 1:
            tax["expected_rank_1"] += 1
        elif found["candidate_rank"] <= 5:
            tax["expected_rank_2_5"] += 1
        # Forbidden candidates that ranked in the top 5 but were rejected.
        forbidden = {f.lower() for f in case.get("forbidden_matches", [])}
        for r in crows:
            if (
                str(r["candidate_name"]).lower() in forbidden
                and r["candidate_rank"] <= 5
                and not r["accepted"]
            ):
                tax["dangerous_ranked_high_but_rejected"] += 1
    return tax


def write_candidates_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CANDIDATES_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

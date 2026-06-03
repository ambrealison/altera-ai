"""Phase Quality-V2-C — NEVO evaluator for the rules+embeddings pipeline.

Kept separate from ``evaluation.py`` so the lightweight rules-only
evaluator never imports the embeddings stack. Compares the V2
rules+embeddings pipeline against a fixture, reusing the same
``NevoMetrics`` shape plus embedding-specific extras (expected-match
rank, embedding-call count) and a per-candidate trace CSV.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

from altera_api.classification_v2.evaluation import Mismatch, NevoMetrics
from altera_api.classification_v2.nevo_index import BuildProgressFn, NevoVectorIndex
from altera_api.classification_v2.nevo_pipeline import decide_with_embeddings
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    concept_of,
    gate_candidate,
)
from altera_api.embeddings.cache import EmbeddingCache, InMemoryEmbeddingCache
from altera_api.embeddings.provider import EmbeddingProvider

#: A query-progress callback receives (queries_done, queries_total).
QueryProgressFn = Callable[[int, int], None]


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
    "candidate_name", "candidate_code", "similarity", "accepted",
    "rejection_reason", "final_decision", "match_type", "confidence",
    "model", "provider",
]


def evaluate_nevo_embeddings(
    cases: list[dict[str, Any]],
    references: list[dict[str, Any]],
    provider: EmbeddingProvider,
    *,
    provider_name: str = "fake",
    top_k: int = 20,
    auto_accept_threshold: float = 0.90,
    cache: EmbeddingCache | None = None,
    batch_size: int = 64,
    build_progress: BuildProgressFn | None = None,
    query_progress: QueryProgressFn | None = None,
    model: str | None = None,
    index: NevoVectorIndex | None = None,
    decisions_sink: list[tuple[dict[str, Any], Any]] | None = None,
) -> tuple[NevoMetrics, list[dict[str, Any]]]:
    """Run the rules+embeddings NEVO pipeline over a fixture.

    Returns ``(metrics, candidate_rows)`` — candidate_rows feed the
    candidates CSV. References are embedded in ``batch_size`` batches via
    a (possibly persistent) ``cache``; ``build_progress`` /
    ``query_progress`` make a long full-NEVO run observable. A prebuilt
    ``index`` may be passed in (the CLI builds it first so it can report
    cache hits/misses between the build and query phases)."""
    if index is None:
        index = NevoVectorIndex.load_or_build(
            references,
            provider=provider, provider_name=provider_name, top_k=top_k,
            cache=cache if cache is not None else InMemoryEmbeddingCache(),
            batch_size=batch_size, progress=build_progress,
        )
    model_name = model or provider.model

    m = NevoMetrics(matcher_version="v2-embeddings")
    rows: list[dict[str, Any]] = []

    total_cases = len(cases)
    for ci, case in enumerate(cases):
        m.total += 1
        name = case.get("product_name", "")
        expected = case.get("expected_match")
        should_match = bool(case.get("should_match", expected is not None))
        decision = decide_with_embeddings(
            _query(case), index, top_k=top_k, full_trace=True
        )
        if decisions_sink is not None:
            decisions_sink.append((case, decision))

        # Full candidate trace rows (up to top_k) for the candidate CSV.
        for tr in decision.top_candidates:
            rows.append(
                {
                    "fixture_id": str(case.get("id", "")),
                    "product_name": name,
                    "expected_match": (expected or {}).get("food_name_en", ""),
                    "candidate_rank": tr.rank,
                    "candidate_name": tr.candidate_name,
                    "candidate_code": tr.nevo_code,
                    "similarity": tr.similarity,
                    "accepted": tr.accepted,
                    "rejection_reason": tr.rejection_reason,
                    "final_decision": decision.match_type,
                    "match_type": tr.match_type,
                    "confidence": tr.confidence,
                    "model": model_name,
                    "provider": provider_name,
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

        if query_progress is not None:
            query_progress(ci + 1, total_cases)

    m.embedding_calls = index.embedding_calls
    m.token_total = getattr(provider, "total_tokens", 0)
    return m, rows


def summarize_candidates(
    cases: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    references: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Phase Quality-V2-D/E — failure taxonomy over the candidate trace.

    Buckets each should-match case to point at the next improvement:
      * ``expected_rank_1`` / ``expected_rank_2_5`` / ``expected_rank_6_20``
        — where the right reference landed (a reranker helps 2–20).
      * ``expected_retrieved_but_rejected`` — retrieval found it but a rule
        killed it (rule/alias problem).
      * ``expected_missing_from_topk`` — in the reference table but not in
        top-k (retrieval recall / reference-text / top_k problem).
      * ``fixture_expected_not_in_reference`` — the expected food isn't in
        the loaded NEVO reference at all (reference-coverage / fixture gap;
        only computed when ``references`` is provided).
      * ``no_safe_reference`` — a should-match case with no expected match
        specified (nothing correct to retrieve).
      * ``dangerous_ranked_high_but_rejected`` — a forbidden candidate
        ranked top-5 but was correctly killed (safety working).
      * ``dangerous_incorrectly_accepted`` — a forbidden candidate that was
        ACCEPTED (a real safety failure; should always be 0).
    """
    by_case: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_case.setdefault(r["fixture_id"], []).append(r)

    ref_names: set[str] | None = None
    ref_codes: set[str] | None = None
    ref_concepts: set[str | None] = set()
    if references is not None:
        ref_names = {str(r.get("food_name_en", "")).lower() for r in references}
        ref_codes = {
            str(r.get("nevo_code", "")) for r in references if r.get("nevo_code")
        }
        ref_concepts = {concept_of(str(r.get("food_name_en", ""))) for r in references}
        ref_concepts.discard(None)

    tax = {
        "expected_rank_1": 0,
        "expected_rank_2_5": 0,
        "expected_rank_6_20": 0,
        "expected_retrieved_but_rejected": 0,
        "expected_missing_from_topk": 0,
        "fixture_expected_not_in_reference": 0,
        "no_safe_reference": 0,
        "dangerous_ranked_high_but_rejected": 0,
        "dangerous_incorrectly_accepted": 0,
    }
    for case in cases:
        if not bool(case.get("should_match", case.get("expected_match") is not None)):
            continue
        expected = case.get("expected_match") or {}
        exp_name = str(expected.get("food_name_en", "")).lower()
        exp_code = str(expected.get("nevo_code", ""))
        crows = by_case.get(str(case.get("id", "")), [])

        if not exp_name and not exp_code:
            tax["no_safe_reference"] += 1
        else:
            in_reference = True
            if ref_names is not None:
                # Concept-aware: the expected food counts as present if its
                # code, normalised name, OR canonical concept matches a
                # reference food (so "Chickpeas" aligns with NEVO's
                # "Peas chick boiled" via the chickpea concept).
                exp_concept = concept_of(str(expected.get("food_name_en", "")))
                in_reference = (
                    (bool(exp_name) and exp_name in ref_names)
                    or (bool(exp_code) and exp_code in (ref_codes or set()))
                    or (exp_concept is not None and exp_concept in ref_concepts)
                )
                if not in_reference:
                    tax["fixture_expected_not_in_reference"] += 1

            # Find the expected food among candidates using the SAME
            # code-aware / concept-aware matching as the metrics + focused
            # failure reports (a fixture label "Chickpeas" matches the real
            # NEVO candidate "Peas chick boiled" via code 1095 / the
            # chickpea concept). Matching only by exact name made every
            # expected look "missing from top-k" on the real NEVO run.
            found = None
            for r in crows:
                if _same_food(expected, r.get("candidate_code"), r.get("candidate_name")):
                    found = r
                    break
            if found is None:
                # A genuine retrieval miss only when the food IS in the
                # reference table; otherwise it's a coverage gap (counted
                # above), not something better retrieval could fix.
                if in_reference:
                    tax["expected_missing_from_topk"] += 1
            elif not found["accepted"]:
                tax["expected_retrieved_but_rejected"] += 1
            elif found["candidate_rank"] == 1:
                tax["expected_rank_1"] += 1
            elif found["candidate_rank"] <= 5:
                tax["expected_rank_2_5"] += 1
            elif found["candidate_rank"] <= 20:
                tax["expected_rank_6_20"] += 1

        # Forbidden candidates: rejected-when-high (safe) vs accepted (bad).
        forbidden = {f.lower() for f in case.get("forbidden_matches", [])}
        for r in crows:
            if str(r["candidate_name"]).lower() not in forbidden:
                continue
            if r["accepted"]:
                tax["dangerous_incorrectly_accepted"] += 1
            elif r["candidate_rank"] <= 5:
                tax["dangerous_ranked_high_but_rejected"] += 1
    return tax


def write_candidates_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CANDIDATES_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

"""Phase Quality-V2-H — refined NEVO rank-miss interpretation.

Same-concept SAFE rank misses must NOT be called ``needs_reranker``; they
are ``harmless_equivalent`` / ``expected_too_specific``. ``needs_reranker``
is reserved for a DIFFERENT-concept food accepted above the right one.
Adds ``match_relationship`` notes. No rules/gate change; V1 stays default;
embeddings disabled by default; no route imports V2/embeddings.
"""

from __future__ import annotations

from altera_api.classification_v2.nevo_diagnostics import (
    MATCH_RELATIONSHIPS,
    inspect_rank_misses,
)
from altera_api.classification_v2.nevo_pipeline import CandidateTrace, NevoDecision


def _trace(rank, name, code, sim, *, accepted, reason="", mt="alias"):
    return CandidateTrace(
        rank=rank, candidate_name=name, nevo_code=code, similarity=sim,
        accepted=accepted, match_type=mt if accepted else "rejected",
        rejection_reason=reason, confidence=0.96 if accepted else 0.0,
    )


def _decision(top, *, matched, name=None, code=None, review=False):
    return NevoDecision(
        matched=matched, nevo_code=code, food_name_en=name,
        confidence=0.96 if matched and not review else (0.6 if matched else 0.0),
        match_type="embedding_plus_rule" if matched and not review else (
            "proxy_review" if matched else "no_match"
        ),
        review_required=review, rationale="", provider="voyage", model="voyage-4-lite",
        top_candidates=top, rejected_candidates=[t for t in top if not t.accepted],
    )


def _one(case, top, *, matched, name, code):
    rank_miss, rejected = inspect_rank_misses(
        [(case, _decision(top, matched=matched, name=name, code=code))]
    )
    rows = rank_miss + rejected
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------------------
# The six real V2-G cases (modelled with their reported ranks/labels).
# ---------------------------------------------------------------------------
class TestRealCasesAreHarmless:
    def test_nve11_lentilles_corail_exact_code_rank_miss(self) -> None:
        case = {"id": "nve-11", "product_name": "Lentilles corail",
                "expected_match": {"food_name_en": "Red lentils", "nevo_code": "5174"}}
        top = [
            _trace(1, "Soup tomato", "8001", 0.8, accepted=False, reason="composite"),
            _trace(2, "Lentils red boiled", "5174", 0.74, accepted=True),
        ]
        r = _one(case, top, matched=True, name="Lentils red boiled", code="5174")
        assert r["diagnosis_bucket"] == "harmless_equivalent"
        assert r["match_relationship"] == "exact_code_rank_miss"
        assert r["accepted_same_concept_as_expected"] is True

    def test_nve16_fresh_cheese_same_concept_code_mismatch(self) -> None:
        case = {"id": "nve-16", "product_name": "Fresh cheese",
                "expected_match": {"food_name_en": "Fresh cheese quark",
                                   "nevo_code": "305"}}
        top = [
            _trace(1, "Cream cheese", "8002", 0.8, accepted=False, reason="x"),
            _trace(2, "Cheese spread", "8003", 0.78, accepted=False, reason="x"),
            _trace(3, "Bread white", "8004", 0.77, accepted=False, reason="x"),
            _trace(4, "Quark full fat", "307", 0.7, accepted=True),
        ]
        r = _one(case, top, matched=True, name="Quark full fat", code="307")
        assert r["diagnosis_bucket"] == "harmless_equivalent"
        assert r["match_relationship"] == "same_concept_code_mismatch"

    def test_nve23_fromage_accepted_more_specific(self) -> None:
        case = {"id": "nve-23", "product_name": "Fromage",
                "expected_match": {"food_name_en": "Cheese", "nevo_code": "513"}}
        top = [
            _trace(1, "Bread", "8005", 0.8, accepted=False, reason="x"),
            _trace(2, "Butter", "8006", 0.78, accepted=False, reason="x"),
            _trace(3, "Cheese Brie 60+", "1500", 0.77, accepted=True),
        ]
        r = _one(case, top, matched=True, name="Cheese Brie 60+", code="1500")
        assert r["diagnosis_bucket"] == "harmless_equivalent"
        # Accepted "Cheese Brie" is more specific than the generic "Cheese".
        assert r["match_relationship"] == "accepted_more_specific_variant"

    def test_nve27_soupe_lentilles_same_concept_variant(self) -> None:
        case = {"id": "nve-27", "product_name": "Soupe lentilles coco",
                "expected_match": {"food_name_en": "Red lentils", "nevo_code": "5174"}}
        top = [_trace(i, f"Noise {i}", f"9{i}", 0.8 - i * 0.01, accepted=False,
                      reason="composite") for i in range(1, 6)]
        top.append(_trace(6, "Lentils green and brown boiled", "970", 0.7, accepted=True))
        r = _one(case, top, matched=True,
                 name="Lentils green and brown boiled", code="970")
        assert r["diagnosis_bucket"] == "harmless_equivalent"
        assert r["match_relationship"] == "same_concept_code_mismatch"
        assert r["expected_rank"] == 6

    def test_nve20_pates_penne_rejected_harmless(self) -> None:
        case = {"id": "nve-20", "product_name": "Pates penne",
                "expected_match": {"food_name_en": "Pasta", "nevo_code": "4"}}
        top = [
            _trace(1, "Manti stuffed pasta cooked Turkish", "8100", 0.8,
                   accepted=False, reason="Candidate is a composite/prepared dish ..."),
            _trace(2, "Pasta white boiled", "659", 0.7, accepted=True),
        ]
        r = _one(case, top, matched=True, name="Pasta white boiled", code="659")
        # First same-concept candidate (the stuffed-pasta dish) was rejected,
        # but a clean pasta was accepted → harmless.
        assert r["diagnosis_bucket"] == "harmless_equivalent"
        assert r["match_relationship"] == "expected_variant_rejected"

    def test_nve22_muesli_rejected_harmless(self) -> None:
        case = {"id": "nve-22", "product_name": "Muesli maison",
                "expected_match": {"food_name_en": "Muesli", "nevo_code": "2809"}}
        top = [
            _trace(1, "Muesli bar", "8200", 0.8, accepted=False,
                   reason="Candidate is a composite/prepared dish ..."),
            _trace(2, "Muesli w fruit seeds and kernels", "2810", 0.7, accepted=True),
        ]
        r = _one(case, top, matched=True,
                 name="Muesli w fruit seeds and kernels", code="2810")
        assert r["diagnosis_bucket"] == "harmless_equivalent"


# ---------------------------------------------------------------------------
# needs_reranker is reserved for a different-concept accept above the right one.
# ---------------------------------------------------------------------------
class TestNeedsRerankerReserved:
    def test_different_concept_accepted_above_is_needs_reranker(self) -> None:
        case = {"id": "x1", "product_name": "Tofu nature",
                "expected_match": {"food_name_en": "Tofu", "nevo_code": "5519"}}
        # A different concept is accepted (review-level) at rank 1, the right
        # tofu accepted lower.
        top = [
            _trace(1, "Soy drink", "7001", 0.85, accepted=True, mt="proxy"),
            _trace(2, "Tofu unprepared", "5519", 0.7, accepted=True),
        ]
        # The decision picks the rank-1 different-concept candidate.
        r = _one(case, top, matched=True, name="Soy drink", code="7001")
        assert r["accepted_same_concept_as_expected"] is False
        assert r["diagnosis_bucket"] == "needs_reranker"
        assert r["match_relationship"] == "different_concept_ranking_noise"

    def test_expected_too_specific_bucket(self) -> None:
        case = {"id": "x2", "product_name": "Lentilles corail",
                "expected_match": {"food_name_en": "Lentils red boiled",
                                   "nevo_code": "5174"}}
        top = [
            _trace(1, "Bread", "7002", 0.85, accepted=False, reason="x"),
            _trace(2, "Lentils boiled", "970", 0.7, accepted=True),
        ]
        r = _one(case, top, matched=True, name="Lentils boiled", code="970")
        assert r["diagnosis_bucket"] == "expected_too_specific"
        assert r["match_relationship"] == "fixture_expected_too_specific"


# ---------------------------------------------------------------------------
# Acceptance: no same-concept safe equivalent is called needs_reranker.
# ---------------------------------------------------------------------------
class TestAcceptance:
    def test_no_same_concept_safe_accept_is_needs_reranker(self) -> None:
        from altera_api.classification_v2.evaluation import load_fixture
        from altera_api.classification_v2.nevo_eval_embeddings import (
            evaluate_nevo_embeddings,
        )
        from altera_api.classification_v2.nevo_index import load_nevo_reference
        from altera_api.embeddings import FakeEmbeddingProvider

        cases = load_fixture("altera_api/data/eval/nevo/nevo_dataset_embeddings.json")
        refs = load_nevo_reference("nevo")
        decisions: list = []
        evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake",
            top_k=20, model="fake", decisions_sink=decisions,
        )
        rank_miss, rejected = inspect_rank_misses(decisions)
        for r in [*rank_miss, *rejected]:
            if r["accepted_same_concept_as_expected"]:
                assert r["diagnosis_bucket"] != "needs_reranker", r
            assert r["match_relationship"] in MATCH_RELATIONSHIPS

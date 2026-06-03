"""Phase Quality-V2-G — NEVO rank-miss inspection (pre-reranker).

Two focused reports: cases where the expected reference was retrieved but
not rank-1 (rank misses) and cases where it was retrieved but rejected by
the rules. All offline (crafted decisions / fake provider). No rules/gate
change; V1 stays default; embeddings disabled by default; no route imports
V2/embeddings.
"""

from __future__ import annotations

from pathlib import Path

from altera_api.classification_v2 import benchmark_nevo_embeddings as cli
from altera_api.classification_v2.evaluation import load_fixture
from altera_api.classification_v2.nevo_diagnostics import (
    DIAGNOSIS_BUCKETS,
    RANK_INSPECTION_CSV_COLUMNS,
    _diagnosis_bucket,
    inspect_rank_misses,
    write_rank_inspection_reports,
)
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
)
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_pipeline import CandidateTrace, NevoDecision
from altera_api.embeddings import FakeEmbeddingProvider

_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"


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
        review_required=review, rationale="", provider="fake", model="m",
        top_candidates=top, rejected_candidates=[t for t in top if not t.accepted],
    )


# ---------------------------------------------------------------------------
# _diagnosis_bucket — direct unit coverage of all six buckets.
# ---------------------------------------------------------------------------
class TestDiagnosisBucket:
    def test_all_buckets_are_declared(self) -> None:
        assert set(DIAGNOSIS_BUCKETS) == {
            "harmless_equivalent", "expected_too_specific", "rule_too_strict",
            "true_ranking_issue", "fixture_should_change", "needs_reranker",
        }

    def test_needs_reranker_when_right_food_ranked_low(self) -> None:
        exp = _trace(3, "Tofu unprepared", "5519", 0.6, accepted=True)
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=exp, accepted_same_concept=True
        ) == "needs_reranker"

    def test_harmless_equivalent_when_equivalent_accepted_above(self) -> None:
        exp = _trace(3, "Quark low fat", "305", 0.6, accepted=True)
        acc = _trace(1, "Quark half fat", "306", 0.8, accepted=True)
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=acc, accepted_same_concept=True
        ) == "harmless_equivalent"

    def test_expected_too_specific_when_broader_accepted_above(self) -> None:
        exp = _trace(3, "Lentils red boiled", "5174", 0.6, accepted=True)
        acc = _trace(1, "Lentils boiled", "970", 0.8, accepted=True)
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=acc, accepted_same_concept=True
        ) == "expected_too_specific"

    def test_true_ranking_issue_when_accepted_different_concept(self) -> None:
        exp = _trace(3, "Tofu unprepared", "5519", 0.6, accepted=True)
        acc = _trace(1, "Bread white", "999", 0.9, accepted=True)
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=acc, accepted_same_concept=False
        ) == "true_ranking_issue"

    def test_rule_too_strict_when_composite_rejected_and_no_equivalent(self) -> None:
        exp = _trace(1, "Lentil soup canned", "9001", 0.8, accepted=False,
                     reason="Candidate is a composite/prepared dish ...")
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=None, accepted_same_concept=False
        ) == "rule_too_strict"

    def test_harmless_equivalent_when_rejected_but_equivalent_accepted(self) -> None:
        exp = _trace(1, "Lentil soup canned", "9001", 0.8, accepted=False,
                     reason="composite")
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=None, accepted_same_concept=True
        ) == "harmless_equivalent"

    def test_fixture_should_change_when_rejected_non_composite(self) -> None:
        exp = _trace(1, "Weird entry", "Q1", 0.7, accepted=False,
                     reason="No safe head/concept match → abstain.")
        assert _diagnosis_bucket(
            exp_trace=exp, accepted_trace=None, accepted_same_concept=False
        ) == "fixture_should_change"


# ---------------------------------------------------------------------------
# inspect_rank_misses — integration over crafted decisions.
# ---------------------------------------------------------------------------
class TestInspectRankMisses:
    def _rank_miss_case(self):
        # Expected chickpea at rank 2 (different-concept noise at rank 1).
        top = [
            _trace(1, "Beef stew", "8001", 0.80, accepted=False, reason="composite"),
            _trace(2, "Peas chick canned", "3185", 0.72, accepted=True),
        ]
        case = {"id": "rm1", "product_name": "Curry pois chiches",
                "expected_match": {"food_name_en": "Chickpeas", "nevo_code": "1095"},
                "should_match": True}
        dec = _decision(top, matched=True, name="Peas chick canned", code="3185")
        return case, dec

    def _rejected_case(self, *, equivalent_accepted: bool):
        top = [
            _trace(1, "Lentil soup canned", "9001", 0.80, accepted=False,
                   reason="Candidate is a composite/prepared dish ..."),
        ]
        name = code = None
        matched = False
        if equivalent_accepted:
            top.append(_trace(2, "Lentils red boiled", "5174", 0.7, accepted=True))
            name, code, matched = "Lentils red boiled", "5174", True
        case = {"id": "rj1", "product_name": "Soupe lentilles coco",
                "expected_match": {"food_name_en": "Red lentils", "nevo_code": "5174"},
                "should_match": True}
        return case, _decision(top, matched=matched, name=name, code=code)

    def test_rank_miss_isolated(self) -> None:
        records = [self._rank_miss_case()]
        rank_miss, rejected = inspect_rank_misses(records)
        assert len(rank_miss) == 1 and not rejected
        r = rank_miss[0]
        assert r["expected_rank"] == 2
        assert r["expected_candidate_name"] == "Peas chick canned"
        assert r["accepted_candidate_name"] == "Peas chick canned"
        assert r["accepted_same_concept_as_expected"] is True
        assert r["diagnosis_bucket"] == "needs_reranker"
        assert "Beef stew" in r["top_5_candidate_names"]

    def test_rejected_isolated_rule_too_strict(self) -> None:
        records = [self._rejected_case(equivalent_accepted=False)]
        rank_miss, rejected = inspect_rank_misses(records)
        assert len(rejected) == 1 and not rank_miss
        r = rejected[0]
        assert r["expected_rank"] == 1
        assert "composite" in r["expected_rejection_reason"].lower()
        assert r["diagnosis_bucket"] == "rule_too_strict"

    def test_rejected_harmless_when_equivalent_accepted(self) -> None:
        records = [self._rejected_case(equivalent_accepted=True)]
        _rank_miss, rejected = inspect_rank_misses(records)
        assert rejected[0]["diagnosis_bucket"] == "harmless_equivalent"

    def test_rank_1_case_excluded(self) -> None:
        top = [_trace(1, "Tofu unprepared", "5519", 0.95, accepted=True)]
        case = {"id": "ok1", "product_name": "Tofu nature",
                "expected_match": {"food_name_en": "Tofu", "nevo_code": "5519"},
                "should_match": True}
        rank_miss, rejected = inspect_rank_misses(
            [(case, _decision(top, matched=True, name="Tofu unprepared", code="5519"))]
        )
        assert not rank_miss and not rejected

    def test_should_abstain_case_excluded(self) -> None:
        top = [_trace(1, "Some dish", "1", 0.5, accepted=False, reason="composite")]
        case = {"id": "ab1", "product_name": "Menu du jour",
                "expected_match": None, "should_match": False}
        rank_miss, rejected = inspect_rank_misses([(case, _decision(top, matched=False))])
        assert not rank_miss and not rejected


# ---------------------------------------------------------------------------
# Counts mirror the taxonomy; CSVs are written with the full column set.
# ---------------------------------------------------------------------------
class TestCountsAndCsv:
    def _run(self):
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        decisions: list = []
        _m, rows = evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake",
            top_k=20, model="fake", decisions_sink=decisions,
        )
        tax = summarize_candidates(cases, rows, refs)
        return decisions, tax

    def test_counts_mirror_taxonomy(self) -> None:
        decisions, tax = self._run()
        rank_miss, rejected = inspect_rank_misses(decisions)
        assert len(rank_miss) == tax["expected_rank_2_5"] + tax["expected_rank_6_20"]
        assert len(rejected) == tax["expected_retrieved_but_rejected"]

    def test_csvs_written_with_columns(self, tmp_path: Path) -> None:
        decisions, _tax = self._run()
        rank_miss, rejected = inspect_rank_misses(decisions)
        counts = write_rank_inspection_reports(tmp_path, "fake", rank_miss, rejected)
        for fname in (
            "nevo_rank_misses_fake.csv",
            "nevo_expected_retrieved_but_rejected_fake.csv",
        ):
            assert (tmp_path / fname).exists()
            header = (tmp_path / fname).read_text().splitlines()[0]
            assert header == ",".join(RANK_INSPECTION_CSV_COLUMNS)
            assert fname in counts

    def test_every_row_has_a_known_bucket(self) -> None:
        decisions, _tax = self._run()
        rank_miss, rejected = inspect_rank_misses(decisions)
        for r in [*rank_miss, *rejected]:
            assert r["diagnosis_bucket"] in DIAGNOSIS_BUCKETS


# ---------------------------------------------------------------------------
# Safety — gates unchanged, routes clean, CLI smoke.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_gates_unchanged_on_full_nevo_fake(self) -> None:
        from altera_api.classification_v2.evaluation import nevo_gates

        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        m, _rows = evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake", top_k=20
        )
        assert m.false_positive_count == 0
        assert nevo_gates(m)["passed"]

    def test_routes_do_not_import_v2(self) -> None:
        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_cli_smoke_writes_rank_reports(self, tmp_path: Path) -> None:
        rc = cli.main(
            ["--models", "fake", "--reference-source", "nevo",
             "--limit-references", "300", "--limit-cases", "12",
             "--cache-dir", "", "--output-dir", str(tmp_path)]
        )
        assert rc in (0, 1)
        assert (tmp_path / "nevo_rank_misses_fake.csv").exists()
        assert (tmp_path / "nevo_expected_retrieved_but_rejected_fake.csv").exists()

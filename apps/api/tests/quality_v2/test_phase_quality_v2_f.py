"""Phase Quality-V2-F — multi-word concept extraction, safe preparation
states vs composite dishes, HC-FP hardening, failure diagnostics, fixture
validator, and reference-text aliases.

All offline (fake provider / shipped CSV). No real Voyage, no network.
V1 stays the production default; no production route imports V2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altera_api.classification_v2 import benchmark_nevo_embeddings as cli
from altera_api.classification_v2 import validate_nevo_fixtures as validator
from altera_api.classification_v2.evaluation import load_fixture
from altera_api.classification_v2.nevo_diagnostics import (
    build_diagnosis_rows,
    write_failure_reports,
)
from altera_api.classification_v2.nevo_eval_embeddings import (
    evaluate_nevo_embeddings,
    summarize_candidates,
)
from altera_api.classification_v2.nevo_index import load_nevo_reference
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    _head_concept,
    _is_composite,
    concept_of,
    gate_candidate,
)
from altera_api.embeddings import FakeEmbeddingProvider
from altera_api.embeddings.text_builder import (
    ForbiddenEmbeddingField,
    build_nevo_reference_text,
)

_FIXTURE = "altera_api/data/eval/nevo/nevo_dataset_embeddings.json"


def _g(product: str, candidate: str):
    return gate_candidate(product, NevoCandidate("X", candidate))


# ---------------------------------------------------------------------------
# PART B — multi-word concept / alias extraction.
# ---------------------------------------------------------------------------
class TestMultiWordConcepts:
    def test_pois_chiches_is_chickpea_not_pois(self) -> None:
        assert concept_of("Pois chiches") == "chickpea"

    def test_nevo_inverted_names_map_to_concept(self) -> None:
        assert concept_of("Peas chick boiled") == "chickpea"
        assert concept_of("Beans black canned") == "black_bean"
        assert concept_of("Lentils red boiled") == "lentil"

    def test_pois_chiches_matches_nevo_chickpea(self) -> None:
        r = _g("Pois chiches", "Peas chick boiled")
        assert r.accepted and r.match_type == "alias"

    def test_curry_pois_chiches_matches_chickpea(self) -> None:
        assert _g("Curry pois chiches", "Peas chick canned").accepted

    def test_longest_phrase_wins(self) -> None:
        # "beurre de cacahuete" → peanut_butter, not the bare "beurre"/butter.
        assert concept_of("Beurre de cacahuete") == "peanut_butter"
        assert not _g("Beurre de cacahuete", "Butter").accepted

    def test_single_token_fallback_still_works(self) -> None:
        assert _g("Tofu nature", "Tofu unprepared").accepted
        assert concept_of("Riz basmati") == "rice"


# ---------------------------------------------------------------------------
# PART C — safe preparation states vs unsafe composite dishes.
# ---------------------------------------------------------------------------
class TestPreparationVsComposite:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Pois chiches", "Peas chick boiled"),
            ("Pois chiches", "Peas chick canned"),
            ("Lentilles corail", "Lentils red boiled"),
            ("Haricots noirs", "Beans black canned"),
            ("Tomate fraiche", "Tomatoes classic round raw"),
        ],
    )
    def test_preparation_states_not_rejected_as_composite(self, product, candidate):
        assert not _is_composite(candidate)
        assert _g(product, candidate).accepted

    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Pois chiches", "Hummus with chickpeas"),
            ("Lait demi-ecreme", "Potatoes mashed with milk"),
            ("Beurre doux", "Apple pie without butter"),
            ("Pomme", "Apple pie without sugar"),
            ("Tomate fraiche", "Soup with tomato"),
            ("Muesli maison", "Muesli bar"),
        ],
    )
    def test_composite_dishes_rejected(self, product, candidate):
        assert _is_composite(candidate)
        assert not _g(product, candidate).accepted

    def test_ratatouille_dish_still_matches_ratatouille(self) -> None:
        # The product itself is the dish → same-concept composite is OK.
        assert _g("Ratatouille", "Ratatouille prepared wo meat").accepted

    def test_head_concept_ignores_trailing_ingredient(self) -> None:
        # The head is the leading food, not the trailing ingredient. After
        # V2-K, "hummus" is its own concept (the dish head), so a chickpea
        # product still rejects "Hummus with chickpeas" because the head
        # concept (hummus) != the product concept (chickpea).
        assert _head_concept("Hummus with chickpeas") == "hummus"
        assert not _g("Pois chiches", "Hummus with chickpeas").accepted
        assert _head_concept("Apple pie without sugar") is None
        assert _head_concept("Peas chick boiled") == "chickpea"


# ---------------------------------------------------------------------------
# PART D — HC-FP hardening: no high-confidence accept without concept/head
# agreement; traps stay rejected.
# ---------------------------------------------------------------------------
class TestHighConfFalsePositiveHardening:
    def test_subtoken_match_with_concept_rejected(self) -> None:
        # 'peanut' present but candidate is a biscuit → not the concept.
        assert not _g("Peanut butter", "Biscuit peanut").accepted

    def test_literal_token_without_concept_goes_to_review_not_accept(self) -> None:
        # Product has no mapped concept; the head token appears only as a
        # secondary token of an unrelated food → REVIEW, never auto-accept.
        r = _g("Surprise box menu", "Beef with menu sauce")
        assert not r.accepted  # never a high-confidence accept

    def test_oil_trap_rejected_for_non_oil_product(self) -> None:
        assert not _g("Ratatouille a l'huile d'olive", "Oil olive").accepted

    def test_traps_zero_false_positives_on_full_nevo(self) -> None:
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        m, _rows = evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake", top_k=20
        )
        assert m.false_positive_count == 0


# ---------------------------------------------------------------------------
# PART A — failure diagnostics.
# ---------------------------------------------------------------------------
class TestFailureDiagnostics:
    def _run(self, source="nevo"):
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference(source)
        decisions: list = []
        evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake",
            top_k=20, model="fake", decisions_sink=decisions,
        )
        return cases, refs, decisions

    def test_diagnosis_rows_and_failure_csvs_written(self, tmp_path: Path) -> None:
        _cases, refs, decisions = self._run()
        rows = build_diagnosis_rows(decisions, refs)
        assert rows and all("taxonomy_bucket" in r for r in rows)
        counts = write_failure_reports(tmp_path, "fake", rows)
        for fname in (
            "nevo_failures_fake.csv",
            "nevo_high_conf_false_positives_fake.csv",
            "nevo_expected_missing_topk_fake.csv",
            "nevo_fixture_expected_not_in_reference_fake.csv",
            "nevo_abstains_fake.csv",
        ):
            assert (tmp_path / fname).exists()
            assert fname in counts

    def test_rows_carry_accepted_and_top_candidates(self, tmp_path: Path) -> None:
        _cases, refs, decisions = self._run()
        rows = build_diagnosis_rows(decisions, refs)
        r = rows[0]
        for col in ("accepted_candidate_name", "top_1_candidate_name",
                    "top_5_candidate_names", "expected_exists_in_reference"):
            assert col in r

    def test_hc_fp_isolation_is_empty_when_none(self, tmp_path: Path) -> None:
        _cases, refs, decisions = self._run()
        rows = build_diagnosis_rows(decisions, refs)
        write_failure_reports(tmp_path, "fake", rows)
        text = (tmp_path / "nevo_high_conf_false_positives_fake.csv").read_text()
        # Header only — no HC-FP rows (gates hold).
        assert len(text.strip().splitlines()) == 1

    def test_expected_not_in_reference_isolated(self, tmp_path: Path) -> None:
        # On the curated reference, build a case whose expected is absent.
        cases = [{"id": "x1", "product_name": "Dragonfruit",
                  "expected_match": {"food_name_en": "Dragonfruit", "nevo_code": "ZZZ"},
                  "should_match": True}]
        refs = load_nevo_reference("fixture")
        decisions: list = []
        evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake",
            top_k=20, decisions_sink=decisions,
        )
        rows = build_diagnosis_rows(decisions, refs)
        assert rows[0]["taxonomy_bucket"] == "fixture_expected_not_in_reference"
        assert rows[0]["expected_exists_in_reference"] is False


# ---------------------------------------------------------------------------
# PART E — fixture validator + alignment.
# ---------------------------------------------------------------------------
class TestFixtureValidator:
    def test_validator_marks_aligned_cases_valid(self) -> None:
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        rows = validator.validate(cases, refs)
        actions = {r["fixture_id"]: r["suggested_action"] for r in rows}
        # Realigned should-match cases resolve by real NEVO code.
        assert actions["nve-1"] == "valid"
        assert actions["nve-29"] == "valid_should_abstain"

    def test_validator_flags_absent_expected(self) -> None:
        cases = [{"id": "z1", "product_name": "Dragonfruit",
                  "expected_match": {"food_name_en": "Dragonfruit", "nevo_code": "ZZZ"},
                  "should_match": True}]
        refs = load_nevo_reference("nevo")
        rows = validator.validate(cases, refs)
        assert rows[0]["suggested_action"] in (
            "expected_reference_absent", "ambiguous"
        )

    def test_validator_does_not_mark_trap_products_as_valid_match(self) -> None:
        # A should-abstain product must never be told it has a valid match.
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        rows = {r["fixture_id"]: r for r in validator.validate(cases, refs)}
        for fid in ("nve-29", "nve-30", "nve-31", "nve-32", "nve-33"):
            assert rows[fid]["suggested_action"] == "valid_should_abstain"

    def test_fixture_expected_not_in_reference_is_zero(self) -> None:
        cases = load_fixture(_FIXTURE)
        refs = load_nevo_reference("nevo")
        _m, rows = evaluate_nevo_embeddings(
            cases, refs, FakeEmbeddingProvider(), provider_name="fake", top_k=20
        )
        tax = summarize_candidates(cases, rows, refs)
        assert tax["fixture_expected_not_in_reference"] == 0


# ---------------------------------------------------------------------------
# PART F — reference text aliases.
# ---------------------------------------------------------------------------
class TestReferenceText:
    def test_simple_food_gets_aliases(self) -> None:
        text = build_nevo_reference_text({"food_name_en": "Peas chick boiled"})
        assert "chickpeas" in text.lower()
        assert "pois chiches" in text.lower()

    def test_composite_food_gets_no_aliases(self) -> None:
        # A dish containing chickpeas must not inherit the chickpea aliases.
        text = build_nevo_reference_text({"food_name_en": "Hummus with chickpeas"})
        assert "pois chiches" not in text.lower()

    def test_reference_text_excludes_commercial(self) -> None:
        with pytest.raises(ForbiddenEmbeddingField):
            build_nevo_reference_text({"food_name_en": "Tofu", "price": 3})

    def test_aliases_improve_expected_rank(self) -> None:
        # With aliases, the fake retriever ranks the chickpea reference for
        # a "Pois chiches" query above an unrelated food.
        from altera_api.classification_v2.nevo_index import NevoVectorIndex

        refs = [
            {"food_name_en": "Peas chick boiled", "nevo_code": "1095"},
            {"food_name_en": "Wheat bread white", "nevo_code": "999"},
        ]
        idx = NevoVectorIndex(provider=FakeEmbeddingProvider(), provider_name="fake")
        idx.build(refs)
        results = idx.search("Name: Pois chiches", top_k=2)
        assert results[0].candidate.food_name_en == "Peas chick boiled"


# ---------------------------------------------------------------------------
# Hotfix — taxonomy uses code/concept-aware matching (label != NEVO label).
# ---------------------------------------------------------------------------
class TestTaxonomyCodeConceptAware:
    """The fixture label ("Chickpeas") differs from the real NEVO candidate
    label ("Peas chick boiled"); the taxonomy must still find the expected
    food by nevo_code / concept, not report it missing-from-top-k."""

    def _rows(self, *, cand_name, cand_code, rank=1, accepted=True):
        return [
            {
                "fixture_id": "t1", "product_name": "Pois chiches",
                "expected_match": "Chickpeas", "candidate_rank": rank,
                "candidate_name": cand_name, "candidate_code": cand_code,
                "similarity": 0.9, "accepted": accepted, "rejection_reason": "",
                "final_decision": "embedding_plus_rule", "match_type": "alias",
                "confidence": 0.96, "model": "voyage-4-lite", "provider": "voyage",
            }
        ]

    def test_code_match_with_different_label_is_rank_1(self) -> None:
        cases = [{"id": "t1", "product_name": "Pois chiches",
                  "expected_match": {"food_name_en": "Chickpeas", "nevo_code": "1095"},
                  "should_match": True}]
        rows = self._rows(cand_name="Peas chick boiled", cand_code="1095")
        refs = [{"food_name_en": "Peas chick boiled", "nevo_code": "1095"}]
        tax = summarize_candidates(cases, rows, refs)
        assert tax["expected_rank_1"] == 1
        assert tax["expected_missing_from_topk"] == 0
        assert tax["fixture_expected_not_in_reference"] == 0

    def test_concept_match_without_code_match_is_found(self) -> None:
        # Synthetic expected code that does NOT equal the candidate code —
        # only the chickpea concept links them.
        cases = [{"id": "t1", "product_name": "Pois chiches",
                  "expected_match": {"food_name_en": "Chickpeas",
                                     "nevo_code": "NEVO-CHICKPEA"},
                  "should_match": True}]
        rows = self._rows(cand_name="Peas chick canned", cand_code="3185", rank=3)
        refs = [{"food_name_en": "Peas chick canned", "nevo_code": "3185"}]
        tax = summarize_candidates(cases, rows, refs)
        assert tax["expected_rank_2_5"] == 1
        assert tax["expected_missing_from_topk"] == 0

    def test_genuinely_unrelated_candidate_is_missing(self) -> None:
        cases = [{"id": "t1", "product_name": "Pois chiches",
                  "expected_match": {"food_name_en": "Chickpeas", "nevo_code": "1095"},
                  "should_match": True}]
        rows = self._rows(cand_name="Wheat bread white", cand_code="999")
        refs = [{"food_name_en": "Peas chick boiled", "nevo_code": "1095"}]
        tax = summarize_candidates(cases, rows, refs)
        # Expected IS in the reference but retrieval returned an unrelated
        # food → genuine retrieval miss.
        assert tax["expected_missing_from_topk"] == 1
        assert tax["expected_rank_1"] == 0

    def test_taxonomy_matches_topk_metrics_on_aligned_run(self) -> None:
        # Simulate a perfect-retrieval real run: every expected food is
        # retrieved at rank 1 under its NEVO label. Taxonomy rank-1 count
        # must equal the should-match total (consistent with top1=100%).
        cases = load_fixture(_FIXTURE)
        should = [c for c in cases if c.get("should_match", bool(c.get("expected_match")))]
        rows = []
        for c in should:
            em = c["expected_match"]
            rows.append({
                "fixture_id": str(c["id"]), "product_name": c["product_name"],
                "expected_match": em["food_name_en"], "candidate_rank": 1,
                "candidate_name": em.get("nevo_reference_name", em["food_name_en"]),
                "candidate_code": em["nevo_code"], "similarity": 0.95,
                "accepted": True, "rejection_reason": "",
                "final_decision": "embedding_plus_rule", "match_type": "alias",
                "confidence": 0.96, "model": "voyage-4-lite", "provider": "voyage",
            })
        refs = load_nevo_reference("nevo")
        tax = summarize_candidates(should, rows, refs)
        assert tax["expected_rank_1"] == len(should)
        assert tax["expected_missing_from_topk"] == 0
        assert tax["fixture_expected_not_in_reference"] == 0


# ---------------------------------------------------------------------------
# Safety — production stays on V1; routes don't import V2.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_routes_do_not_import_v2(self) -> None:
        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, f"routes import V2/embeddings: {offenders}"

    def test_fake_benchmark_smoke(self, tmp_path: Path) -> None:
        rc = cli.main(
            ["--models", "fake", "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path)]
        )
        assert rc in (0, 1)
        assert (tmp_path / "nevo_failures_fake.csv").exists()

"""Phase Quality-V2-AI — persistent FR/DE multilingual NEVO reference.

Manual product-level review does not scale to ~30k FR/DE retailer rows; this
phase materializes FR/DE NEVO names + aliases ONCE into a generated artifact,
validates it (never collapsing a food state/form), and lets V2 retrieval use it
behind an explicit flag. Original NEVO names/codes/nutrition stay canonical; no
DB writes; V1 default; embeddings off; no route imports it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import (
    compare_nevo_multilingual_retrieval as bench,
)
from altera_api.classification_v2 import (
    generate_nevo_multilingual_reference as gen,
)
from altera_api.classification_v2 import nevo_v2_project_batch_dry_run as proj
from altera_api.classification_v2 import (
    validate_nevo_multilingual_reference as val,
)
from altera_api.classification_v2.compare_nevo_v1_v2 import _make_cache
from altera_api.classification_v2.nevo_index import (
    NevoVectorIndex,
    load_nevo_reference,
)
from altera_api.classification_v2.nevo_multilingual_reference import (
    ML_COLUMNS,
    DeterministicTranslator,
    build_multilingual_reference_text,
    generate_rows,
    multilingual_reference_checksum,
)
from altera_api.embeddings.fake_provider import FakeEmbeddingProvider


def _ml_row(**kw):
    return {c: "" for c in ML_COLUMNS} | kw


def _write_artifact(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ML_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Translator + embedding text (Parts C/E).
# ---------------------------------------------------------------------------
class TestTranslatorAndText:
    @pytest.mark.parametrize("name,fr_mark,de_mark", [
        ("Lentils dried", "sèch", "getrocknet"),
        ("Rice drink", "boisson", "reisdrink"),
        ("Coffee instant powder", "instantané", "kaffee"),
        ("Oil rapeseed", "colza", "raps"),
        ("Vinegar Balsamic", "balsamique", "balsamico"),
        ("Milk powder", "poudre", "pulver"),
        # Oil types (Render hotfix — oil-type collapse).
        ("Oil peanut", "arachide", "erdnuss"),
        ("Oil soya", "soja", "soja"),
        ("Oil soy", "soja", "soja"),
        ("Oil Becel Blend Classic", "mélange", "misch"),
        ("Oil corn", "maïs", "mais"),
        ("Oil coconut", "coco", "kokos"),
        ("Oil sesame", "sésame", "sesam"),
        ("Oil palm", "palme", "palm"),
        ("Oil canola", "colza", "raps"),
    ])
    def test_state_form_preserved(self, name, fr_mark, de_mark) -> None:
        tr = DeterministicTranslator().translate(name)
        assert fr_mark in tr.food_name_fr.lower()
        assert de_mark in tr.food_name_de.lower()

    def test_oil_type_products_validate_clean(self) -> None:
        # The Render full-NEVO run flagged these specific oil products with
        # oil-type state_collapse; they must now validate with zero high risk.
        names = ["Oil peanut", "Oil soya", "Oil Becel Blend Classic",
                 "Oil corn", "Oil coconut", "Oil sesame", "Oil palm",
                 "Oil soy", "Oil canola",
                 "Plant-based alternative to Gouda cheese based on coconut oil",
                 "Plant-based alternative to Gouda cheese based on coconut oil "
                 "fortified w Ca and Vit B12"]
        refs = [{"nevo_code": f"N{i}", "food_name_en": n}
                for i, n in enumerate(names)]
        rows = generate_rows(refs, translator=DeterministicTranslator())
        out = val.validate_rows(rows)
        assert out["summary"]["high_risk_translation_issue_count"] == 0
        assert (out["summary"]["recommendation"]
                in ("ready_for_retrieval_experiment",
                    "needs_translation_review"))

    def test_coconut_oil_marker_in_gouda_products(self) -> None:
        for name in (
            "Plant-based alternative to Gouda cheese based on coconut oil",
            "Plant-based alternative to Gouda cheese based on coconut oil "
            "fortified w Ca and Vit B12",
        ):
            tr = DeterministicTranslator().translate(name)
            assert "coco" in tr.food_name_fr.lower()
            assert "kokos" in tr.food_name_de.lower()

    def test_unknown_food_is_needs_review(self) -> None:
        tr = DeterministicTranslator().translate("Mystery xyzzy product")
        assert tr.review_status == "needs_review"
        assert tr.source == "unavailable"
        assert tr.food_name_fr == ""

    def test_text_original_only_unchanged(self) -> None:
        assert build_multilingual_reference_text(
            {"nevo_food_name": "Rice"}) == "Rice"
        assert build_multilingual_reference_text(
            {"food_name_en": "Rice"}) == "Rice"

    def test_text_includes_original_fr_de_aliases(self) -> None:
        txt = build_multilingual_reference_text(_ml_row(
            nevo_food_name="Lentils dried", nevo_food_name_fr="lentilles sèches",
            nevo_food_name_de="getrocknete Linsen",
            search_aliases_fr="lentilles sèches;lentilles;lentilles",
            search_aliases_de="Linsen", search_aliases_en="dried lentils"))
        assert txt.startswith("Lentils dried")
        assert "FR: lentilles sèches" in txt and "DE: getrocknete Linsen" in txt
        assert "EN aliases: dried lentils" in txt
        # duplicate alias removed.
        assert txt.count("lentilles sèches") == 1


# ---------------------------------------------------------------------------
# Generation (Part C).
# ---------------------------------------------------------------------------
class TestGenerate:
    def test_preserves_code_name_nutrition(self) -> None:
        refs = [{"nevo_code": "N1", "food_name_en": "Lentils dried",
                 "protein_g_per_100g": "24.3"},
                {"nevo_code": "N2", "food_name_en": "Rice drink"}]
        rows = generate_rows(refs, translator=DeterministicTranslator())
        assert [r["nevo_code"] for r in rows] == ["N1", "N2"]
        assert rows[0]["nevo_food_name"] == "Lentils dried"  # exact.
        assert rows[0]["protein_g_per_100g"] == "24.3"
        assert "sèch" in rows[0]["nevo_food_name_fr"].lower()

    def test_resume_only_missing(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        artifact = tmp_path / "nevo_reference_multilingual.csv"
        # Hand-edit one row to a manual translation; mark a second blank.
        rows = list(csv.DictReader(artifact.open(encoding="utf-8-sig")))
        rows[0]["nevo_food_name_fr"] = "MANUAL_FR"
        rows[0]["nevo_food_name_de"] = "MANUAL_DE"
        _write_artifact(artifact, rows)
        # Re-run with --only-missing: the manual row must be carried over.
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm", "--only-missing",
                  "--resume-from-existing", str(artifact),
                  "--output-reference", str(tmp_path / "out.csv")])
        out = list(csv.DictReader((tmp_path / "out.csv").open(
            encoding="utf-8-sig")))
        by_code = {r["nevo_code"]: r for r in out}
        assert by_code[rows[0]["nevo_code"]]["nevo_food_name_fr"] == "MANUAL_FR"

    def test_summary_written(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        s = json.loads((tmp_path
                        / "nevo_reference_multilingual_summary.json"
                        ).read_text())
        assert s["total_rows"] > 0
        assert "count_by_translation_source" in s
        assert (tmp_path / "nevo_reference_multilingual_review_sample.csv"
                ).exists()


# ---------------------------------------------------------------------------
# Validation (Part D).
# ---------------------------------------------------------------------------
class TestValidator:
    def test_catches_duplicate_code(self) -> None:
        rows = [_ml_row(nevo_code="X", nevo_food_name="Rice",
                        nevo_food_name_fr="riz", nevo_food_name_de="Reis"),
                _ml_row(nevo_code="X", nevo_food_name="Lentils",
                        nevo_food_name_fr="lentilles", nevo_food_name_de="Linsen")]
        out = val.validate_rows(rows)
        types = {i["issue_type"] for i in out["issues"]}
        assert "duplicate_code" in types

    def test_catches_state_collapse(self) -> None:
        rows = [_ml_row(nevo_code="1", nevo_food_name="Rice drink",
                        nevo_food_name_fr="riz", nevo_food_name_de="Reis")]
        out = val.validate_rows(rows)
        assert out["summary"]["high_risk_translation_issue_count"] >= 1
        assert (out["summary"]["recommendation"]
                == "blocked_by_high_risk_translation_issues")

    def test_catches_oil_type_collapse(self) -> None:
        rows = [_ml_row(nevo_code="1", nevo_food_name="Oil rapeseed",
                        nevo_food_name_fr="huile", nevo_food_name_de="Öl")]
        out = val.validate_rows(rows)
        assert any(i["issue_type"] == "state_collapse"
                   and "oil type" in i["message"] for i in out["issues"])

    def test_low_coverage_needs_review(self) -> None:
        rows = [_ml_row(nevo_code=str(i), nevo_food_name=f"Food {i}")
                for i in range(10)]  # no FR/DE at all.
        out = val.validate_rows(rows)
        assert out["summary"]["recommendation"] == "needs_translation_review"

    def test_commercial_alias_is_whole_word(self) -> None:
        # "beans" must NOT trip on the "ean" substring; "unit price" must.
        ok = val.validate_rows([_ml_row(
            nevo_code="1", nevo_food_name="Beans", nevo_food_name_fr="haricots",
            nevo_food_name_de="Bohnen", search_aliases_en="beans;black beans")])
        assert not any(i["issue_type"] == "commercial_alias"
                       for i in ok["issues"])
        bad = val.validate_rows([_ml_row(
            nevo_code="1", nevo_food_name="Rice", nevo_food_name_fr="riz",
            nevo_food_name_de="Reis", search_aliases_en="rice;unit price")])
        assert any(i["issue_type"] == "commercial_alias" for i in bad["issues"])

    def test_clean_artifact_ready(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        rc = val.main(["--input", str(tmp_path
                       / "nevo_reference_multilingual.csv"),
                       "--output-dir", str(tmp_path)])
        assert rc == 0
        s = json.loads((tmp_path
                        / "nevo_reference_multilingual_validation_summary.json"
                        ).read_text())
        assert s["high_risk_translation_issue_count"] == 0
        assert s["recommendation"] == "ready_for_retrieval_experiment"


# ---------------------------------------------------------------------------
# Retrieval wiring + cache identity (Parts E/F).
# ---------------------------------------------------------------------------
class TestRetrievalWiring:
    def test_baseline_index_behaviour_unchanged(self) -> None:
        # Default text_builder must equal the explicit baseline builder.
        from altera_api.embeddings.text_builder import (
            build_nevo_reference_text,
        )
        refs = load_nevo_reference("fixture")[:8]
        prov = FakeEmbeddingProvider()
        idx_default = NevoVectorIndex.load_or_build(
            refs, provider=prov, provider_name="fake")
        idx_explicit = NevoVectorIndex.load_or_build(
            refs, provider=prov, provider_name="fake",
            text_builder=build_nevo_reference_text)
        a = idx_default.search("rice", top_k=3)
        b = idx_explicit.search("rice", top_k=3)
        assert [c.candidate.nevo_code for c in a] == [
            c.candidate.nevo_code for c in b]

    def test_cache_key_changes_with_reference(self, tmp_path) -> None:
        c_base = _make_cache(str(tmp_path), "fake", "m", "")
        c_ml = _make_cache(str(tmp_path), "fake", "m", "ml-abc123")
        assert c_base._path != c_ml._path

        rows1 = [{"nevo_code": "1", "nevo_food_name": "Rice",
                  "nevo_food_name_fr": "riz", "nevo_food_name_de": "Reis"}]
        rows2 = [{"nevo_code": "1", "nevo_food_name": "Rice",
                  "nevo_food_name_fr": "riz cuit", "nevo_food_name_de": "Reis"}]
        assert (multilingual_reference_checksum(rows1)
                != multilingual_reference_checksum(rows2))

    def test_project_batch_accepts_multilingual_reference(self, tmp_path
                                                          ) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        ml_ref = tmp_path / "nevo_reference_multilingual.csv"

        product = SimpleNamespace(
            id=uuid4(), product_name="Riz Basmati", brand="", labels=(),
            retailer_category="", ingredients_text="", pack_size="")

        class _Store:
            def __init__(self):
                self.reads: list[str] = []

            def get_project(self, pid):
                self.reads.append("p")
                return object()

            def list_products_for_project(self, pid):
                return [product]

            def list_enrichment_records_for_project(self, pid):
                return []

            def __getattr__(self, name):
                if name.split("_")[0] in ("add", "update", "delete", "upsert",
                                          "insert"):
                    raise AssertionError(f"write: {name}")
                raise AttributeError(name)

        store = _Store()
        rc = proj.main(
            ["--project-id", str(uuid4()), "--output-dir", str(tmp_path),
             "--cache-dir", str(tmp_path / "cache"), "--evaluator-fake",
             "--run-id", "RX", "--multilingual-reference", str(ml_ref)],
            store=store)
        assert rc == 0
        # The cache file carries the multilingual reference tag.
        assert any("-ml-" in p.name
                   for p in (tmp_path / "cache").glob("*.json"))


# ---------------------------------------------------------------------------
# Benchmark recommendation logic (Part G).
# ---------------------------------------------------------------------------
def _bench_row(pid, bucket, code="C", conf="0.9", matches="unknown"):
    return {"product_id": pid, "product_name": "p", "_bucket": bucket,
            "batch_nevo_code": code, "confidence": conf,
            "top_5_candidate_names": "Cand", "batch_matches_existing_v2": matches}


class TestBenchmark:
    def test_adopt_when_improves_no_regression(self) -> None:
        base = [_bench_row("a", "no_match"), _bench_row("b", "auto_ready")]
        ml = [_bench_row("a", "auto_ready"), _bench_row("b", "auto_ready")]
        out = bench.compare(baseline_rows=base, multilingual_rows=ml)
        assert out["summary"]["rows_improved"] == 1
        assert (out["summary"]["recommendation"]
                == "adopt_multilingual_reference_candidate")

    def test_reject_when_true_high_risk_appears(self) -> None:
        base = [_bench_row("a", "no_match")]
        ml = [_bench_row("a", "true_high_risk")]
        out = bench.compare(baseline_rows=base, multilingual_rows=ml)
        assert out["summary"]["recommendation"] == "reject_due_to_regressions"

    def test_reject_when_regressions_exceed_threshold(self) -> None:
        base = [_bench_row(str(i), "auto_ready") for i in range(10)]
        ml = [_bench_row(str(i), "no_match" if i < 5 else "auto_ready")
              for i in range(10)]
        out = bench.compare(baseline_rows=base, multilingual_rows=ml)
        assert out["summary"]["rows_regressed"] == 5
        assert out["summary"]["recommendation"] == "reject_due_to_regressions"


# ---------------------------------------------------------------------------
# Conservative decision layer (Render regression hotfix).
# ---------------------------------------------------------------------------
from altera_api.classification_v2 import (  # noqa: E402
    nevo_multilingual_conservative as cons,
)


def _cmp_row(name, bb, mb, bt1, mt1, bconf, mconf, bm="unknown", mm="unknown",
             bcode="C", mcode="M"):
    return {"product_id": name, "product_name": name, "baseline_bucket": bb,
            "multilingual_bucket": mb, "baseline_nevo_code": bcode,
            "multilingual_nevo_code": mcode, "baseline_top1": bt1,
            "multilingual_top1": mt1, "baseline_confidence": str(bconf),
            "multilingual_confidence": str(mconf),
            "baseline_matches_existing_v2": bm,
            "multilingual_matches_existing_v2": mm}


# The six Render regressions + three "improvements" (two suspicious).
_RENDER_ROWS = [
    _cmp_row("Maïs Doux Extra Croquant 285g", "auto_ready", "no_match",
             "Syrup corn", "Cocoa powder sweetened", 0.96, 0.0, bm="true"),
    _cmp_row("Crackers Graines de Lin 150g", "needs_review", "no_match",
             "Crispbread", "Crispbread", 0.95, 0.0),
    _cmp_row("Sucre de Canne Blond 750g", "auto_ready", "safety_downgrade",
             "Sugar castor brown", "Syrup sugar", 0.96, 0.96, bm="true"),
    _cmp_row("Confiture Abricot Intense 370g", "safety_downgrade", "no_match",
             "Apricots in syrup tinned", "Apricots in syrup tinned", 0.96, 0.0),
    _cmp_row("Boisson Amande Sans Sucres 1L", "auto_ready", "no_match",
             "Fruit juice", "Drink soya Alpro", 0.96, 0.0, bm="true"),
    _cmp_row("Houmous Citron Confit 175g", "auto_ready", "no_match", "Lemon",
             "Grapefruit in syrup canned", 0.96, 0.0, bm="true"),
    _cmp_row("Pâtes Fusilli Blé Complet 500g", "safety_downgrade",
             "auto_ready", "Pasta fortified w fibre boiled",
             "Pasta fortified w fibre raw", 0.96, 0.96),
    _cmp_row("Moutarde à l'Ancienne 350g", "no_match", "auto_ready",
             "Salad dressing honey/mustard", "Sauce based on roux prepared",
             0.0, 0.96),
    _cmp_row("Petits Pois Extra Fins Surgelés 600g", "safety_downgrade",
             "auto_ready", "Peas green boiled", "Peas super fine tinned",
             0.96, 0.96),
]


class TestConservativeFamilyGuards:
    @pytest.mark.parametrize("product,base,ml,expected", [
        ("Maïs Doux", "Syrup corn", "Cocoa powder", "corn_to_cocoa"),
        ("Sucre de Canne Blond", "Sugar brown", "Syrup sugar", "sugar_to_syrup"),
        ("Boisson Amande", "Almond drink", "Drink soya", "almond_drink_to_soya"),
        ("Houmous Citron", "Hummus", "Grapefruit in syrup", "hummus_to_citrus"),
        ("Moutarde à l'Ancienne", "Mustard", "Sauce based on roux",
         "mustard_to_roux_sauce"),
        ("Confiture Abricot", "Jam", "Apricots in syrup",
         "jam_to_fruit_in_syrup"),
        ("Petits Pois Surgelés", "Peas boiled", "Peas tinned",
         "peas_frozen_to_tinned"),
    ])
    def test_family_mismatch_detected(self, product, base, ml, expected
                                      ) -> None:
        assert cons.family_mismatch(product, base, ml) == expected

    @pytest.mark.parametrize("product,base,ml", [
        # Licensing exceptions: product explicitly names the target family.
        ("Sirop de Sucre", "Sugar", "Syrup sugar"),
        ("Sauce Moutarde", "Mustard sauce", "Sauce based on roux"),
        ("Confiture sirop", "Jam", "Fruit in syrup"),
        ("Petits Pois en conserve", "Peas", "Peas tinned"),
        # Unrelated pasta switch must not be flagged.
        ("Pâtes Fusilli", "Pasta boiled", "Pasta raw"),
    ])
    def test_family_mismatch_not_flagged(self, product, base, ml) -> None:
        assert cons.family_mismatch(product, base, ml) is None


class TestConservativeDecisions:
    def test_blocks_all_render_regressions(self) -> None:
        out = cons.conservative_decisions(_RENDER_ROWS, coverage=0.28)
        s = out["summary"]
        assert s["conservative_regressed_count"] == 0
        assert s["true_high_risk_delta"] == 0
        assert s["conservative_blocked_regression_count"] == 6
        # Only the legitimate pasta boiled->raw switch is accepted.
        switched = [r["product_name"] for r in out["rows"]
                    if r["conservative_decision"].startswith("switch")]
        assert switched == ["Pâtes Fusilli Blé Complet 500g"]
        # Agreement is preserved, not collapsed.
        agree = s["agreement_with_existing_v2"]
        assert agree["conservative"] == agree["baseline"]
        assert agree["raw"] < agree["baseline"]
        # Coverage-limited deterministic reference -> not adoption.
        assert s["recommendation"] == "needs_more_coverage"

    def test_suspicious_improvements_blocked(self) -> None:
        rows = {r["product_name"]: cons.decide_row(
            r, allow_overwrite_auto_ready=False, min_confidence=0.9)
            for r in _RENDER_ROWS}
        moutarde = rows["Moutarde à l'Ancienne 350g"]
        assert moutarde["conservative_decision"] == "keep_baseline"
        assert "mustard_to_roux_sauce" in moutarde["conservative_reason"]
        pois = rows["Petits Pois Extra Fins Surgelés 600g"]
        assert pois["conservative_decision"] == "keep_baseline"
        assert "peas_frozen_to_tinned" in pois["conservative_reason"]

    def test_auto_ready_protected_unless_flag(self) -> None:
        row = _cmp_row("X", "auto_ready", "safety_downgrade", "A", "B",
                       0.96, 0.96)
        kept = cons.decide_row(row, allow_overwrite_auto_ready=False,
                               min_confidence=0.9)
        assert kept["conservative_decision"] == "keep_baseline"
        assert "auto_ready_protected" in kept["conservative_reason"]
        # auto_ready is the best bucket, so even with the flag a downgrade is
        # never "strictly better" — it stays protected.
        with_flag = cons.decide_row(row, allow_overwrite_auto_ready=True,
                                    min_confidence=0.9)
        assert with_flag["conservative_decision"] == "keep_baseline"

    def test_blocks_no_match_zero_conf_and_below_threshold(self) -> None:
        nm = cons.decide_row(
            _cmp_row("X", "no_match", "no_match", "A", "B", 0.0, 0.0),
            allow_overwrite_auto_ready=False, min_confidence=0.9)
        assert nm["conservative_decision"] == "keep_baseline"
        lowconf = cons.decide_row(
            _cmp_row("Y", "no_match", "auto_ready", "A", "B", 0.0, 0.5),
            allow_overwrite_auto_ready=False, min_confidence=0.9)
        assert lowconf["conservative_decision"] == "keep_baseline"
        assert "below_confidence" in lowconf["conservative_reason"]

    def test_clear_improvement_switches(self) -> None:
        row = _cmp_row("Pasta", "safety_downgrade", "auto_ready",
                       "Pasta boiled", "Pasta raw", 0.96, 0.96)
        out = cons.decide_row(row, allow_overwrite_auto_ready=False,
                              min_confidence=0.9)
        assert out["conservative_decision"] == "switch_multilingual"
        assert out["conservative_bucket"] == "auto_ready"

    def test_conflicts_existing_v2_blocked(self) -> None:
        row = _cmp_row("Z", "safety_downgrade", "auto_ready", "A", "B",
                       0.96, 0.96, bm="true")
        out = cons.decide_row(row, allow_overwrite_auto_ready=False,
                              min_confidence=0.9)
        assert out["conservative_decision"] == "keep_baseline"
        assert "conflicts_existing_v2" in out["conservative_reason"]

    def test_adopt_when_material_safe_lift(self) -> None:
        rows = [_cmp_row(f"p{i}", "safety_downgrade", "auto_ready",
                         "Pasta boiled", "Pasta raw", 0.96, 0.96)
                for i in range(3)]
        out = cons.conservative_decisions(rows, coverage=0.9)
        assert out["summary"]["conservative_improved_count"] == 3
        assert (out["summary"]["recommendation"]
                == "adopt_conservative_candidate")

    def test_neutral_no_lift_when_no_switches_and_covered(self) -> None:
        rows = [_cmp_row("p", "no_match", "no_match", "A", "B", 0.0, 0.0)]
        out = cons.conservative_decisions(rows, coverage=0.9)
        assert out["summary"]["recommendation"] == "neutral_no_lift"


class TestConservativeCli:
    def test_conservative_mode_writes_artifacts(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        ml_ref = tmp_path / "nevo_reference_multilingual.csv"
        products = [SimpleNamespace(
            id=uuid4(), product_name=n, brand="", labels=(),
            retailer_category="", ingredients_text="", pack_size="")
            for n in ("Riz", "Lentilles", "Café")]

        class _Store:
            def get_project(self, pid):
                return object()

            def list_products_for_project(self, pid):
                return products

            def list_enrichment_records_for_project(self, pid):
                return []

            def __getattr__(self, name):
                if name.split("_")[0] in ("add", "update", "delete", "upsert",
                                          "insert"):
                    raise AssertionError(f"write: {name}")
                raise AttributeError(name)

        from altera_api.classification_v2 import (
            compare_nevo_multilingual_retrieval_conservative as wrapper,
        )
        pid = uuid4()
        rc = wrapper.main(
            ["--project-id", str(pid), "--multilingual-reference", str(ml_ref),
             "--baseline-reference-source", "fixture", "--output-dir",
             str(tmp_path), "--cache-dir", str(tmp_path / "cache"),
             "--evaluator-fake"], store=_Store())
        assert rc == 0
        cons_json = (tmp_path
                     / f"nevo_multilingual_retrieval_conservative_{pid}.json")
        cons_csv = (tmp_path
                    / f"nevo_multilingual_retrieval_conservative_{pid}.csv")
        assert cons_json.exists() and cons_csv.exists()
        s = json.loads(cons_json.read_text())
        assert s["decision_mode"] == "conservative"
        assert s["conservative_regressed_count"] == 0
        # Raw comparison is still written (preserve before/after).
        assert (tmp_path
                / f"nevo_multilingual_retrieval_comparison_{pid}.json").exists()

    def test_raw_mode_default_no_conservative_file(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        ml_ref = tmp_path / "nevo_reference_multilingual.csv"
        product = SimpleNamespace(
            id=uuid4(), product_name="Riz", brand="", labels=(),
            retailer_category="", ingredients_text="", pack_size="")

        class _Store:
            def get_project(self, pid):
                return object()

            def list_products_for_project(self, pid):
                return [product]

            def list_enrichment_records_for_project(self, pid):
                return []

            def __getattr__(self, name):
                if name.split("_")[0] in ("add", "update", "delete", "upsert",
                                          "insert"):
                    raise AssertionError(f"write: {name}")
                raise AttributeError(name)

        pid = uuid4()
        rc = bench.main(
            ["--project-id", str(pid), "--multilingual-reference", str(ml_ref),
             "--baseline-reference-source", "fixture", "--output-dir",
             str(tmp_path), "--cache-dir", str(tmp_path / "cache"),
             "--evaluator-fake"], store=_Store())
        assert rc == 0
        assert not (tmp_path
                    / f"nevo_multilingual_retrieval_conservative_{pid}.json"
                    ).exists()


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_no_db_writes_in_modules(self) -> None:
        import altera_api.classification_v2.nevo_multilingual_reference as core
        from altera_api.classification_v2 import (
            compare_nevo_multilingual_retrieval_conservative as wrapper,
        )
        for mod in (core, gen, val, bench, cons, wrapper):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for needle in ("add_enrichment", "update_enrichment",
                           "add_export_record"):
                assert needle not in src, f"{mod.__name__}:{needle}"

    def test_no_route_imports_multilingual(self) -> None:
        api_dir = Path(gen.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "multilingual" in p.read_text(encoding="utf-8")
            or "classification_v2" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_v1_default_and_embeddings_off(self, monkeypatch) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

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
    CompositionalTranslator,
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
# Deterministic compositional coverage expansion.
# ---------------------------------------------------------------------------
class TestCompositionalTranslator:
    @pytest.mark.parametrize("name,fr_mark,de_mark", [
        ("Rice white raw", "cru", "roh"),
        ("Rice white cooked", "cuit", "gekocht"),
        ("Rice white boiled", "cuit", "gekocht"),
        ("Tomato dried", "séch", "getrocknet"),
    ])
    def test_preserves_raw_cooked_boiled_dried(self, name, fr_mark, de_mark
                                               ) -> None:
        tr = CompositionalTranslator().translate(name)
        assert fr_mark in tr.food_name_fr.lower()
        assert de_mark in tr.food_name_de.lower()

    def test_preserves_drink_vs_solid_rice(self) -> None:
        drink = CompositionalTranslator().translate("Rice drink wo sugar")
        solid = CompositionalTranslator().translate("Rice white raw")
        assert "boisson" in drink.food_name_fr.lower()
        assert "drink" in drink.food_name_de.lower()
        assert "boisson" not in solid.food_name_fr.lower()
        assert "drink" not in solid.food_name_de.lower()

    def test_preserves_coffee_beans_vs_instant_powder(self) -> None:
        beans = CompositionalTranslator().translate("Coffee beans")
        instant = CompositionalTranslator().translate("Coffee instant powder")
        # beans must not be translated as instant/powder
        assert "instant" not in beans.food_name_fr.lower()
        assert "poudre" not in beans.food_name_fr.lower()
        # instant powder keeps both markers
        assert "instant" in instant.food_name_fr.lower()
        assert "poudre" in instant.food_name_fr.lower()
        assert "instant" in instant.food_name_de.lower()
        assert "pulver" in instant.food_name_de.lower()

    def test_preserves_powder_vs_prepared_potato(self) -> None:
        powder = CompositionalTranslator().translate("Potato puree powder av")
        prep = CompositionalTranslator().translate(
            "Potatoes mashed fresh prepared w whole milk and margarin")
        assert "poudre" in powder.food_name_fr.lower()
        assert "pulver" in powder.food_name_de.lower()
        assert "poudre" not in prep.food_name_fr.lower()
        assert "préparé" in prep.food_name_fr.lower()

    def test_preserves_oil_type_markers(self) -> None:
        tr = CompositionalTranslator().translate("Oil rapeseed")
        assert "colza" in tr.food_name_fr.lower()
        assert "raps" in tr.food_name_de.lower()

    def test_preserves_vinegar_type_markers(self) -> None:
        tr = CompositionalTranslator().translate("Vinegar balsamic")
        assert "balsamique" in tr.food_name_fr.lower()
        assert "balsamico" in tr.food_name_de.lower()

    def test_too_generic_row_not_translated(self) -> None:
        # Many unknown tokens -> not safely compositional -> blank + flagged.
        tr = CompositionalTranslator().translate("Xyzzy Quux Frobnitz Widget")
        assert tr.food_name_fr == "" and tr.food_name_de == ""
        assert tr.review_status in ("needs_review", "unavailable")

    def test_preserves_nutrition_and_canonical_metadata(self) -> None:
        refs = [{"nevo_code": "N1", "food_name_en": "Rice white raw",
                 "protein_g_per_100g": "7.1"},
                {"nevo_code": "N2", "food_name_en": "Bread brown"}]
        rows = generate_rows(refs, translator=CompositionalTranslator())
        assert [r["nevo_code"] for r in rows] == ["N1", "N2"]
        assert rows[0]["nevo_food_name"] == "Rice white raw"  # exact.
        assert rows[0]["protein_g_per_100g"] == "7.1"
        assert "cru" in rows[0]["nevo_food_name_fr"].lower()
        assert rows[0]["translation_source"] == "deterministic_compositional"

    def test_expanded_increases_coverage_over_deterministic(self) -> None:
        # Rows the curated-only translator leaves blank, composition fills.
        refs = [{"nevo_code": str(i), "food_name_en": n} for i, n in enumerate(
            ["Bread brown", "Chicken raw", "Yoghurt low fat",
             "Carrots boiled", "Salmon smoked", "Cheese white"])]
        det = generate_rows(refs, translator=DeterministicTranslator())
        comp = generate_rows(refs, translator=CompositionalTranslator())
        det_fr = sum(1 for r in det if r["nevo_food_name_fr"])
        comp_fr = sum(1 for r in comp if r["nevo_food_name_fr"])
        assert comp_fr > det_fr

    def test_compositional_row_fr_text_excludes_de_en(self) -> None:
        rows = generate_rows([{"nevo_code": "1",
                               "food_name_en": "Rice white raw"}],
                             translator=CompositionalTranslator())
        row = rows[0]
        txt = build_language_reference_text(row, language="fr")
        assert "cru" in txt.lower()
        assert "reis" not in txt.lower() and "roh" not in txt.lower()


class TestExpandedGenerator:
    def test_default_unchanged_without_flag(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        s = json.loads((tmp_path
                        / "nevo_reference_multilingual_summary.json").read_text())
        assert s["translator"] == "deterministic"
        assert s.get("expand_compositional") is False
        assert "deterministic_compositional" not in s[
            "count_by_translation_source"]

    def test_expand_flag_uses_compositional(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm", "--expand-compositional"])
        s = json.loads((tmp_path
                        / "nevo_reference_multilingual_summary.json").read_text())
        assert s["translator"] == "deterministic_compositional"
        assert s["expand_compositional"] is True
        assert "coverage_target" in s and "fr_coverage" in s

    def test_expanded_validation_high_risk_zero(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm", "--expand-compositional"])
        rc = val.main(["--input", str(tmp_path
                       / "nevo_reference_multilingual.csv"),
                       "--output-dir", str(tmp_path)])
        assert rc == 0
        s = json.loads((tmp_path
                        / "nevo_reference_multilingual_validation_summary.json"
                        ).read_text())
        assert s["high_risk_translation_issue_count"] == 0
        for key in ("coverage_by_language", "coverage_by_source",
                    "compositional_count", "unavailable_count",
                    "needs_review_count", "blocked_compositional_count",
                    "high_risk_by_issue_type"):
            assert key in s


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
        # Language-specific FR false positives (tightened guards).
        ("Compote Pomme Fraise Sans Sucres", "Apple sauce wo sugar tinned",
         "Apple dried soaked in water", "compote_to_dried_fruit"),
        ("Biscuits Sablés au Beurre", "Biscuit spiced Speculaas w butter",
         "Apple pie Dutch w shortbread wo butter", "biscuit_to_pie"),
        # Expanded-coverage FR false positives.
        ("Moutarde à l'Ancienne 350g", "Salad dressing honey/mustard",
         "Mustard leaves raw", "mustard_condiment_to_leaves"),
        ("Riz Thaï Parfumé 1kg", "Rice drink wo sugar", "Rice cakes w spices",
         "rice_grain_to_rice_cakes"),
        ("Riz Basmati", "Rice drink", "Rice cake spiced",
         "rice_grain_to_rice_cakes"),
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
        # The four good FR language switches must NOT be flagged.
        ("Lentilles Vertes Cuites", "Lentils green and brown dried",
         "Lentils green and brown boiled"),
        ("Huile de Colza Bio", "Oil Becel Blend Classic", "Oil rapeseed"),
        ("Riz Thaï Parfumé", "Rice drink wo sugar", "Rice white raw"),
        ("Purée Mousseline Nature",
         "Potatoes mashed fresh prep w whole milk and margarin",
         "Potato puree powder av"),
        # compote with a non-dried candidate is fine.
        ("Compote Pomme", "Apple sauce", "Apple sauce wo sugar"),
        # a shortbread TART product may match a pie/tart candidate.
        ("Tartelettes Sablées Pommes", "Biscuit", "Apple pie Dutch"),
        # Quinoa / couscous good switches must NOT trip the rice guard.
        ("Quinoa Blanc 400g", "Quinoa cooked", "Quinoa raw"),
        ("Semoule Couscous Moyen 500g", "Couscous wholemeal boiled",
         "Couscous wholemeal unprepared"),
        # An explicit rice-CAKE product may match a rice-cake candidate.
        ("Galettes de Riz Bio", "Crispbread", "Rice cakes w spices"),
        ("Riz Soufflé Snack", "Cereal", "Rice cakes spiced"),
        # An explicit mustard-LEAF/greens/salad product may match leaves.
        ("Salade de Pousses de Moutarde", "Salad", "Mustard leaves raw"),
        ("Mustard greens fresh", "Greens", "Mustard leaves raw"),
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

    def test_fr_language_render_switches(self) -> None:
        # The six Render FR-only switches: two are false positives that the
        # tightened guards must now block; four are legitimate rescues.
        rows = [
            _cmp_row("Compote Pomme Fraise Sans Sucres 4x100g",
                     "safety_downgrade", "auto_ready",
                     "Apple sauce wo sugar w sweetener tinned",
                     "Apple dried soaked in water", 0.96, 0.96),
            _cmp_row("Biscuits Sablés au Beurre 200g", "no_match", "auto_ready",
                     "Biscuit spiced Speculaas w butter",
                     "Apple pie Dutch w shortbread wo butter", 0.0, 0.96),
            _cmp_row("Lentilles Vertes Cuites 265g", "safety_downgrade",
                     "auto_ready", "Lentils green and brown dried",
                     "Lentils green and brown boiled", 0.96, 0.96),
            _cmp_row("Huile de Colza Bio 1L", "safety_downgrade", "auto_ready",
                     "Oil Becel Blend Classic", "Oil rapeseed", 0.96, 0.96),
            _cmp_row("Riz Thaï Parfumé 1kg", "safety_downgrade", "auto_ready",
                     "Rice drink wo sugar", "Rice white raw", 0.96, 0.96),
            _cmp_row("Purée Mousseline Nature 4 sachets", "safety_downgrade",
                     "auto_ready",
                     "Potatoes mashed fresh prep w whole milk and margarin",
                     "Potato puree powder av", 0.96, 0.96),
        ]
        out = cons.conservative_decisions(rows, coverage=0.2814)
        by_name = {r["product_name"]: r for r in out["rows"]}
        compote = by_name["Compote Pomme Fraise Sans Sucres 4x100g"]
        assert compote["conservative_decision"] == "keep_baseline"
        assert "compote_to_dried_fruit" in compote["conservative_reason"]
        biscuit = by_name["Biscuits Sablés au Beurre 200g"]
        assert biscuit["conservative_decision"] == "keep_baseline"
        assert "biscuit_to_pie" in biscuit["conservative_reason"]
        for good in ("Lentilles Vertes Cuites 265g", "Huile de Colza Bio 1L",
                     "Riz Thaï Parfumé 1kg", "Purée Mousseline Nature 4 sachets"):
            assert by_name[good]["conservative_decision"] == "switch_multilingual"
        assert out["summary"]["conservative_switch_count"] == 4
        assert out["summary"]["conservative_regressed_count"] == 0
        assert out["summary"]["true_high_risk_delta"] == 0

    def test_fr_expanded_coverage_switches(self) -> None:
        # The six FR-only switches from the expanded (77% coverage) run: two are
        # newly discovered false positives that the tightened guards now block;
        # four are legitimate state/form rescues.
        rows = [
            _cmp_row("Lentilles Vertes Cuites 265g", "safety_downgrade",
                     "auto_ready", "Lentils green and brown dried",
                     "Lentils green and brown boiled", 0.96, 0.96),
            _cmp_row("Moutarde à l'Ancienne 350g", "no_match", "auto_ready",
                     "Salad dressing honey/mustard", "Mustard leaves raw",
                     0.0, 0.96),
            _cmp_row("Riz Thaï Parfumé 1kg", "safety_downgrade", "auto_ready",
                     "Rice drink wo sugar", "Rice cakes w spices", 0.96, 0.96),
            _cmp_row("Quinoa Blanc 400g", "safety_downgrade", "auto_ready",
                     "Quinoa cooked", "Quinoa raw", 0.96, 0.96),
            _cmp_row("Semoule Couscous Moyen 500g", "safety_downgrade",
                     "auto_ready", "Couscous wholemeal boiled",
                     "Couscous wholemeal unprepared", 0.96, 0.96),
            _cmp_row("Purée Mousseline Nature 4 sachets", "safety_downgrade",
                     "auto_ready",
                     "Potatoes mashed fresh prep w whole milk and margarin",
                     "Potato puree powder w milkpowder w fat", 0.96, 0.96),
        ]
        out = cons.conservative_decisions(rows, coverage=0.7745)
        by_name = {r["product_name"]: r for r in out["rows"]}
        moutarde = by_name["Moutarde à l'Ancienne 350g"]
        assert moutarde["conservative_decision"] == "keep_baseline"
        assert "mustard_condiment_to_leaves" in moutarde["conservative_reason"]
        riz = by_name["Riz Thaï Parfumé 1kg"]
        assert riz["conservative_decision"] == "keep_baseline"
        assert "rice_grain_to_rice_cakes" in riz["conservative_reason"]
        for good in ("Lentilles Vertes Cuites 265g", "Quinoa Blanc 400g",
                     "Semoule Couscous Moyen 500g",
                     "Purée Mousseline Nature 4 sachets"):
            assert by_name[good]["conservative_decision"] == "switch_multilingual"
        assert out["summary"]["conservative_switch_count"] == 4
        assert out["summary"]["conservative_regressed_count"] == 0
        assert out["summary"]["true_high_risk_delta"] == 0


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
# Language-specific (FR-only / DE-only) auxiliary retrieval.
# ---------------------------------------------------------------------------
from altera_api.classification_v2 import (  # noqa: E402
    compare_nevo_language_specific_retrieval as lang,
)
from altera_api.classification_v2.nevo_multilingual_reference import (  # noqa: E402
    build_language_reference_text,
    language_name_present,
)

_LANG_ROW = {
    "nevo_food_name": "Lentils dried", "nevo_food_name_fr": "lentilles sèches",
    "nevo_food_name_de": "getrocknete Linsen",
    "search_aliases_fr": ["lentilles sèches", "lentilles"],
    "search_aliases_de": ["getrocknete Linsen", "Linsen"],
    "search_aliases_en": ["dried lentils", "lentils"],
}


class TestLanguageTextBuilder:
    def test_fr_only_excludes_de_en_and_canonical(self) -> None:
        txt = build_language_reference_text(_LANG_ROW, language="fr")
        assert "lentilles" in txt
        assert "getrocknete" not in txt and "Linsen" not in txt  # no DE
        assert "dried lentils" not in txt                         # no EN aliases
        assert "Lentils dried" not in txt                         # no canonical

    def test_de_only_excludes_fr_en_and_canonical(self) -> None:
        txt = build_language_reference_text(_LANG_ROW, language="de")
        assert "Linsen" in txt
        assert "lentilles" not in txt                             # no FR
        assert "dried lentils" not in txt                         # no EN aliases
        assert "Lentils dried" not in txt                         # no canonical

    def test_en_uses_canonical_plus_en_aliases(self) -> None:
        txt = build_language_reference_text(_LANG_ROW, language="en")
        assert "Lentils dried" in txt and "dried lentils" in txt
        assert "lentilles" not in txt and "Linsen" not in txt

    def test_missing_language_returns_none(self) -> None:
        miss = {"nevo_food_name": "Rare food", "nevo_food_name_fr": "",
                "nevo_food_name_de": ""}
        assert build_language_reference_text(miss, language="fr") is None
        assert build_language_reference_text(miss, language="de") is None
        assert build_language_reference_text(miss, language="en") == "Rare food"
        assert not language_name_present(miss, "fr")
        assert language_name_present(miss, "en")

    def test_language_text_differs_from_mixed(self) -> None:
        # The auxiliary FR-only text must NOT be the mixed EN+FR+DE text.
        mixed = build_multilingual_reference_text(_LANG_ROW)
        fr_only = build_language_reference_text(_LANG_ROW, language="fr")
        assert fr_only != mixed
        assert "DE:" not in fr_only and "EN aliases:" not in fr_only


def _cons_summary(*, base, raw, consv, agree, improved=0, regressed=0,
                  switch=0, blocked=0, kept=0):
    return {
        "baseline_counts": base, "raw_multilingual_counts": raw,
        "conservative_counts": consv,
        "agreement_with_existing_v2": agree,
        "conservative_improved_count": improved,
        "conservative_regressed_count": regressed,
        "conservative_switch_count": switch,
        "conservative_blocked_regression_count": blocked,
        "conservative_kept_baseline_count": kept,
        "true_high_risk_delta": consv["true_high_risk"] - base["true_high_risk"],
    }


def _counts(ar=0, sd=0, nr=0, nm=0, thr=0):
    return {"auto_ready": ar, "safety_downgrade": sd, "needs_review": nr,
            "no_match": nm, "true_high_risk": thr}


class TestLanguageRecommendation:
    def test_adopt_when_coverage_high_and_safe_lift(self) -> None:
        cs = _cons_summary(base=_counts(ar=40, nm=30), raw=_counts(ar=43, nm=27),
                           consv=_counts(ar=43, nm=27),
                           agree={"baseline": 40, "raw": 20, "conservative": 40},
                           improved=3)
        assert (lang.language_recommendation(cs, coverage=0.8)
                == "adopt_language_specific_candidate")

    def test_needs_more_coverage_when_low_coverage(self) -> None:
        cs = _cons_summary(base=_counts(ar=49, nm=34), raw=_counts(ar=47, nm=38),
                           consv=_counts(ar=50, nm=34),
                           agree={"baseline": 49, "raw": 29, "conservative": 49},
                           improved=1)
        assert (lang.language_recommendation(cs, coverage=0.28)
                == "needs_more_coverage")

    def test_neutral_no_lift_when_covered_but_no_switch(self) -> None:
        cs = _cons_summary(base=_counts(ar=49, nm=34), raw=_counts(ar=49, nm=34),
                           consv=_counts(ar=49, nm=34),
                           agree={"baseline": 49, "raw": 49, "conservative": 49},
                           improved=0)
        assert (lang.language_recommendation(cs, coverage=0.8)
                == "neutral_no_lift")

    def test_reject_when_agreement_collapses(self) -> None:
        cs = _cons_summary(base=_counts(ar=49, nm=34), raw=_counts(ar=49, nm=34),
                           consv=_counts(ar=49, nm=34),
                           agree={"baseline": 49, "raw": 10, "conservative": 10},
                           improved=0)
        assert (lang.language_recommendation(cs, coverage=0.8)
                == "reject_due_to_regressions")


class TestLanguageCli:
    def _store(self, names):
        products = [SimpleNamespace(
            id=uuid4(), product_name=n, brand="", labels=(),
            retailer_category="", ingredients_text="", pack_size="")
            for n in names]

        class _Store:
            def __init__(self):
                self.reads: list[str] = []

            def get_project(self, pid):
                self.reads.append("p")
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

        return _Store()

    def test_fr_cli_writes_artifacts_read_only(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        ml_ref = tmp_path / "nevo_reference_multilingual.csv"
        store = self._store(["Riz", "Lentilles", "Café"])
        pid = "326c6e1c-46b2-4103-98f1-331afadb721a"
        rc = lang.main(
            ["--project-id", pid, "--reference-source", "fixture",
             "--language-reference", str(ml_ref), "--retailer-language", "fr",
             "--output-dir", str(tmp_path), "--cache-dir",
             str(tmp_path / "cache"), "--evaluator-fake", "--top-k", "5"],
            store=store)
        assert rc == 0
        assert set(store.reads) <= {"p"}
        csv_path = tmp_path / f"nevo_language_specific_retrieval_fr_{pid}.csv"
        json_path = tmp_path / f"nevo_language_specific_retrieval_fr_{pid}.json"
        assert csv_path.exists() and json_path.exists()
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            header = next(csv.reader(fh))
        assert header == lang.LANGUAGE_CSV_COLUMNS
        s = json.loads(json_path.read_text())
        for key in ("retailer_language", "language_reference_coverage",
                    "language_reference_rows_used",
                    "language_reference_rows_missing", "baseline_auto_ready",
                    "raw_language_auto_ready", "conservative_auto_ready",
                    "true_high_risk_delta", "conservative_regressed_count",
                    "baseline_existing_v2_agreement",
                    "conservative_existing_v2_agreement", "recommendation"):
            assert key in s, key
        assert s["retailer_language"] == "fr"
        assert s["conservative_regressed_count"] == 0
        assert s["true_high_risk_delta"] == 0
        # Missing-FR rows were excluded from the language index.
        assert s["language_reference_rows_missing"] >= 0
        assert (s["language_reference_rows_used"]
                + s["language_reference_rows_missing"]
                == s["language_reference_rows_total"])
        # The language index cache is tagged distinctly from baseline.
        assert any("-lang-fr-" in p.name
                   for p in (tmp_path / "cache").glob("*.json"))

    def test_de_cli_runs(self, tmp_path) -> None:
        gen.main(["--reference-source", "fixture", "--output-dir",
                  str(tmp_path), "--no-llm"])
        ml_ref = tmp_path / "nevo_reference_multilingual.csv"
        store = self._store(["Reis", "Linsen"])
        pid = uuid4()
        rc = lang.main(
            ["--project-id", str(pid), "--reference-source", "fixture",
             "--language-reference", str(ml_ref), "--retailer-language", "de",
             "--output-dir", str(tmp_path), "--cache-dir",
             str(tmp_path / "cache"), "--evaluator-fake", "--top-k", "5"],
            store=store)
        assert rc == 0
        assert (tmp_path
                / f"nevo_language_specific_retrieval_de_{pid}.json").exists()


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_no_db_writes_in_modules(self) -> None:
        import altera_api.classification_v2.nevo_multilingual_reference as core
        from altera_api.classification_v2 import (
            compare_nevo_multilingual_retrieval_conservative as wrapper,
        )
        for mod in (core, gen, val, bench, cons, wrapper, lang):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for needle in ("add_enrichment", "update_enrichment",
                           "add_export_record"):
                assert needle not in src, f"{mod.__name__}:{needle}"

    def test_no_route_imports_multilingual(self) -> None:
        api_dir = Path(gen.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "multilingual" in p.read_text(encoding="utf-8")
            or "language_specific" in p.read_text(encoding="utf-8")
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

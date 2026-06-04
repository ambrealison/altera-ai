"""Phase Quality-V2-Q — NEVO V2 dry-run review package + final filters.

Builds on the V2-P two-stage model. Adds (A) a filtered review-package of CSVs
with blank reviewer columns, (B) targeted final nutrition-safety filters
(rice-drink, vinegar/oil/jam variety, instant-vs-prepared puree, generic snack
proxy), and (C) headline review counts in the JSON summary.

All offline; V1 stays default; embeddings off by default; no route imports
V2/embeddings/safety; no DB writes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import nevo_v2_enrich as cli
from altera_api.classification_v2.nevo_matcher import NevoMatcherVersion
from altera_api.classification_v2.nevo_nutrition_safety import (
    nutrition_safety_action,
)


def _action(product: str, ref: str, **kw) -> str:
    kw.setdefault("matched", True)
    kw.setdefault("review_required", False)
    kw.setdefault("confidence", 0.96)
    kw.setdefault("protein", 10.0)
    return nutrition_safety_action(product_name=product, ref_name=ref, **kw)[0]


# ---------------------------------------------------------------------------
# Part B — targeted final filters downgrade nutrition-risky matches.
# ---------------------------------------------------------------------------
class TestRiceDrink:
    @pytest.mark.parametrize(
        "product,ref",
        [
            ("Riz Thaï Parfumé 1kg", "Rice drink wo sugar"),
            ("Riz Basmati", "Rice drink calcium"),
            ("Flocons d'Avoine", "Oat drink barista"),
        ],
    )
    def test_whole_food_vs_drink_skips(self, product, ref) -> None:
        assert _action(product, ref) == "skip_proxy_too_broad"

    def test_actual_drink_product_still_enriches(self) -> None:
        # A beverage product matched to a drink reference is aligned.
        assert _action("Boisson Amande", "Almond drink") == "would_enrich"


class TestVinegar:
    def test_cider_vs_balsamic_skips(self) -> None:
        assert _action("Vinaigre de Cidre Bio 50cl", "Vinegar Balsamic") == (
            "skip_proxy_too_broad"
        )

    def test_same_type_enriches(self) -> None:
        assert _action("Vinaigre Balsamique", "Vinegar Balsamic") == "would_enrich"

    def test_generic_vinegar_reference_enriches(self) -> None:
        # No recognizable type on the reference → treated as a generic proxy.
        assert _action("Vinaigre de Cidre", "Vinegar") == "would_enrich"


class TestOil:
    def test_rapeseed_vs_branded_blend_routes_review(self) -> None:
        assert _action("Huile de Colza Bio 1L", "Oil Becel Blend Classic") == (
            "route_to_review"
        )

    def test_wrong_oil_type_routes_review(self) -> None:
        assert _action("Huile de Colza", "Oil olive extra virgin") == (
            "route_to_review"
        )

    def test_matching_oil_enriches(self) -> None:
        assert _action("Huile d'Olive Vierge", "Oil olive") == "would_enrich"


class TestPuree:
    def test_instant_vs_prepared_skips(self) -> None:
        assert _action(
            "Purée Mousseline Nature",
            "Mashed potato prepared with whole milk and margarine",
        ) == "skip_state_mismatch"

    def test_dry_to_dry_powder_enriches(self) -> None:
        assert _action("Purée Mousseline Nature", "Potato puree powder av") == (
            "would_enrich"
        )


class TestJam:
    def test_wrong_fruit_skips(self) -> None:
        assert _action("Confiture Abricot", "Jam rose hip w vit C") == (
            "skip_proxy_too_broad"
        )

    def test_same_fruit_enriches(self) -> None:
        assert _action("Confiture Abricot", "Jam apricot") == "would_enrich"


class TestSnackGenericProxy:
    @pytest.mark.parametrize(
        "product,ref",
        [
            ("Crackers Graines de Lin", "Crackers cream"),
            ("Chips Vinaigre", "Crisps potato light unflavoured"),
            ("Tortillas Maïs Paprika", "Crisps tortilla unflavoured"),
        ],
    )
    def test_flavoured_snack_routes_review(self, product, ref) -> None:
        assert _action(product, ref) == "route_to_review"

    def test_plain_snack_match_enriches(self) -> None:
        assert _action("Crisps Nature", "Crisps potato unflavoured") == (
            "would_enrich"
        )


class TestNoV2PRegression:
    @pytest.mark.parametrize(
        "product,ref,expected",
        [
            ("Chocolat Noir 70%", "Chocolate dark", "would_enrich"),
            ("Pois Chiches", "Chickpeas canned", "would_enrich"),
            ("Thon au Naturel", "Tuna in water tinned", "would_enrich"),
            ("Maïs Doux", "Sweetcorn tinned", "would_enrich"),
            ("Jus Orange avec Pulpe", "Orange juice with pulp", "would_enrich"),
            ("Pâtes Fusilli Blé Complet", "Pasta wholemeal boiled",
             "skip_state_mismatch"),
            ("Compote Pomme Nature", "Apple syrup", "skip_proxy_too_broad"),
            ("Café Capsules Intense", "Coffee instant powder",
             "skip_state_mismatch"),
        ],
    )
    def test_unchanged(self, product, ref, expected) -> None:
        assert _action(product, ref) == expected


# ---------------------------------------------------------------------------
# Part A — filtered review-package CSVs (reviewer columns + correct counts).
# ---------------------------------------------------------------------------
def _decision(*, matched, code, food, conf, review, top=None):
    return SimpleNamespace(
        matched=matched, nevo_code=code, food_name_en=food, confidence=conf,
        match_type="concept", review_required=review,
        top_candidates=top or [SimpleNamespace(candidate_name=food,
                                               rejection_reason="")],
    )


class _FakeMatcher:
    def __init__(self, by_name):
        self._by_name = by_name

    def decide(self, query, top_k):  # noqa: ARG002
        return self._by_name[query["product_name"]]


def _p(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        ingredients_text=None, labels=(),
    )


def _entry(code, protein, name):
    return SimpleNamespace(nevo_code=code, protein_g_per_100g=protein,
                           food_name_en=name)


def _mixed_rows():
    products = [
        _p("Chocolat Noir 70%"),          # would_enrich
        _p("Pâtes Fusilli Blé Complet"),  # skip_state_mismatch
        _p("Compote Pomme Nature"),       # skip_proxy_too_broad
        _p("Mystery Box XYZ"),            # no_match
        _p("Huile de Colza Bio 1L"),      # route_to_review (oil blend)
    ]
    decisions = {
        "Chocolat Noir 70%": _decision(
            matched=True, code="C", food="Chocolate dark", conf=0.97,
            review=False),
        "Pâtes Fusilli Blé Complet": _decision(
            matched=True, code="P", food="Pasta wholemeal boiled", conf=0.95,
            review=False),
        "Compote Pomme Nature": _decision(
            matched=True, code="S", food="Apple syrup", conf=0.96,
            review=False),
        "Mystery Box XYZ": _decision(
            matched=False, code="", food="", conf=0.0, review=True),
        "Huile de Colza Bio 1L": _decision(
            matched=True, code="O", food="Oil Becel Blend Classic", conf=0.96,
            review=False),
    }
    nevo_by_code = {
        "C": _entry("C", 7.0, "Chocolate dark"),
        "P": _entry("P", 5.0, "Pasta wholemeal boiled"),
        "S": _entry("S", 0.1, "Apple syrup"),
        "O": _entry("O", 90.0, "Oil Becel Blend Classic"),
    }
    return cli.build_proposals(
        products, version=NevoMatcherVersion.V2_EMBEDDINGS,
        matcher=_FakeMatcher(decisions), nevo=None, nevo_by_code=nevo_by_code,
        provider_name="fake", model="m", top_k=5,
    )


class TestFilteredReviewPackage:
    def test_writes_one_csv_per_bucket_with_reviewer_columns(self, tmp_path) -> None:
        rows = _mixed_rows()
        pid = str(uuid4())
        artifacts = cli.write_filtered_review_csvs(tmp_path, pid, rows)

        expected_counts = {
            "would_enrich": 1, "state_mismatch": 1, "proxy_too_broad": 1,
            "no_match": 1, "review": 1,
        }
        assert {k: v["count"] for k, v in artifacts.items()} == expected_counts

        for key, info in artifacts.items():
            path = Path(info["path"])
            assert path.exists()
            with path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                header = reader.fieldnames or []
                data = list(reader)
            # Reviewer columns present and blank.
            for col in ("manual_decision", "reviewer_notes",
                        "approved_nevo_code", "approved_nevo_name"):
                assert col in header
            assert len(data) == info["count"], key
            for row in data:
                assert row["manual_decision"] == ""
                assert row["approved_nevo_code"] == ""

    def test_summary_review_counts(self, tmp_path) -> None:
        rows = _mixed_rows()
        summary = cli.build_dry_run_summary(
            rows, project_id="p", version="v2-embeddings", provider="fake",
            model="m", top_k=5, generated_at=None,
        )
        assert summary["enrich_ready_count"] == 1
        # everything that is not would_enrich needs a human.
        assert summary["manual_review_required_count"] == len(rows) - 1


# ---------------------------------------------------------------------------
# End-to-end dry-run — filtered CSVs written, JSON counts present, no writes.
# ---------------------------------------------------------------------------
class _ReadOnlyStore:
    _WRITES = frozenset({"add_enrichment_record", "upsert_pt_classification",
                         "add_product", "add_run", "add_job"})

    def __init__(self, products, nevo_entries=()):
        self._products = products
        self._nevo = list(nevo_entries)
        self.reads: list[str] = []

    def get_project(self, project_id):
        self.reads.append("get_project")
        return object()

    def list_products_for_project(self, project_id):
        self.reads.append("list_products_for_project")
        return self._products

    def list_nevo_entries(self):
        self.reads.append("list_nevo_entries")
        return self._nevo

    def __getattr__(self, name):
        if name in self._WRITES:
            raise AssertionError(f"read-only violation: {name}")
        raise AttributeError(name)


def _store_product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        retailer_subcategory=None, ingredients_text=None, labels=(),
        pt_fields=object(),
    )


def _nevo_entry(code, protein, name):
    return SimpleNamespace(
        nevo_code=code, protein_g_per_100g=protein, food_name_en=name,
        food_name_nl="", food_group="", plant_protein_g_per_100g=None,
        animal_protein_g_per_100g=None,
    )


class TestDryRunEndToEnd:
    def test_filtered_artifacts_written_and_no_db(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        store = _ReadOnlyStore(
            [_store_product("Tofu nature"),
             _store_product("Riz Thaï Parfumé 1kg")],
            nevo_entries=[_nevo_entry("NEVO-TOFU", 8.0, "Tofu")],
        )
        pid = str(uuid4())
        rc = cli.main(
            ["--project-id", pid, "--matcher-version", "v2-embeddings",
             "--evaluator-fake", "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 0
        # All five filtered CSVs exist.
        for bucket in ("would_enrich", "state_mismatch", "proxy_too_broad",
                       "no_match", "review"):
            assert (tmp_path / f"nevo_v2_enrich_{bucket}_{pid}.csv").exists()
        summary = json.loads(
            (tmp_path / f"nevo_v2_enrich_proposals_{pid}.json").read_text()
        )
        assert set(summary["filtered_artifacts"]) == {
            "would_enrich", "state_mismatch", "proxy_too_broad", "no_match",
            "review",
        }
        assert "enrich_ready_count" in summary
        assert "manual_review_required_count" in summary
        assert summary["persisted_writes"] == 0
        out = capsys.readouterr().out
        assert "REVIEW PACKAGE" in out
        assert set(store.reads) <= {
            "get_project", "list_products_for_project", "list_nevo_entries"
        }


# ---------------------------------------------------------------------------
# Safety — defaults unchanged; routes clean.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_v1_default_and_embeddings_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

    def test_routes_do_not_import_v2(self) -> None:
        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
            or "nevo_nutrition_safety" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

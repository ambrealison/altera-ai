"""Phase Quality-V2-AD — project-level NEVO V2 batch dry-run.

Loads products from an existing project (read-only), reuses the V2-AC
batch/dedup/matching pipeline, excludes commercial fields, and compares the
batch match against the project's already-applied V2 records. No DB writes; V1
default; embeddings off; no routes.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import nevo_v2_project_batch_dry_run as proj
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)


def _product(name, *, items_sold="99999"):
    # items_sold is a COMMERCIAL field that must never be read/leaked.
    return SimpleNamespace(
        id=uuid4(), product_name=name, brand="Lindt",
        retailer_category="Confiserie", ingredients_text="cacao, sucre",
        labels=("bio",), items_sold=Decimal(items_sold))


def _total(pid, code, name):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal("7"),
        status=NutritionEnrichmentStatus.ENRICHED,
        source_metadata={"nevo_code": code, "nevo_food_name": name})


def _split(pid, nutrient):
    return SimpleNamespace(
        product_id=pid, nutrient=nutrient, unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings_split", enriched_value=Decimal("0"),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={})


class _ReadOnlyStore:
    def __init__(self, products, records):
        self._products = products
        self._records = records
        self.reads: list[str] = []

    def get_project(self, project_id):
        self.reads.append("get_project")
        return object()

    def list_products_for_project(self, project_id):
        self.reads.append("list_products_for_project")
        return self._products

    def list_enrichment_records_for_project(self, project_id):
        self.reads.append("list_enrichment_records_for_project")
        return self._records

    def __getattr__(self, name):
        if name.split("_")[0] in ("add", "update", "delete", "upsert",
                                  "insert", "save", "create"):
            raise AssertionError(f"read-only violation: {name}")
        raise AttributeError(name)


# ---------------------------------------------------------------------------
# Fake matcher to control batch codes for the existing-V2 comparison.
# ---------------------------------------------------------------------------
def _cand(name, code, sim):
    return SimpleNamespace(candidate_name=name, nevo_code=code, similarity=sim,
                           rank=1, rejection_reason="")


def _decision(*, matched, code, name, conf=0.97, review=False):
    return SimpleNamespace(
        matched=matched, nevo_code=code, food_name_en=name, confidence=conf,
        match_type="concept", review_required=review,
        top_candidates=[_cand(name or "x", code, conf)])


class _FakeMatcher:
    """Returns a scripted decision keyed by product_name."""

    def __init__(self, by_name):
        self._by_name = by_name

    def decide(self, query, top_k):  # noqa: ARG002
        return self._by_name[query["product_name"]]


# ---------------------------------------------------------------------------
# Part C — dedup on project products.
# ---------------------------------------------------------------------------
class TestDedup:
    def test_groups_duplicates(self) -> None:
        a, b, c = (_product("Chocolat Noir 70%"), _product("Chocolat Noir 70%"),
                   _product("Pois Chiches"))
        groups = proj.dedupe_products([a, b, c], enabled=True)
        assert len(groups) == 2
        choc = next(g for g in groups
                    if g["representative_product_name"] == "Chocolat Noir 70%")
        assert len(choc["products"]) == 2

    def test_descriptor_excludes_commercial(self) -> None:
        desc = proj._product_descriptor(_product("X", items_sold="123456"))
        assert "123456" not in json.dumps(desc)
        assert set(desc) == {"product_name", "brand", "category", "ingredients",
                             "labels", "pack_size"}


# ---------------------------------------------------------------------------
# Part D — existing-V2 comparison.
# ---------------------------------------------------------------------------
class TestExistingV2Compare:
    def test_matches_existing_helper(self) -> None:
        assert proj._matches_existing(
            "N1", {"total": True, "code": "N1"}) == "true"
        assert proj._matches_existing(
            "N2", {"total": True, "code": "N1"}) == "false"
        assert proj._matches_existing(
            "N1", {"total": False, "code": ""}) == "unknown"
        assert proj._matches_existing(
            "", {"total": True, "code": "N1"}) == "unknown"

    def test_build_results_comparison(self) -> None:
        a = _product("Chocolat Noir 70%")     # existing N-CHOC, batch N-CHOC
        b = _product("Yaourt Grec")           # existing N-OTHER, batch N-YOG
        c = _product("Pois Chiches")          # no existing
        groups = proj.dedupe_products([a, b, c], enabled=True)
        records = [_total(a.id, "N-CHOC", "Chocolate dark"),
                   _split(a.id, "plant_protein_pct"),
                   _total(b.id, "N-OTHER", "Other")]
        existing = proj._existing_v2_index(records)
        matcher = _FakeMatcher({
            "Chocolat Noir 70%": _decision(matched=True, code="N-CHOC",
                                           name="Chocolate dark"),
            "Yaourt Grec": _decision(matched=True, code="N-YOG",
                                     name="Yoghurt Greek"),
            "Pois Chiches": _decision(matched=True, code="N-CHICK",
                                      name="Chickpeas"),
        })
        rows = proj.build_results(groups, matcher=matcher, top_k=5,
                                  existing=existing)
        by_pid = {r["product_id"]: r for r in rows}
        ra = by_pid[str(a.id)]
        assert ra["batch_matches_existing_v2"] == "true"
        assert ra["existing_v2_total_record"] is True
        assert ra["existing_v2_split_record"] is True
        rb = by_pid[str(b.id)]
        assert rb["batch_matches_existing_v2"] == "false"
        rc = by_pid[str(c.id)]
        assert rc["batch_matches_existing_v2"] == "unknown"
        assert rc["existing_v2_total_record"] is False


# ---------------------------------------------------------------------------
# Part A/B/E/F — end-to-end read-only run.
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def _run(self, tmp_path, store, project_id, *extra):
        return proj.main(
            ["--project-id", str(project_id), "--output-dir", str(tmp_path),
             "--reference-source", "fixture", "--cache-dir", "",
             "--evaluator-fake", "--top-k", "5", "--run-id", "R1", *extra],
            store=store)

    def test_read_only_and_artifacts(self, tmp_path) -> None:
        a, b, c = (_product("Chocolat Noir 70%"), _product("Chocolat Noir 70%"),
                   _product("Pois Chiches"))
        records = [_total(a.id, "N-CHOC", "Chocolate dark"),
                   _split(a.id, "plant_protein_pct")]
        store = _ReadOnlyStore([a, b, c], records)
        project_id = uuid4()
        rc = self._run(tmp_path, store, project_id)
        assert rc == 0
        # only read methods were called.
        assert set(store.reads) <= {
            "get_project", "list_products_for_project",
            "list_enrichment_records_for_project"}
        suffix = f"{project_id}_R1"
        for name in ("summary", "results", "dedup_groups", "auto_ready",
                     "needs_review", "no_match", "high_risk"):
            ext = "json" if name == "summary" else "csv"
            assert (tmp_path
                    / f"nevo_v2_project_batch_{name}_{suffix}.{ext}").exists()
        # commercial value never leaks into any artifact.
        for art in tmp_path.glob("nevo_v2_project_batch_*"):
            assert "99999" not in art.read_text(encoding="utf-8")

    def test_summary_consistent(self, tmp_path) -> None:
        a, b, c = (_product("Chocolat Noir 70%"), _product("Chocolat Noir 70%"),
                   _product("Pois Chiches"))
        records = [_total(a.id, "N-CHOC", "Chocolate dark"),
                   _split(a.id, "plant_protein_pct"),
                   _total(c.id, "N-OTHER", "Other")]
        store = _ReadOnlyStore([a, b, c], records)
        project_id = uuid4()
        self._run(tmp_path, store, project_id)
        s = json.loads((tmp_path
                        / f"nevo_v2_project_batch_summary_{project_id}_R1.json"
                        ).read_text())
        assert s["raw_product_count"] == 3
        assert s["unique_product_count"] == 2
        assert s["dedupe_reduction_pct"] == 33.33
        assert s["existing_v2_total_count"] == 2  # a + c
        assert s["existing_v2_split_product_count"] == 1  # a
        # per-product result rows == raw product count.
        rows = list(csv.DictReader(
            (tmp_path / f"nevo_v2_project_batch_results_{project_id}_R1.csv"
             ).open()))
        assert len(rows) == 3

    def test_limit_products(self, tmp_path) -> None:
        prods = [_product(f"P{i}") for i in range(5)]
        store = _ReadOnlyStore(prods, [])
        project_id = uuid4()
        self._run(tmp_path, store, project_id, "--limit-products", "2")
        s = json.loads((tmp_path
                        / f"nevo_v2_project_batch_summary_{project_id}_R1.json"
                        ).read_text())
        assert s["raw_product_count"] == 2

    def test_missing_project_fails(self, tmp_path, capsys) -> None:
        class _NoProject(_ReadOnlyStore):
            def get_project(self, project_id):
                self.reads.append("get_project")
                return None

        rc = self._run(tmp_path, _NoProject([], []), uuid4())
        assert rc == 2
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Safety.
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

    def test_routes_do_not_import_project_batch(self) -> None:
        api_dir = Path(proj.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "nevo_v2_project_batch_dry_run" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

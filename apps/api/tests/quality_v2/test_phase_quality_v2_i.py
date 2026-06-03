"""Phase Quality-V2-I — read-only NEVO V1-vs-V2 shadow comparison CLI.

All offline (fake provider / fixture reference / stub store). The CLI must
never call a store write method. V1 stays default; embeddings disabled by
default; a present VOYAGE_API_KEY alone enables nothing; no route imports
V2/embeddings.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import compare_nevo_v1_v2 as cli
from altera_api.classification_v2.compare_nevo_v1_v2 import (
    agreement_bucket,
    risk_bucket,
)
from altera_api.embeddings.provider import EmbeddingProviderError


# ---------------------------------------------------------------------------
# agreement_bucket
# ---------------------------------------------------------------------------
class TestAgreementBucket:
    def test_both_no_match(self) -> None:
        assert agreement_bucket(
            v1_matched=False, v1_code=None, v1_name=None,
            v2_matched=False, v2_code=None, v2_name=None,
        ) == "both_no_match"

    def test_v1_only(self) -> None:
        assert agreement_bucket(
            v1_matched=True, v1_code="1", v1_name="Tofu",
            v2_matched=False, v2_code=None, v2_name=None,
        ) == "v1_only"

    def test_v2_only(self) -> None:
        assert agreement_bucket(
            v1_matched=False, v1_code=None, v1_name=None,
            v2_matched=True, v2_code="2", v2_name="Tofu",
        ) == "v2_only"

    def test_same_code(self) -> None:
        assert agreement_bucket(
            v1_matched=True, v1_code="5519", v1_name="Tofu",
            v2_matched=True, v2_code="5519", v2_name="Tofu unprepared",
        ) == "same_code"

    def test_same_concept_different_code(self) -> None:
        assert agreement_bucket(
            v1_matched=True, v1_code="305", v1_name="Quark low fat",
            v2_matched=True, v2_code="307", v2_name="Quark full fat",
        ) == "same_concept"

    def test_disagreement(self) -> None:
        assert agreement_bucket(
            v1_matched=True, v1_code="1", v1_name="Tofu",
            v2_matched=True, v2_code="2", v2_name="Apple",
        ) == "disagreement_needs_review"


# ---------------------------------------------------------------------------
# risk_bucket
# ---------------------------------------------------------------------------
class TestRiskBucket:
    def test_same_code_is_safe(self) -> None:
        assert risk_bucket(
            agreement="same_code", product_name="Tofu nature", v1_name="Tofu",
            v2_name="Tofu unprepared", v2_matched=True, v2_review_required=False,
        ) == "safe_agreement"

    def test_both_no_match_is_safe(self) -> None:
        assert risk_bucket(
            agreement="both_no_match", product_name="Mystery", v1_name="",
            v2_name="", v2_matched=False, v2_review_required=False,
        ) == "safe_agreement"

    def test_same_concept_v2_more_specific(self) -> None:
        assert risk_bucket(
            agreement="same_concept", product_name="Fromage", v1_name="Cheese",
            v2_name="Cheese Brie 60", v2_matched=True, v2_review_required=False,
        ) == "v2_more_specific"

    def test_same_concept_v1_more_specific(self) -> None:
        assert risk_bucket(
            agreement="same_concept", product_name="Lentilles",
            v1_name="Lentils red boiled", v2_name="Lentils",
            v2_matched=True, v2_review_required=False,
        ) == "v1_more_specific"

    def test_v2_review_only(self) -> None:
        assert risk_bucket(
            agreement="disagreement_needs_review", product_name="Tofu nature",
            v1_name="Tofu", v2_name="Apple", v2_matched=True,
            v2_review_required=True,
        ) == "v2_review_only"

    def test_v2_potential_false_positive_when_v2_offconcept(self) -> None:
        # Product has no mapped concept → cannot verify V2; auto-accept is
        # a potential false positive to inspect.
        assert risk_bucket(
            agreement="v2_only", product_name="Mystery box xyz", v1_name="",
            v2_name="Tofu", v2_matched=True, v2_review_required=False,
        ) == "v2_potential_false_positive"

    def test_v2_potential_false_positive_on_disagreement_v2_offconcept(self) -> None:
        # V1 matched the product concept (tofu); V2 matched a different food.
        assert risk_bucket(
            agreement="disagreement_needs_review", product_name="Tofu nature",
            v1_name="Tofu", v2_name="Apple", v2_matched=True,
            v2_review_required=False,
        ) == "v2_potential_false_positive"

    def test_v1_only_needs_manual_inspection(self) -> None:
        assert risk_bucket(
            agreement="v1_only", product_name="Tofu nature", v1_name="Tofu",
            v2_name="", v2_matched=False, v2_review_required=False,
        ) == "manual_inspection_needed"

    # Phase Quality-V2-J — V2 better than V1.
    def test_v2_better_than_v1_on_v2_only_matching_concept(self) -> None:
        # V1 abstained; V2 matched the product's own concept.
        assert risk_bucket(
            agreement="v2_only", product_name="Chocolat Noir", v1_name="",
            v2_name="Chocolate dark", v2_matched=True, v2_review_required=False,
        ) == "v2_better_than_v1"

    def test_v2_better_than_v1_when_v1_offconcept(self) -> None:
        # V1 matched a milk drink for dark chocolate; V2 matched chocolate.
        assert risk_bucket(
            agreement="disagreement_needs_review", product_name="Chocolat Noir",
            v1_name="Milk chocolate-flavoured full fat", v2_name="Chocolate dark",
            v2_matched=True, v2_review_required=False,
        ) == "v2_better_than_v1"


# ---------------------------------------------------------------------------
# Provider selection — VOYAGE_API_KEY alone must not enable Voyage.
# ---------------------------------------------------------------------------
class TestProviderSelection:
    def test_key_alone_uses_fake(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-not-used")
        provider, name = cli._build_v2_provider("voyage-4-lite", require_voyage=False)
        assert name == "fake"

    def test_require_voyage_without_enable_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        with pytest.raises(EmbeddingProviderError):
            cli._build_v2_provider("voyage-4-lite", require_voyage=True)

    def test_enabled_without_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "true")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(EmbeddingProviderError):
            cli._build_v2_provider("voyage-4-lite", require_voyage=False)


# ---------------------------------------------------------------------------
# Read-only store spy — the CLI must never call a write method.
# ---------------------------------------------------------------------------
class _ReadOnlyStoreSpy:
    """Implements only the read methods the CLI needs; any write-method
    access raises, proving the comparison never mutates the store."""

    _WRITES = frozenset({
        "add_product", "add_products_bulk", "add_upload", "update_upload",
        "delete_upload", "add_enrichment_record", "append_audit",
        "upsert_pt_classification", "upsert_wwf_classification",
        "upsert_review_item", "add_review_decision", "remove_review_item",
        "add_run", "add_export_record", "add_job", "update_job",
        "add_classification_job", "update_classification_job",
        "add_ingestion_job", "update_ingestion_job", "create_project",
        "upsert_user", "upsert_wwf_ingredients_for_product",
    })

    def __init__(self, project, products, nevo_entries) -> None:
        self._project = project
        self._products = products
        self._nevo = nevo_entries
        self.reads: list[str] = []

    def get_project(self, project_id):
        self.reads.append("get_project")
        return self._project

    def list_products_for_project(self, project_id):
        self.reads.append("list_products_for_project")
        return self._products

    def list_nevo_entries(self):
        self.reads.append("list_nevo_entries")
        return self._nevo

    def __getattr__(self, name):
        if name in self._WRITES:
            raise AssertionError(f"read-only violation: store.{name} called")
        raise AttributeError(name)


def _product(name, *, category=None, ingredients=None):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=category,
        retailer_subcategory=None, ingredients_text=ingredients,
        labels=(), pt_fields=object(),  # pt_fields truthy → eligible
    )


class TestCliReadOnly:
    def _run(self, tmp_path, store, extra=None):
        argv = [
            "--project-id", str(uuid4()),
            "--reference-source", "fixture",
            "--cache-dir", "",
            "--output-dir", str(tmp_path),
            *(extra or []),
        ]
        # project id must match what the spy returns; spy ignores the id.
        return cli.main(argv, store=store)

    def test_writes_csv_and_no_db_writes(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        products = [
            _product("Tofu nature"),
            _product("Pois chiches"),
            _product("Menu du jour surprise xyz"),
        ]
        spy = _ReadOnlyStoreSpy(project=object(), products=products, nevo_entries=[])
        rc = self._run(tmp_path, spy)
        assert rc == 0
        # CSV written with the full column set.
        csvs = list(tmp_path.glob("nevo_v1_v2_comparison_*.csv"))
        assert len(csvs) == 1
        header = csvs[0].read_text().splitlines()[0]
        assert header == ",".join(cli.COMPARISON_CSV_COLUMNS)
        # One data row per product.
        assert len(csvs[0].read_text().splitlines()) == 1 + len(products)
        out = capsys.readouterr().out
        assert "shadow comparison" in out
        assert "no database writes" in out.lower()
        # Only read methods were called.
        assert set(spy.reads) <= {
            "get_project", "list_products_for_project", "list_nevo_entries"
        }

    def test_project_not_found_returns_nonzero(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        spy = _ReadOnlyStoreSpy(project=None, products=[], nevo_entries=[])
        rc = self._run(tmp_path, spy)
        assert rc == 2

    def test_limit_products_applies(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        products = [_product(f"Food {i}") for i in range(5)]
        spy = _ReadOnlyStoreSpy(project=object(), products=products, nevo_entries=[])
        rc = self._run(tmp_path, spy, extra=["--limit-products", "2"])
        assert rc == 0
        csv_path = next(tmp_path.glob("nevo_v1_v2_comparison_*.csv"))
        assert len(csv_path.read_text().splitlines()) == 1 + 2


# ---------------------------------------------------------------------------
# Safety — routes clean, defaults unchanged.
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

    def test_v1_default_and_embeddings_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from altera_api.classification_v2.nevo_matcher import get_nevo_matcher
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert get_nevo_matcher().version == "v1"
        assert embeddings_enabled() is False

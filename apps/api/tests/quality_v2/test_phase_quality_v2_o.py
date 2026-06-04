"""Phase Quality-V2-O — admin/internal NEVO V2 opt-in (dry-run).

Strictly controlled, admin-only, dry-run-first. V1 stays default; embeddings
off by default; a present VOYAGE_API_KEY alone does not enable V2; the
persisted write path is gated; no route imports V2/embeddings; no DB writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import nevo_v2_enrich as cli
from altera_api.classification_v2.nevo_v2_enrich import _safety_action


# ---------------------------------------------------------------------------
# safety_action — precision-first (only high-conf accept w/ value enriches).
# ---------------------------------------------------------------------------
class TestSafetyAction:
    def test_no_match_skips(self) -> None:
        assert _safety_action(
            matched=False, review_required=True, protein=None, confidence=0.0
        ) == "skip_no_match"

    def test_review_routes_to_review(self) -> None:
        assert _safety_action(
            matched=True, review_required=True, protein=10.0, confidence=0.6
        ) == "route_to_review"

    def test_low_confidence_routes_to_review(self) -> None:
        assert _safety_action(
            matched=True, review_required=False, protein=10.0, confidence=0.5
        ) == "route_to_review"

    def test_no_value_skips(self) -> None:
        assert _safety_action(
            matched=True, review_required=False, protein=None, confidence=0.96
        ) == "skip_no_nutrition_value"

    def test_high_conf_with_value_would_enrich(self) -> None:
        assert _safety_action(
            matched=True, review_required=False, protein=10.0, confidence=0.96
        ) == "would_enrich"


# ---------------------------------------------------------------------------
# Read-only store spy.
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


def _product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        retailer_subcategory=None, ingredients_text=None, labels=(),
        pt_fields=object(),
    )


def _nevo_entry(code, protein, name="Tofu"):
    return SimpleNamespace(
        nevo_code=code, protein_g_per_100g=protein, food_name_en=name,
        food_name_nl="", food_group="", plant_protein_g_per_100g=None,
        animal_protein_g_per_100g=None,
    )


# ---------------------------------------------------------------------------
# Activation model + safety (Part A/B).
# ---------------------------------------------------------------------------
class TestActivation:
    def test_v2_without_enable_fails_clearly(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        store = _ReadOnlyStore([_product("Tofu nature")])
        rc = cli.main(
            ["--project-id", str(uuid4()), "--matcher-version", "v2-embeddings",
             "--reference-source", "fixture", "--cache-dir", "",
             "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 2
        assert "ALTERA_ENABLE_EMBEDDINGS=true" in capsys.readouterr().out
        assert not list(tmp_path.glob("nevo_v2_enrich_*"))

    def test_voyage_key_alone_does_not_enable(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-not-used")
        store = _ReadOnlyStore([_product("Tofu nature")])
        rc = cli.main(
            ["--project-id", str(uuid4()), "--matcher-version", "v2-embeddings",
             "--reference-source", "fixture", "--cache-dir", "",
             "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 2  # key alone is not enough

    def test_v2_rules_rejected(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        store = _ReadOnlyStore([_product("Tofu nature")])
        rc = cli.main(
            ["--project-id", str(uuid4()), "--matcher-version", "v2-rules",
             "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# Dry-run (Part C) — no DB writes; artifacts written; observability present.
# ---------------------------------------------------------------------------
class TestDryRun:
    def test_v2_dry_run_writes_proposals_no_db(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        store = _ReadOnlyStore(
            [_product("Tofu nature"), _product("Pois chiches")],
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
        assert (tmp_path / f"nevo_v2_enrich_proposals_{pid}.csv").exists()
        summary = json.loads(
            (tmp_path / f"nevo_v2_enrich_proposals_{pid}.json").read_text()
        )
        assert summary["safety_mode"] == "dry_run"
        assert summary["persisted_writes"] == 0
        assert summary["matcher_version"] == "v2-embeddings"
        assert summary["embedding_provider"] == "fake"
        assert "nutrition_safety_counts" in summary
        out = capsys.readouterr().out
        assert "DRY-RUN" in out and "no database writes" in out.lower()
        # Tofu (NEVO-TOFU has a protein value) → would_enrich.
        assert summary["nutrition_safety_counts"]["would_enrich"] >= 1
        # Only read methods were called.
        assert set(store.reads) <= {
            "get_project", "list_products_for_project", "list_nevo_entries"
        }

    def test_v1_dry_run_works(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        store = _ReadOnlyStore([_product("Tofu nature")])
        pid = str(uuid4())
        rc = cli.main(
            ["--project-id", pid, "--matcher-version", "v1",
             "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 0
        assert (tmp_path / f"nevo_v2_enrich_proposals_{pid}.csv").exists()
        summary = json.loads(
            (tmp_path / f"nevo_v2_enrich_proposals_{pid}.json").read_text()
        )
        assert summary["matcher_version"] == "v1"


# ---------------------------------------------------------------------------
# Apply path is explicit-only AND gated (Part D) — writes nothing.
# ---------------------------------------------------------------------------
class TestApplyGated:
    def test_apply_refuses_and_writes_nothing(self, tmp_path, capsys) -> None:
        store = _ReadOnlyStore([_product("Tofu nature")])
        rc = cli.main(
            ["--project-id", str(uuid4()), "--matcher-version", "v2-embeddings",
             "--evaluator-fake", "--apply", "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "--apply is gated" in out
        assert "migration" in out.lower()
        # No artifacts, no store access (refused before any work).
        assert not list(tmp_path.glob("nevo_v2_enrich_*"))
        assert store.reads == []


# ---------------------------------------------------------------------------
# Safety — defaults unchanged; routes clean.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_v1_default_and_embeddings_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
            or "nevo_v2_enrich" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

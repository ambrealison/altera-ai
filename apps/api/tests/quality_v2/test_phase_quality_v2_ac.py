"""Phase Quality-V2-AC — retailer-scale (≈30k) NEVO V2 batch dry-run.

Deduplicated, sensitive-column-excluded, DB-free matching report. The hard rule:
commercial VALUES (sales/price/margin/…) never reach the embedding text or any
output artifact. No routes, no DB writes, V1 default, embeddings off by default.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from altera_api.classification_v2 import nevo_v2_batch_dry_run as batch

_SENSITIVE_COLS = ["Sales Volume", "Unit Price", "Margin %", "Units Sold",
                   "Market Share", "Sell Through", "Store Count", "Velocity"]
_SAFE_COLS = ["Product Name", "Brand", "Category", "Ingredients", "Pack Size"]
_ALL_COLS = _SAFE_COLS + _SENSITIVE_COLS

# sensitive VALUES that must never appear in any output artifact.
_SENSITIVE_VALUES = ["12000", "9000", "3000", "8000", "15000", "2.50", "2.40",
                     "0.30", "0.28", "0.40", "5000", "4000", "7000", "0.12",
                     "0.20"]


def _row(name, brand, cat, ingr, pack, *sens):
    vals = [name, brand, cat, ingr, pack, *sens]
    return dict(zip(_ALL_COLS, vals, strict=True))


def _write_csv(tmp_path, rows, cols=_ALL_COLS, fname="retailer.csv"):
    path = tmp_path / fname
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def _default_rows():
    return [
        _row("Chocolat Noir 70%", "Lindt", "Confiserie", "cacao, sucre",
             "100g", "12000", "2.50", "0.30", "5000", "0.12", "0.2", "40", "1.1"),
        _row("Chocolat Noir 70%", "Lindt", "Confiserie", "cacao, sucre",
             "100g", "9000", "2.40", "0.28", "4000", "0.10", "0.2", "40", "1.0"),
        _row("CHOCOLAT NOIR 70%", "Lindt", "Confiserie", "cacao, sucre",
             "100g", "3000", "2.60", "0.31", "2000", "0.09", "0.1", "30", "0.9"),
        _row("Pois Chiches", "Bonduelle", "Legumes", "pois chiches, eau",
             "400g", "8000", "1.20", "0.20", "3000", "0.05", "0.1", "20", "0.5"),
        _row("Liquide Vaisselle Citron", "Paic", "Entretien", "tensioactifs",
             "500ml", "15000", "3.00", "0.40", "7000", "0.20", "0.3", "50", "1.5"),
    ]


def _run(tmp_path, input_path, *extra, run_id="RUN"):
    return batch.main(
        ["--input", str(input_path), "--output-dir", str(tmp_path),
         "--reference-source", "fixture", "--cache-dir", "",
         "--evaluator-fake", "--top-k", "5", "--run-id", run_id, *extra])


def _summary(tmp_path, run_id="RUN"):
    return json.loads(
        (tmp_path / f"nevo_v2_batch_summary_{run_id}.json").read_text())


# ---------------------------------------------------------------------------
# Part B — sensitive column detection.
# ---------------------------------------------------------------------------
class TestSensitiveColumns:
    def test_detects_all_sensitive(self) -> None:
        detected = {d["column_name"]
                    for d in batch.detect_sensitive_columns(_ALL_COLS)}
        assert detected == set(_SENSITIVE_COLS)
        assert all(d["action"] == "excluded"
                   for d in batch.detect_sensitive_columns(_ALL_COLS))

    def test_safe_columns_not_flagged(self) -> None:
        detected = {d["column_name"]
                    for d in batch.detect_sensitive_columns(_SAFE_COLS)}
        assert detected == set()

    def test_roles_exclude_sensitive(self) -> None:
        sens = {d["column_name"]
                for d in batch.detect_sensitive_columns(_ALL_COLS)}
        roles = batch._detect_roles([c for c in _ALL_COLS if c not in sens])
        assert roles["product_name"] == "Product Name"
        assert roles["brand"] == "Brand"
        assert roles["category"] == "Category"
        assert "Sales Volume" not in roles.values()


# ---------------------------------------------------------------------------
# Part C — dedup.
# ---------------------------------------------------------------------------
class TestDedup:
    def test_canonical_key_groups_duplicates(self, tmp_path) -> None:
        rows = _default_rows()
        roles = batch._detect_roles(_SAFE_COLS)
        groups = batch.dedupe(rows, roles, enabled=True)
        assert len(groups) == 3  # 3 chocolat → 1, pois chiches, vaisselle
        choc = next(g for g in groups
                    if g["representative_product_name"] == "Chocolat Noir 70%")
        assert len(choc["raw_row_indices"]) == 3
        assert choc["raw_row_indices"] == [0, 1, 2]  # maps back to raw rows
        assert "product_name" in choc["safe_fields_used"]

    def test_dedupe_disabled_keeps_all(self, tmp_path) -> None:
        roles = batch._detect_roles(_SAFE_COLS)
        groups = batch.dedupe(_default_rows(), roles, enabled=False)
        assert len(groups) == 5


# ---------------------------------------------------------------------------
# Part D/E/F — end-to-end dry-run.
# ---------------------------------------------------------------------------
class TestBatchRun:
    def test_writes_all_artifacts_no_sensitive_leak(self, tmp_path) -> None:
        path = _write_csv(tmp_path, _default_rows())
        rc = _run(tmp_path, path)
        assert rc == 0
        for name in ("dedup_groups", "results", "auto_ready", "needs_review",
                     "no_match", "high_risk", "sensitive_columns"):
            assert (tmp_path / f"nevo_v2_batch_{name}_RUN.csv").exists()
        assert (tmp_path / "nevo_v2_batch_summary_RUN.json").exists()

        # NO sensitive VALUE may appear in ANY nevo_v2_batch_* output artifact.
        for art in tmp_path.glob("nevo_v2_batch_*"):
            text = art.read_text(encoding="utf-8")
            for value in _SENSITIVE_VALUES:
                assert value not in text, f"{value} leaked into {art.name}"
            # and the sensitive COLUMN NAMES never appear in results / packages
            # (only in the dedicated sensitive_columns report + summary list).
            if art.name not in ("nevo_v2_batch_sensitive_columns_RUN.csv",
                                "nevo_v2_batch_summary_RUN.json"):
                for col in _SENSITIVE_COLS:
                    assert col not in text, f"{col} leaked into {art.name}"

    def test_summary_metrics_consistent(self, tmp_path) -> None:
        path = _write_csv(tmp_path, _default_rows())
        _run(tmp_path, path)
        s = _summary(tmp_path)
        assert s["raw_row_count"] == 5
        assert s["unique_product_count"] == 3
        assert s["max_duplicate_group_size"] == 3
        assert s["dedupe_reduction_pct"] == 40.0
        assert set(s["sensitive_columns_detected"]) == set(_SENSITIVE_COLS)
        assert s["embedding_provider"] == "fake"
        # every unique product lands in exactly one of the four buckets.
        assert (s["auto_ready_count"] + s["needs_review_count"]
                + s["no_match_count"] + s["high_risk_count"]) == 3
        # the non-food product is policy-excluded.
        assert s["policy_excluded_count"] == 1
        assert s["recommendation"] in ("ready_for_human_review",
                                       "investigate_high_risk")

    def test_review_packages_have_reviewer_columns(self, tmp_path) -> None:
        path = _write_csv(tmp_path, _default_rows())
        _run(tmp_path, path)
        with (tmp_path / "nevo_v2_batch_auto_ready_RUN.csv").open() as fh:
            header = next(csv.reader(fh))
        for col in ("manual_decision", "reviewer_notes", "approved_nevo_code",
                    "approved_nevo_name", "approved_protein_g_per_100g"):
            assert col in header

    def test_limit_rows(self, tmp_path) -> None:
        path = _write_csv(tmp_path, _default_rows())
        _run(tmp_path, path, "--limit-rows", "3")
        s = _summary(tmp_path)
        assert s["raw_row_count"] == 3

    def test_no_usable_product_field_fails(self, tmp_path, capsys) -> None:
        # only sensitive columns + a non-identification column.
        path = _write_csv(tmp_path, [
            {"Sales Volume": "100", "Unit Price": "2", "Region": "north"}],
            cols=["Sales Volume", "Unit Price", "Region"], fname="bad.csv")
        rc = _run(tmp_path, path)
        assert rc == 2
        assert "insufficient_product_fields" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Part G — safety posture.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_batch_writes_no_db(self) -> None:
        src = Path(batch.__file__).read_text(encoding="utf-8")
        assert "get_store" not in src
        assert "add_enrichment_record" not in src
        assert ".insert(" not in src
        assert "store_factory" not in src

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

    def test_routes_do_not_import_batch(self) -> None:
        api_dir = Path(batch.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "nevo_v2_batch_dry_run" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

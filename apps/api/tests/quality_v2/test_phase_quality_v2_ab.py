"""Phase Quality-V2-AB — show matched NEVO food instead of the generic label.

The API now surfaces a friendly ``source_display_label`` (and reference
name/code) for V2-applied records from ``source_metadata``; a guarded backfill
normalises that metadata; a read-only audit verifies it. No protein/split
values change, no matcher behaviour changes, V1 stays default, embeddings off.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.api.routes import _nutrition_row_fields, _v2_display_from_metadata
from altera_api.classification_v2 import (
    audit_nevo_v2_display_metadata as disp_audit,
)
from altera_api.classification_v2 import (
    backfill_nevo_v2_display_metadata as backfill,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.protein_tracker import ProteinTrackerGroup


def _rec(nutrient, value, *, source=NutritionEnrichmentSource.NEVO,
         match_method="ai_assisted", source_version="v2_embeddings",
         rationale="NEVO V2 apply (approved review package)",
         source_metadata=None):
    return SimpleNamespace(
        product_id=uuid4(), nutrient=nutrient, original_value=None,
        enriched_value=Decimal(value), unit="g_per_100g", source=source,
        confidence=Decimal("0.95"),
        status=NutritionEnrichmentStatus.ENRICHED, rationale=rationale,
        created_at=datetime(2026, 6, 4, tzinfo=UTC), created_by=None,
        match_method=match_method, source_version=source_version,
        source_metadata=source_metadata)


def _product():
    return SimpleNamespace(id=uuid4(), product_name="Muesli Fruits Rouges 450g",
                           pt_fields=None)


def _classification():
    return SimpleNamespace(pt_group=ProteinTrackerGroup.PLANT_BASED_CORE)


# ---------------------------------------------------------------------------
# Part A/D — metadata helper + API row builder.
# ---------------------------------------------------------------------------
class TestDisplayHelper:
    def test_prefers_display_label(self) -> None:
        out = _v2_display_from_metadata(SimpleNamespace(source_metadata={
            "nevo_food_name": "Muesli w fruit/seeds", "nevo_code": "N1",
            "display_label": "NEVO V2: Muesli w fruit/seeds"}))
        assert out["display_label"] == "NEVO V2: Muesli w fruit/seeds"
        assert out["reference_name"] == "Muesli w fruit/seeds"

    def test_derives_label_from_approved_name_pre_backfill(self) -> None:
        # pre-backfill the apply only stored approved_nevo_name.
        out = _v2_display_from_metadata(SimpleNamespace(source_metadata={
            "approved_nevo_name": "Muesli w fruit/seeds",
            "approved_nevo_code": "N1"}))
        assert out["display_label"] == "NEVO V2: Muesli w fruit/seeds"
        assert out["reference_code"] == "N1"

    def test_none_for_v1_no_metadata(self) -> None:
        assert _v2_display_from_metadata(
            SimpleNamespace(source_metadata=None)) is None


class TestApiRow:
    def test_v2_row_shows_display_label(self) -> None:
        rec = _rec("protein_pct", "10.7", source_metadata={
            "approved_nevo_name": "Muesli w fruit/seeds",
            "approved_nevo_code": "N1"})
        row = _nutrition_row_fields(_product(), _classification(), [rec])
        assert row["source_display_label"] == "NEVO V2: Muesli w fruit/seeds"
        assert row["reference_name"] == "Muesli w fruit/seeds"
        assert row["reference_code"] == "N1"
        # values untouched.
        assert row["protein_pct"] == "10.7"
        assert row["source"] == "nevo"

    def test_v1_row_unchanged_fallback(self) -> None:
        # a V1/legacy enrichment record (no source_metadata) keeps reason as the
        # subtitle and has no display label → frontend falls back to reason.
        rec = _rec("protein_pct", "8.0", match_method="deterministic",
                   source_version=None, rationale="deterministic exact match",
                   source_metadata=None)
        row = _nutrition_row_fields(_product(), _classification(), [rec])
        assert row["source_display_label"] is None
        assert row["reason"] == "deterministic exact match"


# ---------------------------------------------------------------------------
# Part C — backfill.
# ---------------------------------------------------------------------------
_APPROVED_COLUMNS = [
    "product_id", "effective_nevo_code", "effective_nevo_name",
]


class _Store:
    def __init__(self, records):
        self._records = records
        self.updates: list = []

    def list_enrichment_records_for_project(self, project_id):
        return self._records

    def update_enrichment_source_metadata(self, *, product_id, nutrient,
                                          source_version, source_metadata):
        self.updates.append((str(product_id), nutrient, source_version,
                             source_metadata))
        # reflect the update into the in-memory record (so re-run is idempotent).
        for r in self._records:
            if (str(r.product_id) == str(product_id) and r.nutrient == nutrient
                    and r.source_version == source_version):
                r.source_metadata = source_metadata
        return 1


def _write_approved(tmp_path, project_id, rows):
    path = tmp_path / f"nevo_v2_review_approved_candidates_{project_id}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_APPROVED_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def _scenario_records():
    a = uuid4()
    total = _rec("protein_pct", "10.7", source_metadata={
        "approved_nevo_name": "Muesli w fruit/seeds", "approved_nevo_code": "N1"})
    total.product_id = a
    plant = _rec("plant_protein_pct", "10.7", source_version="v2_embeddings_split",
                 rationale="split", source_metadata={"pt_group": "plant_based_core"})
    plant.product_id = a
    animal = _rec("animal_protein_pct", "0", source_version="v2_embeddings_split",
                  rationale="split", source_metadata={"pt_group": "plant_based_core"})
    animal.product_id = a
    manual = _rec("protein_pct", "9", source=NutritionEnrichmentSource.MANUAL_ALTERA,
                  match_method="manual", source_version=None, rationale="manual",
                  source_metadata=None)
    manual.product_id = a
    v1 = _rec("protein_pct", "5", match_method="deterministic",
              source_version=None, rationale="v1", source_metadata=None)
    v1.product_id = uuid4()
    return a, [total, plant, animal, manual, v1]


class TestBackfill:
    def test_dry_run_writes_nothing(self, tmp_path) -> None:
        a, records = _scenario_records()
        appr = _write_approved(tmp_path, "bf", [dict(
            product_id=str(a), effective_nevo_code="N1",
            effective_nevo_name="Muesli w fruit/seeds")])
        store = _Store(records)
        rc = backfill.main(["--project-id", "bf", "--approved-candidates",
                            str(appr), "--output-dir", str(tmp_path)],
                           store=store, generated_at="x")
        assert rc == 0
        assert store.updates == []
        s = json.loads(
            (tmp_path / "nevo_v2_display_metadata_backfill_bf.json").read_text())
        assert s["dry_run"] is True
        assert s["records_that_need_update"] == 3  # total + 2 splits
        assert s["records_updated"] == 0
        assert s["skipped_manual"] == 1
        assert s["skipped_v1"] == 1

    def test_confirmed_updates_only_metadata(self, tmp_path) -> None:
        a, records = _scenario_records()
        appr = _write_approved(tmp_path, "bf2", [dict(
            product_id=str(a), effective_nevo_code="N1",
            effective_nevo_name="Muesli w fruit/seeds")])
        store = _Store(records)
        backfill.main(["--project-id", "bf2", "--approved-candidates",
                       str(appr), "--output-dir", str(tmp_path),
                       "--confirm-backfill-display-metadata"],
                      store=store, generated_at="x")
        assert len(store.updates) == 3
        labels = {u[3]["display_label"] for u in store.updates}
        assert "NEVO V2: Muesli w fruit/seeds" in labels
        assert "NEVO V2 split: Muesli w fruit/seeds" in labels
        # split records carry parent_* keys.
        split_updates = [u for u in store.updates if u[2] == "v2_embeddings_split"]
        assert all(u[3]["parent_nevo_food_name"] == "Muesli w fruit/seeds"
                   for u in split_updates)
        assert all(u[3]["parent_nevo_code"] == "N1" for u in split_updates)
        # manual + V1 never updated (they are not in the updates list).
        assert all(u[2] in ("v2_embeddings", "v2_embeddings_split")
                   for u in store.updates)

    def test_idempotent(self, tmp_path) -> None:
        a, records = _scenario_records()
        appr = _write_approved(tmp_path, "bf3", [dict(
            product_id=str(a), effective_nevo_code="N1",
            effective_nevo_name="Muesli w fruit/seeds")])
        store = _Store(records)
        args = ["--project-id", "bf3", "--approved-candidates", str(appr),
                "--output-dir", str(tmp_path),
                "--confirm-backfill-display-metadata"]
        backfill.main(args, store=store, generated_at="x")
        store.updates.clear()
        backfill.main(args, store=store, generated_at="x")  # second run
        assert store.updates == []  # nothing left to update
        s = json.loads(
            (tmp_path / "nevo_v2_display_metadata_backfill_bf3.json").read_text())
        assert s["records_updated"] == 0
        assert s["records_up_to_date"] == 3


# ---------------------------------------------------------------------------
# Part E — display-metadata audit.
# ---------------------------------------------------------------------------
class _AuditStore:
    def __init__(self, records):
        self._records = records

    def list_enrichment_records_for_project(self, project_id):
        return self._records


class TestDisplayAudit:
    def test_detects_missing(self, tmp_path) -> None:
        _a, records = _scenario_records()
        rc = disp_audit.main(["--project-id", "da", "--output-dir",
                             str(tmp_path)], store=_AuditStore(records),
                            generated_at="x")
        assert rc == 1  # warn — incomplete
        s = json.loads(
            (tmp_path / "nevo_v2_display_metadata_audit_da.json").read_text())
        assert s["audit_status"] == "warn"
        assert s["missing_display_label_count"] == 3

    def test_passes_after_backfill(self, tmp_path) -> None:
        a = uuid4()
        total = _rec("protein_pct", "10.7", source_metadata={
            "nevo_food_name": "Muesli w fruit/seeds", "nevo_code": "N1",
            "display_label": "NEVO V2: Muesli w fruit/seeds"})
        total.product_id = a
        plant = _rec("plant_protein_pct", "10.7",
                     source_version="v2_embeddings_split", source_metadata={
                         "parent_nevo_food_name": "Muesli w fruit/seeds",
                         "parent_nevo_code": "N1",
                         "display_label": "NEVO V2 split: Muesli w fruit/seeds"})
        plant.product_id = a
        rc = disp_audit.main(["--project-id", "da2", "--output-dir",
                             str(tmp_path)], store=_AuditStore([total, plant]),
                            generated_at="x")
        assert rc == 0
        s = json.loads(
            (tmp_path / "nevo_v2_display_metadata_audit_da2.json").read_text())
        assert s["audit_status"] == "pass"
        assert s["recommendation"] == "display_metadata_verified"
        assert s["with_display_label_count"] == 2
        assert s["manual_touched_count"] == 0


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

    def test_routes_do_not_import_v2_clis(self) -> None:
        api_dir = Path(disp_audit.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "backfill_nevo_v2_display_metadata" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

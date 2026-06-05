"""Phase Quality-V2-AA — split audit + app-check + pet-food-as-food + Part E keys.

Read-only audit of the V2 plant/animal split apply (pass/warn/fail), an
app-check CSV, the pet-food-is-food clarification, and standardized split-apply
result-JSON keys. No DB writes; V1 default; embeddings off; routes clean.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip  (avoid persistence<->api cycle)
from altera_api.classification_v2 import apply_nevo_v2_protein_split as splitapply
from altera_api.classification_v2 import audit_nevo_v2_protein_split as audit
from altera_api.classification_v2.nevo_v2_protein_split import split_proposal
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

_PROP_COLUMNS = [
    "product_id", "product_name", "total_protein_g_per_100g", "pt_group",
    "proposed_plant_protein_g_per_100g", "proposed_animal_protein_g_per_100g",
    "split_action", "split_reason",
]


def _prop(pid, *, total, group, plant, animal, action, name="P"):
    return dict(product_id=str(pid), product_name=name,
                total_protein_g_per_100g=total, pt_group=group,
                proposed_plant_protein_g_per_100g=plant,
                proposed_animal_protein_g_per_100g=animal, split_action=action,
                split_reason="")


def _write_props(tmp_path, project_id, rows):
    path = tmp_path / f"nevo_v2_protein_split_proposals_{project_id}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_PROP_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def _prot(pid, value):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal(value),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={})


def _split(pid, nutrient, value, **kw):
    base = dict(
        product_id=pid, nutrient=nutrient, unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings_split", enriched_value=Decimal(value),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={"x": 1})
    base.update(kw)
    return SimpleNamespace(**base)


class _Store:
    def __init__(self, records):
        self._records = records

    def list_enrichment_records_for_project(self, project_id):
        return self._records


def _run(tmp_path, project_id, props, store):
    return audit.main(
        ["--project-id", project_id, "--proposals", str(props),
         "--output-dir", str(tmp_path)], store=store, generated_at="x")


def _audit_json(tmp_path, project_id):
    return json.loads(
        (tmp_path / f"nevo_v2_split_audit_{project_id}.json").read_text())


# ---------------------------------------------------------------------------
# Part A/B — split audit outcomes.
# ---------------------------------------------------------------------------
class TestSplitAudit:
    def _clean_inputs(self, tmp_path, project_id):
        a, b, c = uuid4(), uuid4(), uuid4()
        props = _write_props(tmp_path, project_id, [
            _prop(a, total="24.9", group="animal_core", plant="0",
                  animal="24.9", action="would_split", name="Thon"),
            _prop(b, total="6.8", group="plant_based_core", plant="6.8",
                  animal="0", action="would_split", name="Pois Chiches"),
            _prop(c, total="10", group="composite_products", plant="",
                  animal="", action="needs_review", name="Composite"),
        ])
        records = [
            _prot(a, "24.9"), _split(a, "plant_protein_pct", "0"),
            _split(a, "animal_protein_pct", "24.9"),
            _prot(b, "6.8"), _split(b, "plant_protein_pct", "6.8"),
            _split(b, "animal_protein_pct", "0"),
            _prot(c, "10"),  # needs_review: no split
        ]
        return props, records, (a, b, c)

    def test_clean_split_passes(self, tmp_path) -> None:
        props, records, _ = self._clean_inputs(tmp_path, "ok")
        rc = _run(tmp_path, "ok", props, _Store(records))
        assert rc == 0
        s = _audit_json(tmp_path, "ok")
        assert s["audit_status"] == "pass"
        assert s["recommendation"] == "split_apply_verified"
        assert s["proposal_would_split_count"] == 2
        assert s["proposal_needs_review_count"] == 1
        assert s["applied_split_product_count"] == 2
        assert s["plant_split_record_count"] == 2
        assert s["animal_split_record_count"] == 2
        assert s["matched_would_split_count"] == 2
        assert s["missing_split_count"] == 0
        assert s["sum_mismatch_count"] == 0

    def test_broken_pair_fails(self, tmp_path) -> None:
        props, records, ids = self._clean_inputs(tmp_path, "miss")
        # drop the animal record for product a → a BROKEN pair (plant only).
        records = [r for r in records
                   if not (r.product_id == ids[0]
                           and r.nutrient == "animal_protein_pct")]
        rc = _run(tmp_path, "miss", props, _Store(records))
        assert rc == 2
        s = _audit_json(tmp_path, "miss")
        assert s["audit_status"] == "fail"
        assert s["missing_split_count"] == 1
        assert s["recommendation"] == "rollback_split_recommended"

    def test_unexpected_split_on_needs_review_fails(self, tmp_path) -> None:
        props, records, ids = self._clean_inputs(tmp_path, "unx")
        records += [_split(ids[2], "plant_protein_pct", "5"),
                    _split(ids[2], "animal_protein_pct", "5")]
        rc = _run(tmp_path, "unx", props, _Store(records))
        assert rc == 2
        s = _audit_json(tmp_path, "unx")
        assert s["unexpected_split_count"] == 1
        assert s["audit_status"] == "fail"
        assert s["recommendation"] == "rollback_split_recommended"

    def test_duplicate_split_fails(self, tmp_path) -> None:
        props, records, ids = self._clean_inputs(tmp_path, "dup")
        records.append(_split(ids[0], "plant_protein_pct", "0"))
        rc = _run(tmp_path, "dup", props, _Store(records))
        assert rc == 2
        assert _audit_json(tmp_path, "dup")["duplicate_split_count"] == 1

    def test_sum_mismatch_fails(self, tmp_path) -> None:
        a = uuid4()
        props = _write_props(tmp_path, "sum", [
            _prop(a, total="6.8", group="plant_based_core", plant="6.8",
                  animal="0", action="would_split")])
        records = [_prot(a, "6.8"), _split(a, "plant_protein_pct", "6.8"),
                   _split(a, "animal_protein_pct", "5")]  # 11.8 != 6.8
        rc = _run(tmp_path, "sum", props, _Store(records))
        assert rc == 2
        assert _audit_json(tmp_path, "sum")["sum_mismatch_count"] == 1

    def test_bad_tags_and_missing_metadata_fail(self, tmp_path) -> None:
        a = uuid4()
        props = _write_props(tmp_path, "bad", [
            _prop(a, total="10", group="animal_core", plant="0", animal="10",
                  action="would_split")])
        records = [
            _prot(a, "10"),
            _split(a, "plant_protein_pct", "0", match_method="deterministic"),
            _split(a, "animal_protein_pct", "10", unit="mg",
                   source_metadata=None),
        ]
        rc = _run(tmp_path, "bad", props, _Store(records))
        assert rc == 2
        s = _audit_json(tmp_path, "bad")
        assert s["invalid_match_method_count"] == 1
        assert s["invalid_unit_count"] == 1
        assert s["metadata_missing_count"] == 1


# ---------------------------------------------------------------------------
# Part D — pet food is food (PT-group driven; never flagged for pet-ness).
# ---------------------------------------------------------------------------
class TestPetFoodIsFood:
    def test_pet_animal_core_not_flagged(self, tmp_path) -> None:
        pet = uuid4()
        props = _write_props(tmp_path, "pet", [
            _prop(pet, total="21.8", group="animal_core", plant="0",
                  animal="21.8", action="would_split",
                  name="Croquettes Chat Saumon 1.5kg")])
        records = [_prot(pet, "21.8"), _split(pet, "plant_protein_pct", "0"),
                   _split(pet, "animal_protein_pct", "21.8")]
        rc = _run(tmp_path, "pet", props, _Store(records))
        assert rc == 0
        s = _audit_json(tmp_path, "pet")
        assert s["audit_status"] == "pass"
        assert s["matched_would_split_count"] == 1
        # app-check shows the split for the pet product.
        rows = list(csv.DictReader(
            (tmp_path / "nevo_v2_split_app_check_pet.csv").open()))
        assert rows[0]["product_name"].startswith("Croquettes Chat")
        assert rows[0]["expected_ui_status"] == "split_shown"
        assert rows[0]["animal_protein"] == "21.8"

    def test_composite_pet_food_is_needs_review(self) -> None:
        # A composite pet product is routed to review, not auto-split.
        p = split_proposal(pt_group="composite_products",
                           total_protein=Decimal("18"),
                           has_manual_override=False, has_classification=True)
        assert p["action"] == "needs_review"


# ---------------------------------------------------------------------------
# Part C — app-check CSV columns.
# ---------------------------------------------------------------------------
class TestAppCheck:
    def test_app_check_columns(self, tmp_path) -> None:
        a = uuid4()
        props = _write_props(tmp_path, "app", [
            _prop(a, total="3.8", group="animal_core", plant="0", animal="3.8",
                  action="would_split", name="Yaourt Grec Nature")])
        records = [_prot(a, "3.8"), _split(a, "plant_protein_pct", "0"),
                   _split(a, "animal_protein_pct", "3.8")]
        _run(tmp_path, "app", props, _Store(records))
        rows = list(csv.DictReader(
            (tmp_path / "nevo_v2_split_app_check_app.csv").open()))
        assert set(rows[0].keys()) == {
            "product_id", "product_name", "total_protein", "plant_protein",
            "animal_protein", "plant_plus_animal", "pt_group",
            "expected_ui_status",
        }
        assert rows[0]["plant_plus_animal"] == "3.8"


# ---------------------------------------------------------------------------
# Part E — split-apply result JSON key consistency.
# ---------------------------------------------------------------------------
class TestResultKeys:
    def test_standardized_keys(self, tmp_path) -> None:
        a = uuid4()
        prop_csv = tmp_path / "nevo_v2_protein_split_proposals_k.csv"
        with prop_csv.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_PROP_COLUMNS)
            w.writeheader()
            w.writerow(_prop(a, total="10", group="animal_core", plant="0",
                             animal="10", action="would_split"))

        class _S:
            def __init__(self):
                self.writes = []

            def has_enrichment_provenance_columns(self):
                return True

            def get_enrichment_records_for_product(self, pid):
                return []

            def add_enrichment_record(self, r):
                self.writes.append(r)

        rc = splitapply.main(
            ["--proposals", str(prop_csv), "--project-id", "k",
             "--output-dir", str(tmp_path), "--confirm-apply-split"],
            store=_S(), generated_at="2026-06-04T00:00:00+00:00")
        assert rc == 0
        s = json.loads(
            (tmp_path / "nevo_v2_split_apply_result_k.json").read_text())
        for key in ("written_pairs", "records_written", "would_write_count",
                    "skipped_existing_split_count", "skipped_manual_count",
                    "error_count", "dry_run", "confirmation_present",
                    "limit_apply"):
            assert key in s, key
        assert s["written_pairs"] == 1
        assert s["records_written"] == 2
        # Old *_count variants for the pair/record totals are gone.
        assert "written_pairs_count" not in s
        assert "records_written_count" not in s


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_audit_writes_no_db(self) -> None:
        src = Path(audit.__file__).read_text(encoding="utf-8")
        assert "add_enrichment_record" not in src
        assert ".insert(" not in src

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

    def test_routes_do_not_import_split_audit(self) -> None:
        api_dir = Path(audit.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "audit_nevo_v2_protein_split" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

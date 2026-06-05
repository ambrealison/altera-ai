"""Phase Quality-V2-Y — read-only post-apply audit + 30k scale baseline.

Audits applied V2 enrichment records against the approved candidates / plan and
returns a pass/warn/fail result. No DB writes; not wired into routes; V1 default;
embeddings off. Also exposes the retailer-scale (≈30k) readiness baseline.
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
from altera_api.classification_v2 import audit_nevo_v2_apply as audit
from altera_api.classification_v2.nevo_v2_scale_baseline import (
    scale_baseline_report,
)
from altera_api.domain.enrichment import NutritionEnrichmentSource

_APPROVED_COLUMNS = [
    "product_id", "product_name", "manual_decision", "source",
    "effective_nevo_code", "effective_nevo_name",
    "effective_protein_g_per_100g", "review_priority", "suggested_action",
    "reviewer_notes",
]


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def list_enrichment_records_for_project(self, project_id):
        return self._records


def _v2(pid, **kw):
    base = dict(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal("7"),
        source_metadata={"provider": "voyage", "model": "voyage-4-lite"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _write_inputs(tmp_path, project_id, *, n=3):
    ids = [uuid4() for _ in range(n)]
    approved_path = tmp_path / f"nevo_v2_review_approved_candidates_{project_id}.csv"
    with approved_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_APPROVED_COLUMNS)
        w.writeheader()
        for i, pid in enumerate(ids):
            w.writerow(dict(
                product_id=str(pid), product_name=f"P{i}",
                manual_decision="approve", source="existing",
                effective_nevo_code="N1", effective_nevo_name="Food",
                effective_protein_g_per_100g="7", review_priority="P1",
                suggested_action="approve_auto_candidate", reviewer_notes="",
            ))
    plan_path = tmp_path / f"nevo_v2_apply_plan_{project_id}.json"
    plan_path.write_text(
        json.dumps({"project_id": project_id, "planned_operation_count": n}),
        encoding="utf-8",
    )
    return plan_path, approved_path, ids


def _run(tmp_path, project_id, plan, appr, store):
    return audit.main(
        ["--project-id", project_id, "--plan-json", str(plan),
         "--approved-candidates", str(appr), "--output-dir", str(tmp_path)],
        store=store, generated_at="2026-06-04T00:00:00+00:00",
    )


def _audit_json(tmp_path, project_id):
    return json.loads(
        (tmp_path / f"nevo_v2_apply_audit_{project_id}.json").read_text()
    )


# ---------------------------------------------------------------------------
# Part A/B — audit outcomes.
# ---------------------------------------------------------------------------
class TestAudit:
    def test_clean_apply_passes(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-ok")
        store = _FakeStore([_v2(i) for i in ids])
        rc = _run(tmp_path, "au-ok", plan, appr, store)
        assert rc == 0
        s = _audit_json(tmp_path, "au-ok")
        assert s["audit_status"] == "pass"
        assert s["recommendation"] == "pilot_apply_verified"
        assert s["applied_v2_count"] == 3
        assert s["matched_approved_count"] == 3
        assert s["plan_count_matches_applied"] is True
        # artifacts exist.
        assert (tmp_path / "nevo_v2_apply_audit_au-ok.csv").exists()
        assert (tmp_path / "nevo_v2_apply_audit_anomalies_au-ok.csv").exists()

    def test_missing_from_db_warns(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-miss")
        store = _FakeStore([_v2(ids[0]), _v2(ids[1])])  # 3rd not applied
        rc = _run(tmp_path, "au-miss", plan, appr, store)
        assert rc == 1
        s = _audit_json(tmp_path, "au-miss")
        assert s["audit_status"] == "warn"
        assert s["missing_from_db_count"] == 1
        assert s["recommendation"] == "investigate_anomalies"

    def test_unexpected_v2_fails(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-unx")
        store = _FakeStore([_v2(i) for i in ids] + [_v2(uuid4())])
        rc = _run(tmp_path, "au-unx", plan, appr, store)
        assert rc == 2
        s = _audit_json(tmp_path, "au-unx")
        assert s["audit_status"] == "fail"
        assert s["unexpected_v2_count"] == 1
        assert s["recommendation"] == "rollback_recommended"

    def test_duplicate_v2_fails(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-dup")
        store = _FakeStore([_v2(ids[0]), _v2(ids[0]), _v2(ids[1]), _v2(ids[2])])
        rc = _run(tmp_path, "au-dup", plan, appr, store)
        assert rc == 2
        s = _audit_json(tmp_path, "au-dup")
        assert s["duplicate_v2_count"] == 1
        assert s["audit_status"] == "fail"

    def test_missing_metadata_fails(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-meta")
        store = _FakeStore([_v2(ids[0], source_metadata=None), _v2(ids[1]),
                            _v2(ids[2])])
        rc = _run(tmp_path, "au-meta", plan, appr, store)
        assert rc == 2
        s = _audit_json(tmp_path, "au-meta")
        assert s["metadata_missing_count"] == 1
        assert s["audit_status"] == "fail"

    def test_wrong_fields_fail(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-bad")
        store = _FakeStore([
            _v2(ids[0], match_method="deterministic"),
            _v2(ids[1], nutrient="fat_pct"),
            _v2(ids[2], source=NutritionEnrichmentSource.CIQUAL, unit="mg"),
        ])
        rc = _run(tmp_path, "au-bad", plan, appr, store)
        assert rc == 2
        s = _audit_json(tmp_path, "au-bad")
        assert s["invalid_match_method_count"] == 1
        assert s["invalid_nutrient_count"] == 1
        assert s["invalid_source_count"] == 1
        assert s["invalid_unit_count"] == 1

    def test_manual_and_v1_conflicts_fail(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-conf")
        manual = SimpleNamespace(
            product_id=ids[0], nutrient="protein_pct", unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            match_method="manual", source_version=None,
            enriched_value=Decimal("9"), source_metadata=None,
        )
        v1 = SimpleNamespace(
            product_id=ids[1], nutrient="protein_pct", unit="g_per_100g",
            source=NutritionEnrichmentSource.NEVO, match_method="deterministic",
            source_version=None, enriched_value=Decimal("4"),
            source_metadata=None,
        )
        store = _FakeStore([_v2(i) for i in ids] + [manual, v1])
        rc = _run(tmp_path, "au-conf", plan, appr, store)
        assert rc == 2
        s = _audit_json(tmp_path, "au-conf")
        assert s["manual_conflict_count"] == 1
        assert s["v1_conflict_count"] == 1
        assert s["audit_status"] == "fail"


# ---------------------------------------------------------------------------
# Part C — 30k scale baseline.
# ---------------------------------------------------------------------------
class TestScaleBaseline:
    def test_baseline_report_shape(self) -> None:
        b = scale_baseline_report()
        assert b["target_row_count"] == 30_000
        assert b["status"] == "design_only"
        for key in ("deduplication_strategy", "canonical_product_key",
                    "batch_embedding_cache", "review_prioritization",
                    "no_match_feedback_loop", "apply_batching_strategy",
                    "monitoring_metrics", "next_artifacts"):
            assert key in b

    def test_audit_can_emit_baseline(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "au-bl")
        audit.main(
            ["--project-id", "au-bl", "--plan-json", str(plan),
             "--approved-candidates", str(appr), "--output-dir", str(tmp_path),
             "--write-scale-baseline"],
            store=_FakeStore([_v2(i) for i in ids]),
            generated_at="2026-06-04T00:00:00+00:00",
        )
        bl = tmp_path / "nevo_v2_30k_scale_baseline_au-bl.json"
        assert bl.exists()
        assert json.loads(bl.read_text())["target_row_count"] == 30_000


# ---------------------------------------------------------------------------
# Safety — read-only, no route imports, defaults unchanged.
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

    def test_routes_do_not_import_audit(self) -> None:
        api_dir = Path(audit.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "audit_nevo_v2_apply" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

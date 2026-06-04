"""Phase Quality-V2-T — NEVO V2 apply PLANNING (read-only, no DB writes).

Validator gains an explicit --project-id override; a new read-only plan
generator turns the validator outputs into apply-plan artifacts that document
exactly what a future DB-write phase would do and why it is still blocked by a
missing schema migration. No DB writes, no routes, no Supabase, V2 not active.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from altera_api.classification_v2 import plan_nevo_v2_apply as planner
from altera_api.classification_v2 import (
    validate_nevo_v2_review_package as validator,
)

_PKG_COLUMNS = [
    "review_bucket", "product_id", "product_name", "matcher_outcome",
    "nevo_code", "nevo_food_name", "enriched_protein_g_per_100g",
    "nutrition_safety_action", "manual_decision", "reviewer_notes",
    "approved_nevo_code", "approved_nevo_name", "approved_protein_g_per_100g",
    "review_priority", "suggested_action", "top_5_candidates",
]


def _pkg_row(**kw):
    base = {c: "" for c in _PKG_COLUMNS}
    base.update(
        product_id="1", product_name="X", matcher_outcome="match",
        nevo_code="N1", nevo_food_name="Food",
        enriched_protein_g_per_100g="5.0",
        nutrition_safety_action="would_enrich", review_priority="P1",
        suggested_action="approve_auto_candidate",
    )
    base.update(kw)
    return base


def _write_pkg(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_PKG_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ---------------------------------------------------------------------------
# Part A — validator --project-id override.
# ---------------------------------------------------------------------------
class TestProjectIdOverride:
    def test_explicit_project_id_used_in_filenames_and_summary(self, tmp_path) -> None:
        # Filename would infer a noisy id; --project-id overrides it.
        path = tmp_path / "nevo_v2_enrich_review_package_FILLED_SAMPLE_abc.csv"
        _write_pkg(path, [_pkg_row(manual_decision="approve")])
        rc = validator.main(
            ["--input", str(path), "--output-dir", str(tmp_path),
             "--project-id", "real-uuid"]
        )
        assert rc == 0
        summary_path = tmp_path / "nevo_v2_review_validation_summary_real-uuid.json"
        assert summary_path.exists()
        assert json.loads(summary_path.read_text())["project_id"] == "real-uuid"
        # the inferred-name artifact is NOT written.
        assert not (
            tmp_path
            / "nevo_v2_review_validation_summary_FILLED_SAMPLE_abc.json"
        ).exists()

    def test_inference_still_works_without_override(self, tmp_path) -> None:
        path = tmp_path / "nevo_v2_enrich_review_package_proj-9.csv"
        _write_pkg(path, [_pkg_row(manual_decision="approve")])
        validator.main(["--input", str(path), "--output-dir", str(tmp_path)])
        summary = json.loads(
            (tmp_path / "nevo_v2_review_validation_summary_proj-9.json").read_text()
        )
        assert summary["project_id"] == "proj-9"


# ---------------------------------------------------------------------------
# Helpers to produce a validated package for the planner.
# ---------------------------------------------------------------------------
def _validate(tmp_path, rows, pid="proj-t"):
    path = tmp_path / f"nevo_v2_enrich_review_package_{pid}.csv"
    _write_pkg(path, rows)
    validator.main(["--input", str(path), "--output-dir", str(tmp_path),
                    "--project-id", pid])
    return (
        tmp_path / f"nevo_v2_review_approved_candidates_{pid}.csv",
        tmp_path / f"nevo_v2_review_validation_summary_{pid}.json",
    )


# ---------------------------------------------------------------------------
# Part B/C/D — plan generation.
# ---------------------------------------------------------------------------
class TestPlanRefusals:
    def test_refuses_blocked_by_errors(self, tmp_path, capsys) -> None:
        # An approve on a no_match with no replacement → error → blocked.
        appr, summ = _validate(tmp_path, [
            _pkg_row(manual_decision="approve", matcher_outcome="no_match",
                     nevo_code="", nevo_food_name="",
                     enriched_protein_g_per_100g="",
                     nutrition_safety_action="skip_no_match",
                     suggested_action="review_no_match"),
        ], pid="blk")
        assert json.loads(summ.read_text())["recommendation"] == "blocked_by_errors"
        rc = planner.main(
            ["--approved-candidates", str(appr), "--validation-summary",
             str(summ), "--output-dir", str(tmp_path), "--project-id", "blk"],
            generated_at="t",
        )
        assert rc == 2
        assert "blocked_by_errors" in capsys.readouterr().out
        assert not (tmp_path / "nevo_v2_apply_plan_blk.json").exists()

    def test_refuses_review_incomplete_without_flag(self, tmp_path, capsys) -> None:
        appr, summ = _validate(tmp_path, [
            _pkg_row(manual_decision="approve"),
            _pkg_row(product_id="2", manual_decision=""),  # pending
        ], pid="inc")
        assert json.loads(summ.read_text())["recommendation"] == "review_incomplete"
        rc = planner.main(
            ["--approved-candidates", str(appr), "--validation-summary",
             str(summ), "--output-dir", str(tmp_path), "--project-id", "inc"],
            generated_at="t",
        )
        assert rc == 2
        assert "--allow-incomplete" in capsys.readouterr().out
        assert not (tmp_path / "nevo_v2_apply_plan_inc.json").exists()

    def test_writes_plan_with_allow_incomplete(self, tmp_path) -> None:
        appr, summ = _validate(tmp_path, [
            _pkg_row(manual_decision="approve"),
            _pkg_row(product_id="2", manual_decision=""),
        ], pid="inc2")
        rc = planner.main(
            ["--approved-candidates", str(appr), "--validation-summary",
             str(summ), "--output-dir", str(tmp_path), "--project-id", "inc2",
             "--allow-incomplete"],
            generated_at="t",
        )
        assert rc == 0
        plan = json.loads((tmp_path / "nevo_v2_apply_plan_inc2.json").read_text())
        assert plan["blocked_reason"] and "review_incomplete" in plan["blocked_reason"]
        assert plan["planned_operation_count"] == 1  # only the approved row

    def test_missing_summary_fails_clearly(self, tmp_path, capsys) -> None:
        appr, _ = _validate(tmp_path, [_pkg_row(manual_decision="approve")],
                            pid="m1")
        rc = planner.main(
            ["--approved-candidates", str(appr), "--validation-summary",
             str(tmp_path / "nope.json"), "--output-dir", str(tmp_path)],
            generated_at="t",
        )
        assert rc == 2
        assert "not found" in capsys.readouterr().out


class TestPlanArtifacts:
    def _ready_plan(self, tmp_path):
        appr, summ = _validate(tmp_path, [
            _pkg_row(product_id="1", product_name="Choc",
                     manual_decision="approve"),
            _pkg_row(product_id="2", product_name="Truc",
                     manual_decision="replace", matcher_outcome="no_match",
                     nevo_code="", nevo_food_name="",
                     enriched_protein_g_per_100g="", approved_nevo_code="N9",
                     approved_nevo_name="Picked", approved_protein_g_per_100g="8",
                     nutrition_safety_action="skip_no_match",
                     suggested_action="review_no_match"),
        ], pid="rdy")
        rc = planner.main(
            ["--approved-candidates", str(appr), "--validation-summary",
             str(summ), "--output-dir", str(tmp_path), "--project-id", "rdy"],
            generated_at="2026-06-04T00:00:00Z",
        )
        assert rc == 0
        return (
            json.loads((tmp_path / "nevo_v2_apply_plan_rdy.json").read_text()),
            tmp_path / "nevo_v2_apply_plan_rdy.csv",
        )

    def test_plan_json_fields_and_migration(self, tmp_path) -> None:
        plan, _ = self._ready_plan(tmp_path)
        assert plan["project_id"] == "rdy"
        assert plan["generated_at"] == "2026-06-04T00:00:00Z"
        assert plan["validation_recommendation"] == "ready_for_apply_planning"
        assert plan["planned_operation_count"] == 2
        assert plan["blocked_reason"] is None
        # Part D — migration requirements.
        assert plan["schema_migration_required"] is True
        assert "match_method" in plan["reason"]
        assert len(plan["recommended_options"]) >= 2
        assert "rollback_plan" in plan
        assert plan["db_apply_status"] == "blocked_pending_schema_migration"

    def test_operations_never_overwrite_and_need_migration(self, tmp_path) -> None:
        plan, csv_path = self._ready_plan(tmp_path)
        by_id = {o["product_id"]: o for o in plan["operations"]}
        for op in plan["operations"]:
            assert op["planned_operation"] == "create_v2_enrichment_record"
            assert op["requires_schema_migration"] is True
            assert op["overwrite_existing_v1"] is False
            assert op["overwrite_manual"] is False
            assert op["proposed_match_method"] == "v2_embeddings"
            assert op["proposed_source_tag"] == "nevo_v2_embeddings"
        # existing vs replacement source resolved correctly.
        assert by_id["1"]["source"] == "existing"
        assert by_id["1"]["approved_nevo_code"] == "N1"
        assert by_id["2"]["source"] == "replacement"
        assert by_id["2"]["approved_nevo_code"] == "N9"

        # CSV mirrors the plan with lowercase booleans.
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 2
        assert {r["requires_schema_migration"] for r in rows} == {"true"}
        assert {r["overwrite_existing_v1"] for r in rows} == {"false"}


# ---------------------------------------------------------------------------
# Safety — read-only, no DB, defaults unchanged, routes clean.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_planner_does_not_touch_db(self) -> None:
        src = Path(planner.__file__).read_text(encoding="utf-8")
        assert "get_store" not in src
        assert "store_factory" not in src
        assert "add_enrichment_record" not in src

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

    def test_routes_do_not_import_planner(self) -> None:
        api_dir = Path(planner.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "plan_nevo_v2_apply" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

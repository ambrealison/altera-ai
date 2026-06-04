"""Phase Quality-V2-X — apply-readiness checker + tiny-rehearsal --limit-apply.

Read-only readiness checker for the first real V2 apply, plus a --limit-apply
guard on the apply CLI for a tiny first rehearsal. No DB writes from the
checker; apply still double-gated. No routes; V1 default; embeddings off.
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
from altera_api.classification_v2 import apply_nevo_v2_plan as apply
from altera_api.classification_v2 import check_nevo_v2_apply_readiness as readiness
from altera_api.domain.enrichment import NutritionEnrichmentSource

_APPROVED_COLUMNS = [
    "product_id", "product_name", "manual_decision", "source",
    "effective_nevo_code", "effective_nevo_name",
    "effective_protein_g_per_100g", "review_priority", "suggested_action",
    "reviewer_notes",
]


class _FakeStore:
    def __init__(self, *, columns: bool = True, existing=None):
        self._columns = columns
        self._existing = existing or {}
        self.writes: list = []

    def has_enrichment_provenance_columns(self) -> bool:
        return self._columns

    def get_enrichment_records_for_product(self, pid):
        return self._existing.get(str(pid), [])

    def add_enrichment_record(self, record) -> None:
        self.writes.append(record)


def _existing(*, match_method="deterministic", source_version=None,
              enriched_value=Decimal("5.0"),
              source=NutritionEnrichmentSource.NEVO):
    return SimpleNamespace(
        nutrient="protein_pct", match_method=match_method,
        source_version=source_version, enriched_value=enriched_value,
        source=source,
    )


def _write_inputs(tmp_path, project_id, *, n=1):
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
    plan = {
        "project_id": project_id, "schema_migration_required": True,
        "db_apply_status": "blocked_pending_schema_migration",
        "overwrite_existing_v1": False, "overwrite_manual": False,
        "planned_operation_count": n,
        "validation_recommendation": "ready_for_apply_planning",
        "blocked_reason": None, "source_validation_summary": None,
        "operations": [{} for _ in range(n)],
    }
    plan_path = tmp_path / f"nevo_v2_apply_plan_{project_id}.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path, approved_path, ids


def _readiness_json(tmp_path, project_id):
    return json.loads(
        (tmp_path / f"nevo_v2_apply_readiness_{project_id}.json").read_text()
    )


def _checks(summary) -> dict[str, str]:
    return {c["name"]: c["status"] for c in summary["checks"]}


# ---------------------------------------------------------------------------
# Part A — readiness checker.
# ---------------------------------------------------------------------------
class TestReadinessChecker:
    def test_reports_columns_missing_not_ready(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "rk-m")
        rc = readiness.main(
            ["--project-id", "rk-m", "--plan-json", str(plan),
             "--approved-candidates", str(appr), "--output-dir", str(tmp_path)],
            store=_FakeStore(columns=False), generated_at="x",
        )
        assert rc == 1
        s = _readiness_json(tmp_path, "rk-m")
        assert s["ready"] is False
        assert s["provenance_columns_present"] is False
        assert _checks(s)["provenance_columns_present"] == "fail"
        # readiness CSV lists the checks.
        rows = list(csv.DictReader(
            (tmp_path / "nevo_v2_apply_readiness_rk-m.csv").open()))
        assert any(r["name"] == "provenance_columns_present" for r in rows)

    def test_reports_ready_when_columns_present(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "rk-r")
        rc = readiness.main(
            ["--project-id", "rk-r", "--plan-json", str(plan),
             "--approved-candidates", str(appr), "--output-dir", str(tmp_path)],
            store=_FakeStore(columns=True), generated_at="x",
        )
        assert rc == 0
        s = _readiness_json(tmp_path, "rk-r")
        assert s["ready"] is True
        statuses = _checks(s)
        assert statuses["provenance_columns_present"] == "pass"
        assert statuses["v1_default_unchanged"] == "pass"
        assert statuses["routes_clean"] == "pass"

    def test_detects_existing_conflicts(self, tmp_path) -> None:
        plan, appr, ids = _write_inputs(tmp_path, "rk-c", n=3)
        existing = {
            str(ids[0]): [_existing()],  # V1
            str(ids[1]): [_existing(match_method="manual",
                                    source=NutritionEnrichmentSource.MANUAL_ALTERA)],
            str(ids[2]): [_existing(match_method="ai_assisted",
                                    source_version="v2_embeddings")],
        }
        readiness.main(
            ["--project-id", "rk-c", "--plan-json", str(plan),
             "--approved-candidates", str(appr), "--output-dir", str(tmp_path)],
            store=_FakeStore(existing=existing), generated_at="x",
        )
        s = _readiness_json(tmp_path, "rk-c")
        assert s["conflicts"] == {
            "writable": 0, "existing_manual": 1, "existing_v1": 1,
            "existing_v2": 1, "error": 0,
        }
        # still "ready" — conflicts are informational (they will be skipped).
        assert s["ready"] is True

    def test_project_mismatch_fails_check(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "rk-p")
        rc = readiness.main(
            ["--project-id", "WRONG", "--plan-json", str(plan),
             "--approved-candidates", str(appr), "--output-dir", str(tmp_path)],
            store=_FakeStore(), generated_at="x",
        )
        assert rc == 1
        s = _readiness_json(tmp_path, "WRONG")
        assert _checks(s)["plan_project_matches"] == "fail"
        assert s["ready"] is False


# ---------------------------------------------------------------------------
# Part C — --limit-apply.
# ---------------------------------------------------------------------------
class TestLimitApply:
    def test_limit_caps_dry_run_planned(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "lim-d", n=5)
        rc = apply.main(
            ["--plan-json", str(plan), "--approved-candidates", str(appr),
             "--project-id", "lim-d", "--output-dir", str(tmp_path),
             "--limit-apply", "2"],
            store=_FakeStore(), generated_at="2026-06-04T00:00:00+00:00",
        )
        assert rc == 0
        s = json.loads(
            (tmp_path / "nevo_v2_apply_result_lim-d.json").read_text())
        assert s["limit_apply"] == 2
        assert s["total_planned"] == 2
        assert s["would_write_count"] == 2

    def test_limit_caps_confirmed_writes(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "lim-c", n=5)
        store = _FakeStore()
        rc = apply.main(
            ["--plan-json", str(plan), "--approved-candidates", str(appr),
             "--project-id", "lim-c", "--output-dir", str(tmp_path),
             "--limit-apply", "2", "--confirm-apply-v2"],
            store=store, generated_at="2026-06-04T00:00:00+00:00",
        )
        assert rc == 0
        assert len(store.writes) == 2
        s = json.loads(
            (tmp_path / "nevo_v2_apply_result_lim-c.json").read_text())
        assert s["written_count"] == 2 and s["total_planned"] == 2

    def test_no_limit_processes_all(self, tmp_path) -> None:
        plan, appr, _ = _write_inputs(tmp_path, "lim-n", n=4)
        apply.main(
            ["--plan-json", str(plan), "--approved-candidates", str(appr),
             "--project-id", "lim-n", "--output-dir", str(tmp_path)],
            store=_FakeStore(), generated_at="2026-06-04T00:00:00+00:00",
        )
        s = json.loads(
            (tmp_path / "nevo_v2_apply_result_lim-n.json").read_text())
        assert s["limit_apply"] is None
        assert s["total_planned"] == 4


# ---------------------------------------------------------------------------
# Safety — read-only checker, no route imports, defaults unchanged.
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

    def test_routes_do_not_import_v2_or_apply(self) -> None:
        api_dir = Path(readiness.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "apply_nevo_v2_plan" in p.read_text(encoding="utf-8")
            or "check_nevo_v2_apply_readiness" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_checker_does_not_write_db(self) -> None:
        src = Path(readiness.__file__).read_text(encoding="utf-8")
        assert "add_enrichment_record" not in src
        assert ".insert(" not in src

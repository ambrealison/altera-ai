"""Phase Quality-V2-W — explicit, guarded NEVO V2 apply CLI.

The only path that may persist V2-tagged enrichment records: default dry-run,
real write requires --confirm-apply-v2 AND the 0037 provenance columns, never
overwrites manual/V1, never re-writes an existing V2 record. Not imported by any
route; V1 default; embeddings off.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

# Load api package first to avoid the persistence<->api import cycle when the
# apply CLI imports the store factory lazily.
import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import apply_nevo_v2_plan as apply
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


def _write_inputs(
    tmp_path, project_id, *, approved_rows, planned=None,
    recommendation="ready_for_apply_planning", blocked_reason=None,
    overwrite=False, schema=True,
    db_status="blocked_pending_schema_migration",
    source_validation_summary=None,
):
    approved_path = tmp_path / f"nevo_v2_review_approved_candidates_{project_id}.csv"
    with approved_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_APPROVED_COLUMNS)
        w.writeheader()
        for r in approved_rows:
            w.writerow({c: r.get(c, "") for c in _APPROVED_COLUMNS})
    plan = {
        "project_id": project_id,
        "schema_migration_required": schema,
        "db_apply_status": db_status,
        "overwrite_existing_v1": overwrite,
        "overwrite_manual": overwrite,
        "planned_operation_count": (
            len(approved_rows) if planned is None else planned
        ),
        "validation_recommendation": recommendation,
        "blocked_reason": blocked_reason,
        "source_validation_summary": source_validation_summary,
        "operations": [{} for _ in approved_rows],
    }
    plan_path = tmp_path / f"nevo_v2_apply_plan_{project_id}.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path, approved_path


def _approved(pid, **kw):
    base = dict(
        product_id=str(pid), product_name="Choc", manual_decision="approve",
        source="existing", effective_nevo_code="N1",
        effective_nevo_name="Chocolate dark", effective_protein_g_per_100g="7",
        review_priority="P1", suggested_action="approve_auto_candidate",
        reviewer_notes="",
    )
    base.update(kw)
    return base


def _run(plan, approved, tmp_path, project_id, *args, store, gen="2026-06-04T00:00:00+00:00"):
    return apply.main(
        ["--plan-json", str(plan), "--approved-candidates", str(approved),
         "--project-id", project_id, "--output-dir", str(tmp_path), *args],
        store=store, generated_at=gen,
    )


def _result(tmp_path, project_id):
    return json.loads(
        (tmp_path / f"nevo_v2_apply_result_{project_id}.json").read_text()
    )


# ---------------------------------------------------------------------------
# Part D — impossible to write by accident.
# ---------------------------------------------------------------------------
class TestGuards:
    def test_default_is_dry_run_writes_nothing(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-d", approved_rows=[_approved(pid)])
        store = _FakeStore()
        rc = _run(plan, appr, tmp_path, "proj-d", store=store)
        assert rc == 0
        assert store.writes == []
        s = _result(tmp_path, "proj-d")
        assert s["dry_run"] is True
        assert s["written_count"] == 0
        assert s["would_write_count"] == 1
        assert s["confirmation_present"] is False

    def test_confirm_but_missing_columns_writes_nothing(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-m", approved_rows=[_approved(pid)])
        store = _FakeStore(columns=False)
        rc = _run(plan, appr, tmp_path, "proj-m", "--confirm-apply-v2", store=store)
        assert rc == 2
        assert store.writes == []
        s = _result(tmp_path, "proj-m")
        assert s["provenance_columns_present"] is False
        assert s["written_count"] == 0
        assert s["blocked_reason"] and "migration 0037" in s["blocked_reason"]

    def test_project_mismatch_refuses_no_artifacts(self, tmp_path, capsys) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-x", approved_rows=[_approved(pid)])
        store = _FakeStore()
        rc = _run(plan, appr, tmp_path, "WRONG", "--confirm-apply-v2", store=store)
        assert rc == 2
        assert store.writes == []
        assert "project_id" in capsys.readouterr().out
        assert not (tmp_path / "nevo_v2_apply_result_WRONG.json").exists()

    def test_count_mismatch_refuses(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(
            tmp_path, "proj-c", approved_rows=[_approved(pid)], planned=5
        )
        store = _FakeStore()
        rc = _run(plan, appr, tmp_path, "proj-c", "--confirm-apply-v2", store=store)
        assert rc == 2
        assert store.writes == []

    def test_overwrite_flag_in_plan_refuses(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(
            tmp_path, "proj-o", approved_rows=[_approved(pid)], overwrite=True
        )
        rc = _run(plan, appr, tmp_path, "proj-o", "--confirm-apply-v2",
                  store=_FakeStore())
        assert rc == 2


class TestIncompletePlan:
    def test_review_incomplete_refuses_without_flag(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(
            tmp_path, "proj-i", approved_rows=[_approved(pid)],
            recommendation="review_incomplete",
            blocked_reason="review_incomplete: planning only the apply-ready rows",
        )
        rc = _run(plan, appr, tmp_path, "proj-i", "--confirm-apply-v2",
                  store=_FakeStore())
        assert rc == 2

    def test_review_incomplete_allowed_with_flag(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(
            tmp_path, "proj-i2", approved_rows=[_approved(pid)],
            recommendation="review_incomplete",
            blocked_reason="review_incomplete: planning only the apply-ready rows",
        )
        store = _FakeStore()
        rc = _run(plan, appr, tmp_path, "proj-i2", "--allow-incomplete-apply",
                  store=store)
        assert rc == 0  # dry-run (no --confirm) → ok
        assert store.writes == []


# ---------------------------------------------------------------------------
# Part B — write behavior + skip rules.
# ---------------------------------------------------------------------------
class TestWriteBehavior:
    def test_confirmed_write_constructs_v2_record(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-w", approved_rows=[_approved(pid)])
        store = _FakeStore()
        rc = _run(plan, appr, tmp_path, "proj-w", "--confirm-apply-v2",
                  "--embedding-provider", "voyage", "--embedding-model",
                  "voyage-4-lite", "--top-k", "20", store=store)
        assert rc == 0
        assert len(store.writes) == 1
        rec = store.writes[0]
        assert rec.source is NutritionEnrichmentSource.NEVO
        assert rec.match_method == "ai_assisted"
        assert rec.source_version == "v2_embeddings"
        assert rec.enriched_value == Decimal("7")
        assert rec.source_metadata["applied_by_cli"] is True
        assert rec.source_metadata["embedding_model"] == "voyage-4-lite"
        assert rec.source_metadata["top_k"] == 20
        assert rec.source_metadata["candidate_source"] == "existing"
        s = _result(tmp_path, "proj-w")
        assert s["written_count"] == 1 and s["dry_run"] is False

    def test_skips_existing_v1(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-v1", approved_rows=[_approved(pid)])
        store = _FakeStore(existing={str(pid): [_existing()]})
        rc = _run(plan, appr, tmp_path, "proj-v1", "--confirm-apply-v2", store=store)
        assert rc == 0
        assert store.writes == []
        s = _result(tmp_path, "proj-v1")
        assert s["skipped_v1_count"] == 1
        assert s["results"][0]["status"] == "skipped_existing_v1"

    def test_skips_existing_manual(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-mn", approved_rows=[_approved(pid)])
        store = _FakeStore(existing={
            str(pid): [_existing(match_method="manual",
                                 source=NutritionEnrichmentSource.MANUAL_ALTERA)]
        })
        assert _run(plan, appr, tmp_path, "proj-mn", "--confirm-apply-v2",
                    store=store) == 0
        assert store.writes == []
        s = _result(tmp_path, "proj-mn")
        assert s["skipped_manual_count"] == 1
        assert s["results"][0]["status"] == "skipped_existing_manual"

    def test_skips_existing_v2(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-v2", approved_rows=[_approved(pid)])
        store = _FakeStore(existing={
            str(pid): [_existing(match_method="ai_assisted",
                                 source_version="v2_embeddings")]
        })
        assert _run(plan, appr, tmp_path, "proj-v2", "--confirm-apply-v2",
                    store=store) == 0
        assert store.writes == []
        s = _result(tmp_path, "proj-v2")
        assert s["skipped_existing_count"] == 1
        assert s["results"][0]["status"] == "skipped_existing_v2"

    def test_artifacts_written_with_per_row_status(self, tmp_path) -> None:
        pid = uuid4()
        plan, appr = _write_inputs(tmp_path, "proj-a", approved_rows=[_approved(pid)])
        _run(plan, appr, tmp_path, "proj-a", store=_FakeStore())
        assert (tmp_path / "nevo_v2_apply_result_proj-a.json").exists()
        csv_path = tmp_path / "nevo_v2_apply_result_proj-a.csv"
        rows = list(csv.DictReader(csv_path.open()))
        assert rows[0]["status"] == "would_write"
        assert "effective_nevo_code" in rows[0]


# ---------------------------------------------------------------------------
# Safety — read-only by default, no route imports, defaults unchanged.
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

    def test_routes_do_not_import_apply_cli(self) -> None:
        api_dir = Path(apply.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "apply_nevo_v2_plan" in p.read_text(encoding="utf-8")
            or "classification_v2" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

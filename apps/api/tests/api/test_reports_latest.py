"""``GET /projects/{id}/reports/latest`` — latest PT + WWF report docs.

Runs are per-methodology, so the Result step needs both the latest Protein
Tracker report and the latest WWF report to show both. This endpoint returns
them as two separate documents (never merged metrics).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.domain.common import Methodology
from tests.api.test_phase21_report import _pt_summary, _wwf_summary


def _make_project(store: InMemoryStore, methodologies: set[Methodology]):
    return store.create_project(
        name="reports-latest",
        methodologies_enabled=frozenset(methodologies),
        reporting_period_label="FY 2024",
        organisation_id=store.default_org_id,
        created_by=store.default_user_id,
    )


def _add_run(store: InMemoryStore, project_id, methodology: Methodology):
    rid = uuid4()
    payload = (
        _pt_summary(rid)
        if methodology is Methodology.PROTEIN_TRACKER
        else _wwf_summary(rid)
    )
    now = datetime.now(UTC)
    store.add_run(
        RunRecord(
            id=rid,
            project_id=project_id,
            methodology=methodology,
            started_at=now,
            finished_at=now,
            triggered_by=store.default_user_id,
            summary_payload=payload,
            rows_count=10,
            organisation_id=store.default_org_id,
        )
    )
    return rid


class TestLatestReports:
    def test_both_runs_returns_both_documents(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        proj = _make_project(
            store, {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        )
        _add_run(store, proj.id, Methodology.PROTEIN_TRACKER)
        _add_run(store, proj.id, Methodology.WWF)

        r = client.get(f"/api/v1/projects/{proj.id}/reports/latest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["protein_tracker"] is not None
        assert body["wwf"] is not None
        # Each doc carries only its own methodology's section.
        assert body["protein_tracker"]["pt_section"] is not None
        assert body["wwf"]["wwf_section"] is not None

    def test_pt_only_project_has_null_wwf(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        proj = _make_project(store, {Methodology.PROTEIN_TRACKER})
        _add_run(store, proj.id, Methodology.PROTEIN_TRACKER)
        body = client.get(
            f"/api/v1/projects/{proj.id}/reports/latest"
        ).json()
        assert body["protein_tracker"] is not None
        assert body["wwf"] is None

    def test_wwf_only_project_has_null_pt(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        proj = _make_project(store, {Methodology.WWF})
        _add_run(store, proj.id, Methodology.WWF)
        body = client.get(
            f"/api/v1/projects/{proj.id}/reports/latest"
        ).json()
        assert body["wwf"] is not None
        assert body["protein_tracker"] is None

    def test_no_runs_returns_both_null(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        proj = _make_project(
            store, {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        )
        body = client.get(
            f"/api/v1/projects/{proj.id}/reports/latest"
        ).json()
        assert body["protein_tracker"] is None
        assert body["wwf"] is None

    def test_multiple_runs_per_methodology_ok(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        proj = _make_project(
            store, {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        )
        _add_run(store, proj.id, Methodology.PROTEIN_TRACKER)
        _add_run(store, proj.id, Methodology.WWF)
        _add_run(store, proj.id, Methodology.WWF)  # a second WWF run
        r = client.get(f"/api/v1/projects/{proj.id}/reports/latest")
        assert r.status_code == 200
        body = r.json()
        assert body["protein_tracker"] is not None
        assert body["wwf"] is not None

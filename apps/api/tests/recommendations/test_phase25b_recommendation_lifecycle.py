"""Phase 25B — recommendation lifecycle and persistence tests.

Covers:
- Generate + persist via POST /recommendations/generate
- Generated recs start as 'draft'
- Persisted recs used in report (not engine fallback)
- Client cannot see draft; can see proposed/accepted
- Altera can see draft
- Only authorised roles can propose/dismiss/archive/accept
- Cross-org access blocked
- Status transitions correct
- No forbidden commercial fields in any response
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.api.store_factory import get_store
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
)
from altera_api.main import app

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _dev_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def http(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(authed_user, None)


def _make_altera_org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="Altera AI",
        slug="altera",
        created_at=datetime.now(UTC),
        organisation_type=OrganisationType.ALTERA_INTERNAL,
    )
    store.organisations[org.id] = org
    return org


def _make_client_org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="GMS Client",
        slug="gms",
        created_at=datetime.now(UTC),
        organisation_type=OrganisationType.GMS_CLIENT,
    )
    store.organisations[org.id] = org
    return org


def _make_user(store: InMemoryStore, org: Organisation, role: AlteraRole | ClientRole) -> UserProfile:
    uid = uuid4()
    profile = UserProfile(
        user_id=uid,
        organisation_id=org.id,
        email=f"{uid}@test.local",
        display_name=str(uid),
        role=role,
        created_at=datetime.now(UTC),
    )
    store.users[uid] = profile
    return profile


def _auth_ctx(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id,
        email=user.email,
        organisation_id=org.id,
        role=user.role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=org.organisation_type,
    )


def _make_project(store: InMemoryStore, org: Organisation) -> Project:
    return store.create_project(
        name="Test Project",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )


def _make_run(store: InMemoryStore, project: Project) -> RunRecord:
    per_group = tuple(
        ProteinTrackerGroupAggregate(
            pt_group=g,
            item_count=1,
            volume_kg=Decimal("10.0"),
            protein_kg=Decimal("5.0"),
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    )
    summary = ProteinTrackerCalculationSummary(
        run_id=uuid4(),
        methodology_version="1.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0",
        rules_version="1.0",
        reporting_period_label="2024",
        plant_protein_kg=Decimal("10.00"),
        animal_protein_kg=Decimal("90.00"),
        total_in_scope_protein_kg=Decimal("100.00"),
        plant_share_pct=Decimal("10.00"),  # < 40% → triggers increase_plant_core_share
        animal_share_pct=Decimal("90.00"),
        out_of_scope_count=0,
        unknown_count=0,
        rows_with_per_product_split=0,
        rows_protein_source_label=5,
        rows_protein_source_reference_db=5,
        per_group=per_group,
    )
    run = RunRecord(
        id=uuid4(),
        project_id=project.id,
        methodology=Methodology.PROTEIN_TRACKER,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        triggered_by=project.created_by,
        summary_payload=summary.model_dump(),
        rows_payload=[],
        organisation_id=project.organisation_id,
    )
    store.runs[run.id] = run
    return run


# ---------------------------------------------------------------------------
# Tests: generate endpoint
# ---------------------------------------------------------------------------


def test_generate_creates_draft_recommendations(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for rec in data:
            assert rec["status"] == "draft"
            assert rec["id"] is not None
            assert rec["run_id"] == str(run.id)
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_generate_requires_altera(http: TestClient, store: InMemoryStore) -> None:
    # Project and run are in the client's own org so auth doesn't 404 first.
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: list endpoint visibility
# ---------------------------------------------------------------------------


def test_altera_sees_draft_recommendations(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        list_resp = http.get(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations")
        assert list_resp.status_code == 200
        data = list_resp.json()
        statuses = {r["status"] for r in data}
        assert "draft" in statuses
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_client_cannot_see_draft(http: TestClient, store: InMemoryStore) -> None:
    # Project in client_org so client can see it; Altera accesses cross-org.
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)
    run = _make_run(store, project)

    # Generate as Altera (all draft)
    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")

    # List as client — should be empty because all are draft
    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        list_resp = http.get(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations")
        assert list_resp.status_code == 200
        assert list_resp.json() == []
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_client_sees_proposed_and_accepted(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)  # in client org
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
    rec_id = gen_resp.json()[0]["id"]

    http.post(f"/api/v1/recommendations/{rec_id}/propose")

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        list_resp = http.get(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert len(data) == 1
        assert data[0]["id"] == rec_id
        assert data[0]["status"] == "proposed"
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: lifecycle transitions
# ---------------------------------------------------------------------------


def test_propose_transitions_draft_to_proposed(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        rec_id = gen_resp.json()[0]["id"]
        resp = http.post(f"/api/v1/recommendations/{rec_id}/propose")
        assert resp.status_code == 200
        assert resp.json()["status"] == "proposed"
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_accept_transitions_proposed_to_accepted(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        rec_id = gen_resp.json()[0]["id"]
        http.post(f"/api/v1/recommendations/{rec_id}/propose")
        resp = http.post(f"/api/v1/recommendations/{rec_id}/accept")
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_dismiss_sets_dismissed(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        rec_id = gen_resp.json()[0]["id"]
        resp = http.post(f"/api/v1/recommendations/{rec_id}/dismiss")
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_archive_sets_archived(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        rec_id = gen_resp.json()[0]["id"]
        resp = http.post(f"/api/v1/recommendations/{rec_id}/archive")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: role gating
# ---------------------------------------------------------------------------


def test_analyst_cannot_propose(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    analyst = _make_user(store, altera_org, AlteraRole.ALTERA_ANALYST)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
    rec_id = gen_resp.json()[0]["id"]

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(analyst, altera_org)
    try:
        resp = http.post(f"/api/v1/recommendations/{rec_id}/propose")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_client_cannot_manage_recommendations(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)  # in client org so Altera can generate
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
    rec_id = gen_resp.json()[0]["id"]

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        assert http.post(f"/api/v1/recommendations/{rec_id}/propose").status_code == 403
        assert http.post(f"/api/v1/recommendations/{rec_id}/dismiss").status_code == 403
        assert http.post(f"/api/v1/recommendations/{rec_id}/archive").status_code == 403
        assert http.post(f"/api/v1/recommendations/{rec_id}/accept").status_code == 403
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: upsert status preservation
# ---------------------------------------------------------------------------


def test_regenerate_preserves_proposed_status(http: TestClient, store: InMemoryStore) -> None:
    """Re-running generate does not downgrade a proposed recommendation to draft."""
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen1 = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        first_rec = gen1.json()[0]
        rec_id = first_rec["id"]
        action_type = first_rec["action_type"]

        http.post(f"/api/v1/recommendations/{rec_id}/propose")

        gen2 = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        recs_after = gen2.json()
        same_rec = next((r for r in recs_after if r["action_type"] == action_type), None)
        assert same_rec is not None
        assert same_rec["status"] == "proposed"  # must be preserved
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: report integration
# ---------------------------------------------------------------------------


def test_report_uses_persisted_recs_with_ids(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        report_resp = http.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        assert report_resp.status_code == 200
        recs = report_resp.json()["recommendations"]
        assert len(recs) > 0
        assert all(r["id"] is not None for r in recs)
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests: no commercial fields
# ---------------------------------------------------------------------------

_FORBIDDEN = {"revenue", "margin", "cost_price", "contract_terms", "confidential"}


def test_no_commercial_fields_in_recommendation_response(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        gen_resp = http.post(f"/api/v1/projects/{project.id}/runs/{run.id}/recommendations/generate")
        for rec in gen_resp.json():
            text = " ".join([
                rec.get("title", ""),
                rec.get("description", ""),
                rec.get("rationale", ""),
                " ".join(rec.get("evidence", [])),
                " ".join(rec.get("caveats", [])),
            ]).lower()
            for word in _FORBIDDEN:
                assert word not in text, f"Forbidden word '{word}' found in recommendation: {rec}"
    finally:
        app.dependency_overrides.pop(authed_user, None)

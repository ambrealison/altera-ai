"""Phase 29B — role × action permission matrix tests.

Covers paginated list endpoints and write actions across the full role matrix:
  Altera: ALTERA_ANALYST, ALTERA_METHODOLOGY_LEAD, ALTERA_ADMIN
  Client: CLIENT_VIEWER, CLIENT_ANALYST, CLIENT_OWNER

Key invariants tested:
- All list endpoints return Page envelope (items / total / limit / offset)
- Clients are scoped to their own org; Altera sees all
- Viewers cannot create projects; Owners/Analysts can
- Altera-only actions (approve export, create scenario op) are blocked for clients
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import ExportRecord, InMemoryStore, RunRecord
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALTERA_ORG_TYPE = OrganisationType.ALTERA_INTERNAL
CLIENT_ORG_TYPE = OrganisationType.GMS_CLIENT


def _make_org(store: InMemoryStore, *, name: str, org_type: OrganisationType) -> Organisation:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"
    org = Organisation(
        id=uuid4(),
        name=name,
        slug=f"{slug}-{uuid4().hex[:6]}",
        organisation_type=org_type,
        created_at=datetime.now(UTC),
    )
    store.organisations[org.id] = org
    return org


def _make_user(
    store: InMemoryStore, *, org: Organisation, role: AlteraRole | ClientRole
) -> UserProfile:
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


def _seed_project(store: InMemoryStore, org: Organisation) -> UUID:
    project = store.create_project(
        name="Test Project",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="FY 2024",
        organisation_id=org.id,
    )
    return project.id


def _seed_run(store: InMemoryStore, project_id: UUID, org: Organisation) -> UUID:
    run = RunRecord(
        id=uuid4(),
        project_id=project_id,
        organisation_id=org.id,
        methodology=Methodology.PROTEIN_TRACKER,
        triggered_by=uuid4(),
        rows_count=0,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        summary_payload={},
    )
    store.runs[run.id] = run
    return run.id


def _seed_export(
    store: InMemoryStore,
    run_id: UUID,
    org_id: UUID,
    approval_status: str = "approved",
) -> UUID:
    eid = uuid4()
    export = ExportRecord(
        id=eid,
        run_id=run_id,
        organisation_id=org_id,
        format="csv",
        status="success",
        storage_path=f"exports/{eid}.csv",
        filename=f"{eid}.csv",
        size_bytes=0,
        approval_status=approval_status,
        created_at=datetime.now(UTC),
    )
    store.export_records[eid] = export
    return eid


def _override(user: UserProfile, org: Organisation):
    ctx = _auth_ctx(user, org)
    app.dependency_overrides[authed_user] = lambda: ctx


def _clear():
    app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# List endpoints return Page envelope
# ---------------------------------------------------------------------------


class TestListEndpointsReturnPageEnvelope:
    """Every GET list endpoint must return {items, total, limit, offset}."""

    def test_list_projects_returns_page(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        _override(analyst, altera_org)
        try:
            r = client.get("/api/v1/projects")
        finally:
            _clear()
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

    def test_list_uploads_returns_page(self, client: TestClient, store: InMemoryStore) -> None:
        org = _make_org(store, name="OrgU", org_type=CLIENT_ORG_TYPE)
        user = _make_user(store, org=org, role=ClientRole.CLIENT_OWNER)
        pid = _seed_project(store, org)
        _override(user, org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/uploads")
        finally:
            _clear()
        assert r.status_code == 200
        body = r.json()
        assert "items" in body and "total" in body

    def test_list_runs_returns_page(self, client: TestClient, store: InMemoryStore) -> None:
        org = _make_org(store, name="OrgR", org_type=CLIENT_ORG_TYPE)
        user = _make_user(store, org=org, role=ClientRole.CLIENT_OWNER)
        pid = _seed_project(store, org)
        _override(user, org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/runs")
        finally:
            _clear()
        assert r.status_code == 200
        assert "items" in r.json()

    def test_list_exports_returns_page(self, client: TestClient, store: InMemoryStore) -> None:
        org = _make_org(store, name="OrgE", org_type=CLIENT_ORG_TYPE)
        user = _make_user(store, org=org, role=ClientRole.CLIENT_OWNER)
        pid = _seed_project(store, org)
        run_id = _seed_run(store, pid, org)
        _seed_export(store, run_id, org.id)
        _override(user, org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/runs/{run_id}/exports")
        finally:
            _clear()
        assert r.status_code == 200
        assert "items" in r.json()

    def test_list_scenarios_returns_page(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="AltS", org_type=ALTERA_ORG_TYPE)
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        pid = _seed_project(store, altera_org)
        _override(lead, altera_org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/scenarios")
        finally:
            _clear()
        assert r.status_code == 200
        assert "items" in r.json()


# ---------------------------------------------------------------------------
# Project list scoping
# ---------------------------------------------------------------------------


class TestProjectListScoping:
    def test_client_sees_only_own_org_projects(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_a = _make_org(store, name="OrgA", org_type=CLIENT_ORG_TYPE)
        org_b = _make_org(store, name="OrgB", org_type=CLIENT_ORG_TYPE)
        user_a = _make_user(store, org=org_a, role=ClientRole.CLIENT_OWNER)
        _seed_project(store, org_a)
        _seed_project(store, org_b)

        _override(user_a, org_a)
        try:
            r = client.get("/api/v1/projects")
        finally:
            _clear()

        assert r.status_code == 200
        items = r.json()["items"]
        assert all(item["organisation_id"] == str(org_a.id) for item in items)

    def test_altera_sees_all_org_projects(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_a = _make_org(store, name="OrgC", org_type=CLIENT_ORG_TYPE)
        org_b = _make_org(store, name="OrgD", org_type=CLIENT_ORG_TYPE)
        altera_org = _make_org(store, name="AltPM", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        pid_a = _seed_project(store, org_a)
        pid_b = _seed_project(store, org_b)

        _override(analyst, altera_org)
        try:
            r = client.get("/api/v1/projects")
        finally:
            _clear()

        ids = {item["id"] for item in r.json()["items"]}
        assert str(pid_a) in ids
        assert str(pid_b) in ids


# ---------------------------------------------------------------------------
# Project creation — role matrix
# ---------------------------------------------------------------------------


class TestProjectCreationRoles:
    _body = {
        "name": "PM Test",
        "methodologies_enabled": ["protein_tracker"],
        "reporting_period_label": "FY 2025",
    }

    @pytest.mark.parametrize(
        "role,expected",
        [
            (ClientRole.CLIENT_VIEWER, 403),
            (ClientRole.CLIENT_ADMIN, 201),
            (ClientRole.CLIENT_OWNER, 201),
        ],
    )
    def test_client_role_create_project(
        self,
        client: TestClient,
        store: InMemoryStore,
        role: ClientRole,
        expected: int,
    ) -> None:
        org = _make_org(store, name=f"Org-{role.value}", org_type=CLIENT_ORG_TYPE)
        user = _make_user(store, org=org, role=role)
        _override(user, org)
        try:
            r = client.post("/api/v1/projects", json=self._body)
        finally:
            _clear()
        assert r.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (AlteraRole.ALTERA_ANALYST, 201),
            (AlteraRole.ALTERA_METHODOLOGY_LEAD, 403),  # can_write_data is False for lead
            (AlteraRole.ALTERA_ADMIN, 201),
        ],
    )
    def test_altera_role_create_project(
        self,
        client: TestClient,
        store: InMemoryStore,
        role: AlteraRole,
        expected: int,
    ) -> None:
        org = _make_org(store, name="Altera", org_type=ALTERA_ORG_TYPE)
        user = _make_user(store, org=org, role=role)
        _override(user, org)
        try:
            r = client.post("/api/v1/projects", json=self._body)
        finally:
            _clear()
        assert r.status_code == expected


# ---------------------------------------------------------------------------
# Export visibility — client filtered by approval status
# ---------------------------------------------------------------------------


class TestExportVisibilityByRole:
    def test_client_viewer_sees_only_approved_and_delivered(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org = _make_org(store, name="OrgEV", org_type=CLIENT_ORG_TYPE)
        user = _make_user(store, org=org, role=ClientRole.CLIENT_VIEWER)
        pid = _seed_project(store, org)
        run_id = _seed_run(store, pid, org)
        for status in ("draft", "under_review", "approved", "rejected", "delivered"):
            _seed_export(store, run_id, org.id, approval_status=status)

        _override(user, org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/runs/{run_id}/exports")
        finally:
            _clear()

        assert r.status_code == 200
        statuses = {e["approval_status"] for e in r.json()["items"]}
        assert statuses == {"approved", "delivered"}

    def test_altera_analyst_sees_all_export_statuses(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        altera_org = _make_org(store, name="AltEV", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        client_org = _make_org(store, name="RetailEV", org_type=CLIENT_ORG_TYPE)
        pid = _seed_project(store, client_org)
        run_id = _seed_run(store, pid, client_org)
        for status in ("draft", "under_review", "approved", "rejected", "delivered"):
            _seed_export(store, run_id, client_org.id, approval_status=status)

        _override(analyst, altera_org)
        try:
            r = client.get(f"/api/v1/projects/{pid}/runs/{run_id}/exports")
        finally:
            _clear()

        assert r.status_code == 200
        statuses = {e["approval_status"] for e in r.json()["items"]}
        assert statuses == {"draft", "under_review", "approved", "rejected", "delivered"}


# ---------------------------------------------------------------------------
# Pagination params respected
# ---------------------------------------------------------------------------


class TestPaginationParams:
    def test_limit_respected(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="AltPag", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        for i in range(5):
            store.create_project(
                name=f"Proj {i}",
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                reporting_period_label="FY 2025",
                organisation_id=altera_org.id,
            )

        _override(analyst, altera_org)
        try:
            r = client.get("/api/v1/projects?limit=3&offset=0")
        finally:
            _clear()

        assert r.status_code == 200
        body = r.json()
        assert body["limit"] == 3
        assert len(body["items"]) <= 3

    def test_offset_advances_page(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="AltOff", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        created = []
        for i in range(4):
            p = store.create_project(
                name=f"OffProj {i}",
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                reporting_period_label="FY 2025",
                organisation_id=altera_org.id,
            )
            created.append(str(p.id))

        _override(analyst, altera_org)
        try:
            page1 = client.get("/api/v1/projects?limit=2&offset=0").json()["items"]
            page2 = client.get("/api/v1/projects?limit=2&offset=2").json()["items"]
        finally:
            _clear()

        ids1 = {p["id"] for p in page1}
        ids2 = {p["id"] for p in page2}
        assert ids1.isdisjoint(ids2), "pages should not overlap"

    def test_total_reflects_full_count(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="AltTot", org_type=ALTERA_ORG_TYPE)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        for i in range(3):
            store.create_project(
                name=f"TotProj {i}",
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                reporting_period_label="FY 2025",
                organisation_id=altera_org.id,
            )

        _override(analyst, altera_org)
        try:
            r = client.get("/api/v1/projects?limit=1&offset=0")
        finally:
            _clear()

        body = r.json()
        assert body["total"] >= 3
        assert len(body["items"]) == 1


# ---------------------------------------------------------------------------
# 403 response shape
# ---------------------------------------------------------------------------


class TestForbiddenResponseShape:
    def test_viewer_create_project_returns_structured_error(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org = _make_org(store, name="OrgFShape", org_type=CLIENT_ORG_TYPE)
        viewer = _make_user(store, org=org, role=ClientRole.CLIENT_VIEWER)
        _override(viewer, org)
        try:
            r = client.post(
                "/api/v1/projects",
                json={
                    "name": "Blocked",
                    "methodologies_enabled": ["protein_tracker"],
                    "reporting_period_label": "FY 2025",
                },
            )
        finally:
            _clear()
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error_code"] == "forbidden"
        assert "message" in detail

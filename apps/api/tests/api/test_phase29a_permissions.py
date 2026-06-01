"""Phase 29A — API permission regression tests.

Verifies:
- Client cannot access another org's project (gets 404)
- Client cannot approve/reject/deliver exports (gets 403)
- Client cannot create manual enrichment (gets 403)
- Client cannot create or run scenarios (gets 403)
- Altera analyst can access any org's project (200)
- A client can view its OWN org's draft report (Phase Product-UX-D
  self-service); cross-org access is still 404
- Review queue returns paginated Page envelope
- Jobs list returns paginated Page envelope
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from altera_api.api.state import ExportRecord, InMemoryStore, RunRecord
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.report_exports import ReportApprovalStatus
from altera_api.main import app

# ---------------------------------------------------------------------------
# Helpers (identical to test_phase20_report_delivery helpers)
# ---------------------------------------------------------------------------


def _make_org(
    store: InMemoryStore,
    *,
    name: str,
    org_type: OrganisationType = OrganisationType.ALTERA_INTERNAL,
) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name=name,
        slug=name.lower().replace(" ", "-"),
        organisation_type=org_type,
        created_at=datetime.now(UTC),
    )
    store.organisations[org.id] = org
    return org


def _make_user(
    store: InMemoryStore,
    *,
    org: Organisation,
    role: AlteraRole | ClientRole,
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


def _seed_project_and_run(
    store: InMemoryStore,
    org: Organisation,
) -> tuple[UUID, UUID]:
    project = store.create_project(
        name="Test Project",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="FY 2024",
        organisation_id=org.id,
    )
    run = RunRecord(
        id=uuid4(),
        project_id=project.id,
        organisation_id=org.id,
        methodology=Methodology.PROTEIN_TRACKER,
        triggered_by=uuid4(),
        rows_count=0,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        summary_payload={},
    )
    store.runs[run.id] = run
    return project.id, run.id


def _seed_export(
    store: InMemoryStore,
    run_id: UUID,
    org_id: UUID,
    approval_status: ReportApprovalStatus,
) -> UUID:
    export_id = uuid4()
    export = ExportRecord(
        id=export_id,
        run_id=run_id,
        organisation_id=org_id,
        format="pdf",
        status="success",
        storage_path=f"exports/{export_id}.pdf",
        filename=f"{export_id}.pdf",
        size_bytes=0,
        approval_status=approval_status.value,
        created_at=datetime.now(UTC),
    )
    store.export_records[export_id] = export
    return export_id


# ---------------------------------------------------------------------------
# Cross-org project access
# ---------------------------------------------------------------------------


class TestCrossOrgAccess:
    def test_client_cannot_access_other_org_project(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Client user from org A sees 404 for org B's project (not 403 — no info leak)."""
        org_a = _make_org(store, name="OrgA", org_type=OrganisationType.GMS_CLIENT)
        org_b = _make_org(store, name="OrgB", org_type=OrganisationType.GMS_CLIENT)
        client_a = _make_user(store, org=org_a, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(client_a, org_a)
        project_b_id, _ = _seed_project_and_run(store, org_b)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.get(f"/api/v1/projects/{project_b_id}")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 404

    def test_altera_analyst_can_access_any_org_project(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Altera internal user can read any org's project."""
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        ctx = _auth_ctx(analyst, altera_org)
        project_id, _ = _seed_project_and_run(store, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.get(f"/api/v1/projects/{project_id}")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Export approval/rejection/delivery — client blocked
# ---------------------------------------------------------------------------


class TestExportApprovalPermissions:
    def _setup(
        self, store: InMemoryStore
    ) -> tuple[AuthContext, UUID, UUID, UUID]:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, run_id = _seed_project_and_run(store, client_org)
        export_id = _seed_export(store, run_id, client_org.id, ReportApprovalStatus.UNDER_REVIEW)
        return ctx, project_id, run_id, export_id

    def test_client_cannot_approve_export(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        ctx, project_id, run_id, export_id = self._setup(store)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/approve"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"

    def test_client_cannot_reject_export(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        ctx, project_id, run_id, export_id = self._setup(store)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/reject",
                json={"rejection_reason": "test"},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"

    def test_client_cannot_deliver_export(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        ctx, project_id, run_id, export_id = self._setup(store)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/deliver"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"


# ---------------------------------------------------------------------------
# Draft/internal export hidden from clients
# ---------------------------------------------------------------------------


class TestDraftExportVisibility:
    def test_client_can_read_own_draft_report(
        self, store: InMemoryStore
    ) -> None:
        """Phase Product-UX-D — the self-service guided workflow shows the
        full report inline immediately after a calculation. A project's own
        organisation may view its own draft report (access is org-scoped by
        get_project; cross-org is a 404). So the permission gate must NOT
        return 403 here.

        Uses raise_server_exceptions=False because the seeded run has an
        empty summary_payload, so build_report_document throws a 500 after
        the gate — but the gate itself must pass.
        """
        from altera_api.api.store_factory import get_store

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, run_id = _seed_project_and_run(store, client_org)
        _seed_export(store, run_id, client_org.id, ReportApprovalStatus.DRAFT)

        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            with TestClient(app, raise_server_exceptions=False) as http:
                resp = http.get(f"/api/v1/projects/{project_id}/runs/{run_id}/report")
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_store, None)

        # The permission gate passed — a 403 "forbidden" would be the bug.
        assert not (
            resp.status_code == 403
            and resp.json().get("detail", {}).get("error_code") == "forbidden"
        )

    def test_client_can_read_approved_report(
        self, store: InMemoryStore
    ) -> None:
        """Client passes the permission check when an approved export exists.

        Uses raise_server_exceptions=False because the seeded run has an empty
        summary_payload so build_report_document throws a 500 after the
        permission gate — but the gate itself must not return 403.
        """
        from altera_api.api.store_factory import get_store

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, run_id = _seed_project_and_run(store, client_org)
        _seed_export(store, run_id, client_org.id, ReportApprovalStatus.APPROVED)

        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            with TestClient(app, raise_server_exceptions=False) as http:
                resp = http.get(f"/api/v1/projects/{project_id}/runs/{run_id}/report")
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_store, None)

        # The permission gate passed — a 403 with error_code "forbidden" would be wrong.
        assert not (
            resp.status_code == 403
            and resp.json().get("detail", {}).get("error_code") == "forbidden"
        )


# ---------------------------------------------------------------------------
# Manual enrichment — client blocked
# ---------------------------------------------------------------------------


class TestEnrichmentPermissions:
    def test_client_cannot_create_manual_enrichment(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, _ = _seed_project_and_run(store, client_org)
        product_id = uuid4()

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={"enriched_value": 10.0, "rationale": "test"},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"


# ---------------------------------------------------------------------------
# Scenarios — client blocked
# ---------------------------------------------------------------------------


class TestScenarioPermissions:
    def test_client_cannot_create_scenario(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, run_id = _seed_project_and_run(store, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/scenarios",
                json={"name": "s1", "base_run_id": str(run_id)},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"

    def test_client_cannot_run_scenario(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.post(f"/api/v1/scenarios/{uuid4()}/run")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "forbidden"


# ---------------------------------------------------------------------------
# Pagination envelope checks
# ---------------------------------------------------------------------------


class TestPaginationEnvelope:
    def test_review_queue_returns_page_envelope(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """GET /review returns {items, total, limit, offset}."""
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        altera_org = _make_org(store, name="Altera")
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        ctx = _auth_ctx(reviewer, altera_org)
        project_id, _ = _seed_project_and_run(store, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.get(f"/api/v1/projects/{project_id}/review")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
        assert isinstance(body["items"], list)

    def test_review_queue_limit_and_offset(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """limit and offset params are echoed in the response envelope."""
        altera_org = _make_org(store, name="Altera")
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        ctx = _auth_ctx(reviewer, altera_org)
        project_id, _ = _seed_project_and_run(store, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.get(f"/api/v1/projects/{project_id}/review?limit=10&offset=5")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 10
        assert body["offset"] == 5

    def test_jobs_list_returns_page_envelope(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """GET /jobs returns {items, total, limit, offset}."""
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        ctx = _auth_ctx(user, client_org)
        project_id, _ = _seed_project_and_run(store, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            resp = client.get(f"/api/v1/projects/{project_id}/jobs")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

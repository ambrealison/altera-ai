"""Phase 20 — report approval, delivery, and client-safe report workflow.

Covers:
- domain helpers: can_submit_for_review, can_deliver, can_client_download
- lifecycle: draft → under_review → approved → delivered
- lifecycle: approved → rejected; rejected cannot be delivered
- permissions: client blocked from draft/under_review/rejected
- permissions: client allowed for approved/delivered
- permissions: reviewer cannot approve/reject/deliver
- permissions: admin can deliver but not approve/reject
- list_exports: clients only see approved/delivered
- client download tracking: increments count, sets client_downloaded_at
- audit events: submit_for_review, approve, reject, deliver, download
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
from altera_api.domain.report_approval import (
    can_client_download,
    can_deliver,
    can_submit_for_review,
)
from altera_api.domain.report_exports import ReportApprovalStatus
from altera_api.main import app

# ---------------------------------------------------------------------------
# Helpers
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
        methodology=Methodology.PROTEIN_TRACKER,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        triggered_by=uuid4(),
        organisation_id=org.id,
    )
    store.runs[run.id] = run
    return project.id, run.id


def _seed_export(
    store: InMemoryStore,
    run_id: UUID,
    org_id: UUID,
    *,
    approval_status: str = "draft",
    fmt: str = "csv",
) -> ExportRecord:
    record = ExportRecord(
        id=uuid4(),
        run_id=run_id,
        organisation_id=org_id,
        format=fmt,
        status="success",
        storage_path=f"organisations/{org_id}/exports/{run_id}/x/report.{fmt}",
        filename=f"report.{fmt}",
        size_bytes=1234,
        approval_status=approval_status,
        created_at=datetime.now(UTC),
    )
    store.export_records[record.id] = record
    return record


# ---------------------------------------------------------------------------
# Unit tests — pure domain helpers
# ---------------------------------------------------------------------------


class TestDomainHelpers:
    def test_any_altera_role_can_submit_for_review(self) -> None:
        for role in AlteraRole:
            assert can_submit_for_review(role) is True

    def test_client_cannot_submit_for_review(self) -> None:
        for role in ClientRole:
            assert can_submit_for_review(role) is False

    def test_methodology_lead_can_deliver(self) -> None:
        assert can_deliver(AlteraRole.ALTERA_METHODOLOGY_LEAD) is True

    def test_admin_can_deliver(self) -> None:
        assert can_deliver(AlteraRole.ALTERA_ADMIN) is True

    def test_analyst_cannot_deliver(self) -> None:
        assert can_deliver(AlteraRole.ALTERA_ANALYST) is False

    def test_reviewer_cannot_deliver(self) -> None:
        assert can_deliver(AlteraRole.ALTERA_REVIEWER) is False

    def test_client_cannot_download_draft(self) -> None:
        assert can_client_download(ReportApprovalStatus.DRAFT) is False

    def test_client_cannot_download_under_review(self) -> None:
        assert can_client_download(ReportApprovalStatus.UNDER_REVIEW) is False

    def test_client_cannot_download_rejected(self) -> None:
        assert can_client_download(ReportApprovalStatus.REJECTED) is False

    def test_client_can_download_approved(self) -> None:
        assert can_client_download(ReportApprovalStatus.APPROVED) is True

    def test_client_can_download_delivered(self) -> None:
        assert can_client_download(ReportApprovalStatus.DELIVERED) is True


# ---------------------------------------------------------------------------
# Lifecycle — store-level
# ---------------------------------------------------------------------------


class TestExportLifecycle:
    def test_draft_to_under_review(self, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        _, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="draft")

        updated = store.mark_export_under_review(exp.id, by_user_id=analyst.user_id)
        assert updated is not None
        assert updated.approval_status == "under_review"
        assert updated.under_review_by == analyst.user_id
        assert updated.under_review_at is not None

    def test_under_review_to_approved(self, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        _, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="under_review")

        updated = store.update_export_approval(
            exp.id, approval_status="approved", by_user_id=lead.user_id
        )
        assert updated is not None
        assert updated.approval_status == "approved"
        assert updated.approved_by == lead.user_id
        assert updated.approved_at is not None

    def test_approved_to_delivered(self, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        _, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")

        updated = store.deliver_export(exp.id, by_user_id=lead.user_id)
        assert updated is not None
        assert updated.approval_status == "delivered"
        assert updated.delivered_by == lead.user_id
        assert updated.delivered_at is not None

    def test_draft_to_rejected_with_reason(self, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        _, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="draft")

        updated = store.update_export_approval(
            exp.id,
            approval_status="rejected",
            by_user_id=lead.user_id,
            rejection_reason="Methodology version mismatch",
        )
        assert updated is not None
        assert updated.approval_status == "rejected"
        assert updated.rejection_reason == "Methodology version mismatch"
        assert updated.rejected_by == lead.user_id

    def test_client_download_tracking(self, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        _, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")

        assert exp.client_download_count == 0
        assert exp.client_downloaded_at is None

        updated = store.record_client_download(exp.id)
        assert updated is not None
        assert updated.client_download_count == 1
        assert updated.client_downloaded_at is not None

        updated2 = store.record_client_download(exp.id)
        assert updated2 is not None
        assert updated2.client_download_count == 2
        # first-download timestamp is preserved
        assert updated2.client_downloaded_at == updated.client_downloaded_at


# ---------------------------------------------------------------------------
# HTTP — lifecycle routes
# ---------------------------------------------------------------------------


class TestLifecycleRoutes:
    def _lead_ctx(self, store: InMemoryStore) -> tuple[AuthContext, UUID, UUID, UUID]:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(lead, altera_org)
        return ctx, project_id, run_id, exp.id

    def test_submit_for_review(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/submit-for-review"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approval_status"] == "under_review"
        assert body["under_review_by"] == str(analyst.user_id)

    def test_approve_export(self, client: TestClient, store: InMemoryStore) -> None:
        ctx, project_id, run_id, export_id = self._lead_ctx(store)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/approve"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approval_status"] == "approved"
        assert body["approved_by"] is not None
        assert body["approved_at"] is not None

    def test_reject_export_with_reason(self, client: TestClient, store: InMemoryStore) -> None:
        ctx, project_id, run_id, export_id = self._lead_ctx(store)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/reject",
                json={"rejection_reason": "Needs reclassification"},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approval_status"] == "rejected"
        assert body["rejection_reason"] == "Needs reclassification"
        assert body["rejected_by"] is not None

    def test_deliver_approved_export(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")
        ctx = _auth_ctx(lead, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/deliver")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approval_status"] == "delivered"
        assert body["delivered_by"] is not None
        assert body["delivered_at"] is not None

    def test_cannot_deliver_draft_export(self, client: TestClient, store: InMemoryStore) -> None:
        ctx, project_id, run_id, export_id = self._lead_ctx(store)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{export_id}/deliver"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 409, r.text
        assert "approved" in r.json()["detail"]

    def test_cannot_deliver_rejected_export(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="rejected")
        ctx = _auth_ctx(lead, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/deliver")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 409, r.text

    def test_cannot_submit_delivered_for_review(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="delivered")
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/submit-for-review"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 409, r.text

    def test_admin_can_deliver(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        admin = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ADMIN)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")
        ctx = _auth_ctx(admin, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/deliver")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        assert r.json()["approval_status"] == "delivered"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestExportPermissions:
    def test_client_cannot_submit_for_review(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        exp = _seed_export(store, run_id, client_org.id)
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/submit-for-review"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 403, r.text

    def test_reviewer_cannot_approve(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(reviewer, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/approve")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 403, r.text
        assert "methodology_lead" in r.json()["detail"]

    def test_reviewer_cannot_reject(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(reviewer, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/reject",
                json={},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 403, r.text

    def test_reviewer_cannot_deliver(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")
        ctx = _auth_ctx(reviewer, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/deliver")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 403, r.text

    def test_admin_cannot_approve(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        admin = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ADMIN)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(admin, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/approve")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# List filtering — clients see only approved/delivered
# ---------------------------------------------------------------------------


class TestExportListFiltering:
    def test_altera_sees_all_statuses(self, client: TestClient, store: InMemoryStore) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        for status in ("draft", "under_review", "approved", "rejected", "delivered"):
            _seed_export(store, run_id, altera_org.id, approval_status=status)
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/exports")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        statuses = {e["approval_status"] for e in r.json()}
        assert statuses == {"draft", "under_review", "approved", "rejected", "delivered"}

    def test_client_sees_only_approved_and_delivered(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Create a shared project owned by the client org
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        for status in ("draft", "under_review", "approved", "rejected", "delivered"):
            _seed_export(store, run_id, client_org.id, approval_status=status)
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/exports")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        statuses = {e["approval_status"] for e in r.json()}
        assert statuses == {"approved", "delivered"}
        assert "draft" not in statuses
        assert "under_review" not in statuses
        assert "rejected" not in statuses

    def test_response_includes_metadata_fields(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        _seed_export(store, run_id, altera_org.id, approval_status="draft")
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/exports")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        item = r.json()[0]
        for field in (
            "approved_by",
            "approved_at",
            "rejected_by",
            "rejected_at",
            "rejection_reason",
            "under_review_by",
            "under_review_at",
            "delivered_by",
            "delivered_at",
            "client_download_count",
            "client_downloaded_at",
        ):
            assert field in item, f"missing field: {field}"


# ---------------------------------------------------------------------------
# Client download tracking (with FakeStorageService)
# ---------------------------------------------------------------------------


class TestClientDownloadTracking:
    def test_client_download_increments_count(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        from altera_api.storage.factory import get_storage_service
        from altera_api.storage.fake import FakeStorageService

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        exp = _seed_export(store, run_id, client_org.id, approval_status="approved", fmt="csv")

        # Populate fake storage with export content
        fake_storage = FakeStorageService()
        fake_storage._exports[exp.storage_path] = b"col1,col2\na,b\n"
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        app.dependency_overrides[get_storage_service] = lambda: fake_storage
        try:
            r = client.get(
                f"/api/v1/projects/{project_id}/runs/{run_id}/export?fmt=csv",
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_storage_service, None)

        # Response is a redirect (302) to the signed URL
        assert r.status_code == 302, r.text

        updated = store.get_export_record(exp.id)
        assert updated is not None
        assert updated.client_download_count == 1
        assert updated.client_downloaded_at is not None

    def test_raw_storage_path_not_exposed_in_list(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        _seed_export(store, run_id, altera_org.id, approval_status="approved")
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/exports")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200
        item = r.json()[0]
        # The raw Supabase storage_path must not appear in the list response
        assert "storage_path" not in item

    def test_delivered_export_is_downloadable_by_client(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        from altera_api.storage.factory import get_storage_service
        from altera_api.storage.fake import FakeStorageService

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        exp = _seed_export(store, run_id, client_org.id, approval_status="delivered", fmt="csv")

        fake_storage = FakeStorageService()
        fake_storage._exports[exp.storage_path] = b"col1,col2\na,b\n"
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        app.dependency_overrides[get_storage_service] = lambda: fake_storage
        try:
            r = client.get(
                f"/api/v1/projects/{project_id}/runs/{run_id}/export?fmt=csv",
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_storage_service, None)

        assert r.status_code == 302, r.text

    def test_client_blocked_from_draft_export_with_storage(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        from altera_api.storage.factory import get_storage_service
        from altera_api.storage.fake import FakeStorageService

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        _seed_export(store, run_id, client_org.id, approval_status="draft")

        fake_storage = FakeStorageService()
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        app.dependency_overrides[get_storage_service] = lambda: fake_storage
        try:
            r = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}/export?fmt=csv")
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_storage_service, None)

        assert r.status_code == 403, r.text
        assert "approved" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


class TestAuditEvents:
    def test_submit_for_review_emits_audit_event(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        from altera_api.domain.audit import AuditEventType

        altera_org = _make_org(store, name="Altera")
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(analyst, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/submit-for-review"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        events = [
            e for e in store.audit_events if e.action == AuditEventType.EXPORT_SUBMITTED_FOR_REVIEW
        ]
        assert len(events) == 1

    def test_approve_emits_audit_event(self, client: TestClient, store: InMemoryStore) -> None:
        from altera_api.domain.audit import AuditEventType

        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(lead, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/approve")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        events = [e for e in store.audit_events if e.action == AuditEventType.EXPORT_APPROVED]
        assert len(events) == 1
        assert events[0].actor_user_id == lead.user_id

    def test_reject_emits_audit_event(self, client: TestClient, store: InMemoryStore) -> None:
        from altera_api.domain.audit import AuditEventType

        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id)
        ctx = _auth_ctx(lead, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            client.post(
                f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/reject",
                json={"rejection_reason": "Test reason"},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        events = [e for e in store.audit_events if e.action == AuditEventType.EXPORT_REJECTED]
        assert len(events) == 1

    def test_deliver_emits_audit_event(self, client: TestClient, store: InMemoryStore) -> None:
        from altera_api.domain.audit import AuditEventType

        altera_org = _make_org(store, name="Altera")
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_id, run_id = _seed_project_and_run(store, altera_org)
        exp = _seed_export(store, run_id, altera_org.id, approval_status="approved")
        ctx = _auth_ctx(lead, altera_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            client.post(f"/api/v1/projects/{project_id}/runs/{run_id}/exports/{exp.id}/deliver")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        events = [e for e in store.audit_events if e.action == AuditEventType.EXPORT_DELIVERED]
        assert len(events) == 1
        assert events[0].actor_user_id == lead.user_id

    def test_client_download_emits_audit_event(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        from altera_api.domain.audit import AuditEventType
        from altera_api.storage.factory import get_storage_service
        from altera_api.storage.fake import FakeStorageService

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        project_id, run_id = _seed_project_and_run(store, client_org)
        exp = _seed_export(store, run_id, client_org.id, approval_status="approved", fmt="csv")
        fake_storage = FakeStorageService()
        fake_storage._exports[exp.storage_path] = b"a,b\n1,2\n"
        ctx = _auth_ctx(user, client_org)

        app.dependency_overrides[authed_user] = lambda: ctx
        app.dependency_overrides[get_storage_service] = lambda: fake_storage
        try:
            client.get(
                f"/api/v1/projects/{project_id}/runs/{run_id}/export?fmt=csv",
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)
            app.dependency_overrides.pop(get_storage_service, None)

        events = [e for e in store.audit_events if e.action == AuditEventType.EXPORT_DOWNLOADED]
        assert len(events) == 1
        assert events[0].actor_user_id == user.user_id

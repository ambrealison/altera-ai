"""Phase 14 tests: role namespace split + cross-org visibility + export approval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import ExportRecord, InMemoryStore, RunRecord
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType, Role
from altera_api.domain.organisation import Organisation, UserProfile
from tests.auth.conftest import TEST_JWT_SECRET

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(
    store: InMemoryStore,
    *,
    name: str,
    org_type: OrganisationType = OrganisationType.GMS_CLIENT,
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
    role: Role | ClientRole | AlteraRole,
) -> UserProfile:
    uid = uuid4()
    email = f"{uid}@test.local"
    profile = UserProfile(
        user_id=uid,
        organisation_id=org.id,
        email=email,
        display_name=str(uid),
        role=role,
        created_at=datetime.now(UTC),
    )
    store.users[uid] = profile
    return profile


def _token(mint_token: Callable[..., str], user: UserProfile) -> str:
    return mint_token(sub=user.user_id, email=user.email)


# ---------------------------------------------------------------------------
# Role namespace — AuthContext.is_altera_internal
# ---------------------------------------------------------------------------


class TestAuthContextRoles:
    def test_altera_role_is_altera_internal(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        altera_org = _make_org(store, name="Altera HQ", org_type=OrganisationType.ALTERA_INTERNAL)
        user = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        r = client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {_token(mint_token, user)}"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "altera_analyst"
        assert body["organisation_type"] == "altera_internal"

    def test_client_role_is_gms_client(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        r = client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {_token(mint_token, user)}"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "client_owner"
        assert body["organisation_type"] == "gms_client"

    def test_methodology_lead_can_approve(self, store: InMemoryStore) -> None:
        from altera_api.auth.models import AuthContext, AuthProvider

        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        ctx = AuthContext(
            user_id=uuid4(),
            email="lead@altera.ai",
            organisation_id=altera_org.id,
            role=AlteraRole.ALTERA_METHODOLOGY_LEAD,
            auth_provider=AuthProvider.SUPABASE,
            is_dev_auth=False,
            organisation_type=OrganisationType.ALTERA_INTERNAL,
        )
        assert ctx.can_approve_report is True
        assert ctx.is_altera_internal is True
        assert ctx.can_review is True

    def test_analyst_cannot_approve(self, store: InMemoryStore) -> None:
        from altera_api.auth.models import AuthContext, AuthProvider

        ctx = AuthContext(
            user_id=uuid4(),
            email="analyst@altera.ai",
            organisation_id=uuid4(),
            role=AlteraRole.ALTERA_ANALYST,
            auth_provider=AuthProvider.SUPABASE,
            is_dev_auth=False,
            organisation_type=OrganisationType.ALTERA_INTERNAL,
        )
        assert ctx.can_approve_report is False
        assert ctx.can_write_data is True

    def test_client_owner_cannot_approve(self) -> None:
        from altera_api.auth.models import AuthContext, AuthProvider

        ctx = AuthContext(
            user_id=uuid4(),
            email="owner@retailco.com",
            organisation_id=uuid4(),
            role=ClientRole.CLIENT_OWNER,
            auth_provider=AuthProvider.SUPABASE,
            is_dev_auth=False,
        )
        assert ctx.can_approve_report is False
        assert ctx.is_altera_internal is False
        assert ctx.can_review is False

    def test_client_viewer_cannot_write(self) -> None:
        from altera_api.auth.models import AuthContext, AuthProvider

        ctx = AuthContext(
            user_id=uuid4(),
            email="viewer@retailco.com",
            organisation_id=uuid4(),
            role=ClientRole.CLIENT_VIEWER,
            auth_provider=AuthProvider.SUPABASE,
            is_dev_auth=False,
        )
        assert ctx.can_write_data is False
        assert ctx.can_review is False


# ---------------------------------------------------------------------------
# Cross-org visibility for Altera staff
# ---------------------------------------------------------------------------


class TestAlteraCrossOrgVisibility:
    def test_altera_can_list_client_projects(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)

        # Client org with a project
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        client_user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        altera_user = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)

        # Create project as client user
        client_token = _token(mint_token, client_user)
        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {client_token}"},
            json={
                "name": "Client Project",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        assert r.status_code == 201
        project_id = r.json()["id"]

        # Altera analyst can see it
        altera_token = _token(mint_token, altera_user)
        r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {altera_token}"})
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert project_id in ids

    def test_client_cannot_see_other_client_projects(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)

        org_a = _make_org(store, name="OrgA", org_type=OrganisationType.GMS_CLIENT)
        org_b = _make_org(store, name="OrgB", org_type=OrganisationType.GMS_CLIENT)
        user_a = _make_user(store, org=org_a, role=ClientRole.CLIENT_OWNER)
        user_b = _make_user(store, org=org_b, role=ClientRole.CLIENT_OWNER)

        # Create project as user_a
        token_a = _token(mint_token, user_a)
        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "name": "OrgA Project",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        assert r.status_code == 201
        project_id = r.json()["id"]

        # user_b cannot see it
        token_b = _token(mint_token, user_b)
        r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_b}"})
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert project_id not in ids

        # user_b gets 404 on direct fetch
        r = client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r.status_code == 404

    def test_altera_can_fetch_client_project_directly(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        client_user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        altera_user = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)

        token_client = _token(mint_token, client_user)
        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token_client}"},
            json={
                "name": "Client Project",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        project_id = r.json()["id"]

        altera_token = _token(mint_token, altera_user)
        r = client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {altera_token}"},
        )
        assert r.status_code == 200
        assert r.json()["id"] == project_id


# ---------------------------------------------------------------------------
# Export approval workflow
# ---------------------------------------------------------------------------


class TestExportApprovalWorkflow:
    def _seed_run(self, store: InMemoryStore, org_id: UUID) -> RunRecord:
        run = RunRecord(
            id=uuid4(),
            project_id=uuid4(),
            methodology=Methodology.PROTEIN_TRACKER,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by=uuid4(),
            organisation_id=org_id,
        )
        store.runs[run.id] = run
        return run

    def _seed_export(
        self, store: InMemoryStore, run: RunRecord, *, approval_status: str = "draft"
    ) -> ExportRecord:
        record = ExportRecord(
            id=uuid4(),
            run_id=run.id,
            organisation_id=run.organisation_id,  # type: ignore[arg-type]
            format="csv",
            status="success",
            storage_path=f"organisations/{run.organisation_id}/exports/{run.id}/x/report.csv",
            filename="report.csv",
            size_bytes=1234,
            approval_status=approval_status,
            created_at=datetime.now(UTC),
        )
        store.export_records[record.id] = record
        return record

    def test_client_blocked_without_approved_export(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)

        # The export gate only fires when Storage is configured (storage=not None).
        # In test mode, storage is None so the gate is skipped — client always
        # gets inline bytes in dev/test mode.  This test verifies the store-level
        # logic directly.
        run = self._seed_run(store, client_org.id)
        self._seed_export(store, run, approval_status="draft")
        exports = store.get_exports_for_run(run.id)
        approved = [e for e in exports if e.approval_status == "approved"]
        assert len(approved) == 0

    def test_methodology_lead_can_approve_export(
        self,
        store: InMemoryStore,
    ) -> None:
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        run = self._seed_run(store, client_org.id)
        export = self._seed_export(store, run, approval_status="draft")

        updated = store.update_export_approval(
            export.id, approval_status="approved", by_user_id=lead.user_id
        )
        assert updated is not None
        assert updated.approval_status == "approved"
        assert updated.approved_by == lead.user_id
        assert updated.approved_at is not None

    def test_methodology_lead_can_reject_export(
        self,
        store: InMemoryStore,
    ) -> None:
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        run = self._seed_run(store, client_org.id)
        export = self._seed_export(store, run, approval_status="draft")

        updated = store.update_export_approval(
            export.id,
            approval_status="rejected",
            by_user_id=lead.user_id,
            rejection_reason="Methodology version mismatch",
        )
        assert updated is not None
        assert updated.approval_status == "rejected"
        assert updated.rejected_by == lead.user_id
        assert updated.rejection_reason == "Methodology version mismatch"

    def test_non_lead_cannot_approve_via_api(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        analyst = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)

        # Seed a project + run in the client org
        project = store.create_project(
            name="Test",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="FY2025",
            organisation_id=client_org.id,
        )
        run = self._seed_run(store, client_org.id)
        store.runs[run.id] = RunRecord(
            id=run.id,
            project_id=project.id,
            methodology=run.methodology,
            started_at=run.started_at,
            finished_at=run.finished_at,
            triggered_by=run.triggered_by,
            organisation_id=client_org.id,
        )
        export = self._seed_export(store, run, approval_status="draft")

        token = _token(mint_token, analyst)
        r = client.post(
            f"/api/v1/projects/{project.id}/runs/{run.id}/exports/{export.id}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "forbidden"

    def test_methodology_lead_can_approve_via_api(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        lead = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_METHODOLOGY_LEAD)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)

        project = store.create_project(
            name="Test",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="FY2025",
            organisation_id=client_org.id,
        )
        run = self._seed_run(store, client_org.id)
        store.runs[run.id] = RunRecord(
            id=run.id,
            project_id=project.id,
            methodology=run.methodology,
            started_at=run.started_at,
            finished_at=run.finished_at,
            triggered_by=run.triggered_by,
            organisation_id=client_org.id,
        )
        export = self._seed_export(store, run, approval_status="draft")

        token = _token(mint_token, lead)
        r = client.post(
            f"/api/v1/projects/{project.id}/runs/{run.id}/exports/{export.id}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["approval_status"] == "approved"
        assert body["id"] == str(export.id)

    def test_get_exports_for_run(self, store: InMemoryStore) -> None:
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        run = self._seed_run(store, client_org.id)
        e1 = self._seed_export(store, run, approval_status="draft")
        e2 = self._seed_export(store, run, approval_status="approved")

        exports = store.get_exports_for_run(run.id)
        assert len(exports) == 2
        ids = {e.id for e in exports}
        assert e1.id in ids
        assert e2.id in ids

    def test_storage_gate_blocks_client_without_approved_export(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP-level: client gets 403 when Storage is configured but no approved export."""
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        from altera_api.api.state import RunRecord
        from altera_api.main import app
        from altera_api.storage.factory import get_storage_service

        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        token = _token(mint_token, user)

        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "P",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        assert r.status_code == 201
        project_id = r.json()["id"]
        project = store.get_project(UUID(project_id))
        assert project is not None

        run = self._seed_run(store, client_org.id)
        store.runs[run.id] = RunRecord(
            id=run.id,
            project_id=project.id,
            methodology=run.methodology,
            started_at=run.started_at,
            finished_at=run.finished_at,
            triggered_by=run.triggered_by,
            organisation_id=client_org.id,
        )
        self._seed_export(store, run, approval_status="draft")

        app.dependency_overrides[get_storage_service] = lambda: object()
        try:
            r = client.get(
                f"/api/v1/projects/{project_id}/runs/{run.id}/export",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            app.dependency_overrides.pop(get_storage_service, None)

        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "forbidden"


# ---------------------------------------------------------------------------
# Manual review ownership
# ---------------------------------------------------------------------------


class TestManualReviewOwnership:
    def test_client_cannot_submit_review_decision(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP-level: client users cannot submit manual review decisions."""
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)
        user = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        token = _token(mint_token, user)

        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "P",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        assert r.status_code == 201
        project_id = r.json()["id"]

        r = client.post(
            f"/api/v1/projects/{project_id}/review/{uuid4()}/protein_tracker/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "accepted"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "forbidden"

    def test_altera_reviewer_can_submit_review_decision_404_on_unknown_product(
        self,
        store: InMemoryStore,
        client: TestClient,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP-level: Altera staff pass the role gate (404 on unknown product is fine)."""
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        altera_org = _make_org(store, name="Altera", org_type=OrganisationType.ALTERA_INTERNAL)
        reviewer = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_REVIEWER)
        client_org = _make_org(store, name="RetailCo", org_type=OrganisationType.GMS_CLIENT)

        reviewer_token = _token(mint_token, reviewer)
        client_owner = _make_user(store, org=client_org, role=ClientRole.CLIENT_OWNER)
        client_token = _token(mint_token, client_owner)

        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {client_token}"},
            json={
                "name": "P",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY2025",
            },
        )
        project_id = r.json()["id"]

        r = client.post(
            f"/api/v1/projects/{project_id}/review/{uuid4()}/protein_tracker/decision",
            headers={"Authorization": f"Bearer {reviewer_token}"},
            json={"decision": "accepted"},
        )
        # Role gate is passed; 404 is from the unknown product — not a 403.
        assert r.status_code == 404

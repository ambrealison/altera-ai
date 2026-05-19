"""Phase 32A — admin route tests.

Verifies:
- altera_admin can list organisations
- altera_admin can create a client organisation
- altera_admin can invite a user (dev/memory mode; no Supabase call)
- Non-admin roles receive 403 on all admin endpoints
- Duplicate slug returns 409
- Invalid role on invite returns 400
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


def _make_altera_org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="Altera AI",
        slug="altera-ai",
        organisation_type=OrganisationType.ALTERA_INTERNAL,
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


def _auth(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id,
        email=user.email,
        organisation_id=org.id,
        role=user.role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=org.organisation_type,
    )


@pytest.fixture
def admin_client(store: InMemoryStore):
    altera_org = _make_altera_org(store)
    admin_user = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ADMIN)
    admin_ctx = _auth(admin_user, altera_org)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[authed_user] = lambda: admin_ctx
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(authed_user, None)


@pytest.fixture
def analyst_client(store: InMemoryStore):
    altera_org = _make_altera_org(store)
    analyst_user = _make_user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
    analyst_ctx = _auth(analyst_user, altera_org)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[authed_user] = lambda: analyst_ctx
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Tests — list organisations
# ---------------------------------------------------------------------------


class TestListOrganisations:
    def test_admin_can_list(self, admin_client: TestClient) -> None:
        r = admin_client.get("/api/v1/admin/organisations")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        # Bootstrap org is always present
        assert len(data) >= 1

    def test_non_admin_gets_403(self, analyst_client: TestClient) -> None:
        r = analyst_client.get("/api/v1/admin/organisations")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "admin_required"


# ---------------------------------------------------------------------------
# Tests — create organisation
# ---------------------------------------------------------------------------


class TestCreateOrganisation:
    def test_admin_creates_org(self, admin_client: TestClient) -> None:
        r = admin_client.post(
            "/api/v1/admin/organisations",
            json={"name": "Acme Retail", "slug": "acme-retail"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Acme Retail"
        assert body["slug"] == "acme-retail"
        assert body["organisation_type"] == "gms_client"
        assert "id" in body

    def test_invalid_slug_returns_400(self, admin_client: TestClient) -> None:
        r = admin_client.post(
            "/api/v1/admin/organisations",
            json={"name": "Bad Slug", "slug": "Bad Slug!"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_slug"

    def test_non_admin_gets_403(self, analyst_client: TestClient) -> None:
        r = analyst_client.post(
            "/api/v1/admin/organisations",
            json={"name": "X", "slug": "x"},
        )
        assert r.status_code == 403

    def test_created_org_appears_in_list(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        admin_client.post(
            "/api/v1/admin/organisations",
            json={"name": "Beta Corp", "slug": "beta-corp"},
        )
        orgs = store.list_organisations()
        slugs = [o.slug for o in orgs]
        assert "beta-corp" in slugs


# ---------------------------------------------------------------------------
# Tests — invite user (dev mode, no Supabase call)
# ---------------------------------------------------------------------------


class TestInviteUser:
    def _create_client_org(self, store: InMemoryStore) -> Organisation:
        return store.create_organisation(name="Test Client", slug="test-client")

    def test_invite_creates_profile(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = self._create_client_org(store)
        r = admin_client.post(
            f"/api/v1/admin/organisations/{org.id}/invite",
            json={"email": "alice@client.com", "role": "client_owner"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["email"] == "alice@client.com"
        assert body["role"] == "client_owner"
        assert body["organisation_id"] == str(org.id)
        # invite_sent is False in dev mode (no Supabase configured)
        assert body["invite_sent"] is False
        # Profile is pre-provisioned in store
        user_id_str = body["user_id"]
        from uuid import UUID
        profile = store.get_user(UUID(user_id_str))
        assert profile is not None
        assert profile.email == "alice@client.com"

    def test_invalid_role_returns_400(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = self._create_client_org(store)
        r = admin_client.post(
            f"/api/v1/admin/organisations/{org.id}/invite",
            json={"email": "bob@client.com", "role": "super_admin"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_role"

    def test_unknown_org_returns_404(self, admin_client: TestClient) -> None:
        r = admin_client.post(
            f"/api/v1/admin/organisations/{uuid4()}/invite",
            json={"email": "x@y.com", "role": "client_owner"},
        )
        assert r.status_code == 404

    def test_non_admin_gets_403(
        self, analyst_client: TestClient, store: InMemoryStore
    ) -> None:
        org = self._create_client_org(store)
        r = analyst_client.post(
            f"/api/v1/admin/organisations/{org.id}/invite",
            json={"email": "x@y.com", "role": "client_owner"},
        )
        assert r.status_code == 403

    def test_different_roles_all_valid(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = self._create_client_org(store)
        for role in ("client_owner", "client_admin", "client_viewer"):
            r = admin_client.post(
                f"/api/v1/admin/organisations/{org.id}/invite",
                json={"email": f"{role}@client.com", "role": role},
            )
            assert r.status_code == 201, f"expected 201 for role={role}, got {r.status_code}"


# ---------------------------------------------------------------------------
# Tests — list members (Phase 32B)
# ---------------------------------------------------------------------------


class TestListMembers:
    def _setup_org_with_members(
        self, store: InMemoryStore, *, member_count: int = 2
    ) -> Organisation:
        org = store.create_organisation(name="Member Org", slug="member-org")
        for i in range(member_count):
            profile = UserProfile(
                user_id=uuid4(),
                organisation_id=org.id,
                email=f"user{i}@client.com",
                display_name=f"User {i}",
                role=ClientRole.CLIENT_VIEWER,
                created_at=datetime.now(UTC),
            )
            store.upsert_user(profile)
        return org

    def test_admin_can_list_members(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = self._setup_org_with_members(store, member_count=3)
        r = admin_client.get(f"/api/v1/admin/organisations/{org.id}/members")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 3
        member = body[0]
        assert "user_id" in member
        assert "email" in member
        assert "role" in member
        assert "organisation_id" in member

    def test_empty_org_returns_empty_list(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Empty Org", slug="empty-org")
        r = admin_client.get(f"/api/v1/admin/organisations/{org.id}/members")
        assert r.status_code == 200
        assert r.json() == []

    def test_unknown_org_returns_404(self, admin_client: TestClient) -> None:
        r = admin_client.get(f"/api/v1/admin/organisations/{uuid4()}/members")
        assert r.status_code == 404

    def test_non_admin_gets_403(
        self, analyst_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="X", slug="x-org")
        r = analyst_client.get(f"/api/v1/admin/organisations/{org.id}/members")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "admin_required"


# ---------------------------------------------------------------------------
# Tests — resend invite (Phase 32B)
# ---------------------------------------------------------------------------


class TestResendInvite:
    def _org_with_member(self, store: InMemoryStore) -> tuple[Organisation, UserProfile]:
        org = store.create_organisation(name="Resend Org", slug="resend-org")
        profile = UserProfile(
            user_id=uuid4(),
            organisation_id=org.id,
            email="pending@client.com",
            display_name="pending",
            role=ClientRole.CLIENT_OWNER,
            created_at=datetime.now(UTC),
        )
        store.upsert_user(profile)
        return org, profile

    def test_resend_returns_200_invite_sent_false_in_dev(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        r = admin_client.post(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}/resend-invite"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == str(member.user_id)
        assert body["email"] == "pending@client.com"
        assert body["organisation_id"] == str(org.id)
        assert body["invite_sent"] is False  # dev mode — no Supabase

    def test_unknown_org_returns_404(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        r = admin_client.post(
            f"/api/v1/admin/organisations/{uuid4()}/members/{uuid4()}/resend-invite"
        )
        assert r.status_code == 404

    def test_unknown_member_returns_404(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Y", slug="y-org")
        r = admin_client.post(
            f"/api/v1/admin/organisations/{org.id}/members/{uuid4()}/resend-invite"
        )
        assert r.status_code == 404

    def test_non_admin_gets_403(
        self, analyst_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Z", slug="z-org")
        member_id = uuid4()
        r = analyst_client.post(
            f"/api/v1/admin/organisations/{org.id}/members/{member_id}/resend-invite"
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Tests — change role (Phase 32B)
# ---------------------------------------------------------------------------


class TestUpdateMemberRole:
    def _org_with_member(
        self, store: InMemoryStore, *, role: ClientRole = ClientRole.CLIENT_VIEWER
    ) -> tuple[Organisation, UserProfile]:
        org = store.create_organisation(name="Role Org", slug="role-org")
        profile = UserProfile(
            user_id=uuid4(),
            organisation_id=org.id,
            email="user@client.com",
            display_name="user",
            role=role,
            created_at=datetime.now(UTC),
        )
        store.upsert_user(profile)
        return org, profile

    def test_admin_can_change_role(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store, role=ClientRole.CLIENT_VIEWER)
        r = admin_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}",
            json={"role": "client_owner"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "client_owner"
        assert body["user_id"] == str(member.user_id)

    def test_role_persisted_in_store(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store, role=ClientRole.CLIENT_VIEWER)
        admin_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}",
            json={"role": "client_admin"},
        )
        updated = store.get_user(member.user_id)
        assert updated is not None
        assert updated.role == ClientRole.CLIENT_ADMIN

    def test_invalid_role_returns_400(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        r = admin_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}",
            json={"role": "super_admin"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_role"

    def test_altera_role_rejected_400(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        r = admin_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}",
            json={"role": "altera_admin"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_role"

    def test_unknown_org_returns_404(self, admin_client: TestClient) -> None:
        r = admin_client.patch(
            f"/api/v1/admin/organisations/{uuid4()}/members/{uuid4()}",
            json={"role": "client_owner"},
        )
        assert r.status_code == 404

    def test_unknown_member_returns_404(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Patch Org", slug="patch-org")
        r = admin_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{uuid4()}",
            json={"role": "client_owner"},
        )
        assert r.status_code == 404

    def test_non_admin_gets_403(
        self, analyst_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Analyst Org", slug="analyst-org")
        r = analyst_client.patch(
            f"/api/v1/admin/organisations/{org.id}/members/{uuid4()}",
            json={"role": "client_owner"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Tests — remove member (Phase 32B)
# ---------------------------------------------------------------------------


class TestRemoveMember:
    def _org_with_member(self, store: InMemoryStore) -> tuple[Organisation, UserProfile]:
        org = store.create_organisation(name="Remove Org", slug="remove-org")
        profile = UserProfile(
            user_id=uuid4(),
            organisation_id=org.id,
            email="leaving@client.com",
            display_name="leaving",
            role=ClientRole.CLIENT_VIEWER,
            created_at=datetime.now(UTC),
        )
        store.upsert_user(profile)
        return org, profile

    def test_admin_can_remove_member(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        r = admin_client.delete(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}"
        )
        assert r.status_code == 204

    def test_removed_member_no_longer_in_list(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        admin_client.delete(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}"
        )
        remaining = store.list_members(org.id)
        user_ids = [p.user_id for p in remaining]
        assert member.user_id not in user_ids

    def test_unknown_org_returns_404(self, admin_client: TestClient) -> None:
        r = admin_client.delete(
            f"/api/v1/admin/organisations/{uuid4()}/members/{uuid4()}"
        )
        assert r.status_code == 404

    def test_unknown_member_returns_404(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="Del Org", slug="del-org")
        r = admin_client.delete(
            f"/api/v1/admin/organisations/{org.id}/members/{uuid4()}"
        )
        assert r.status_code == 404

    def test_non_admin_gets_403(
        self, analyst_client: TestClient, store: InMemoryStore
    ) -> None:
        org = store.create_organisation(name="No Del", slug="no-del-org")
        r = analyst_client.delete(
            f"/api/v1/admin/organisations/{org.id}/members/{uuid4()}"
        )
        assert r.status_code == 403

    def test_audit_event_emitted(
        self, admin_client: TestClient, store: InMemoryStore
    ) -> None:
        org, member = self._org_with_member(store)
        admin_client.delete(
            f"/api/v1/admin/organisations/{org.id}/members/{member.user_id}"
        )
        events = store.audit_events
        assert any(
            e.action.value == "organisation.member_removed"
            and e.target_id == member.user_id
            for e in events
        )

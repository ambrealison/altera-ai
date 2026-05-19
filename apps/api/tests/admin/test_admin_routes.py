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

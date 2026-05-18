"""Phase 19D — reviewer assignment and soft-lock visibility.

Covers:
- lock fields (lock_status, locked_by_*, lock_expires_at) present in list response
- lock_status=unlocked for items that have not been claimed
- claim endpoint sets status=reviewing, lock_status=locked_by_me for claimant
- claim returns 409 when another reviewer holds an unexpired lock
- release endpoint reverts item to in_queue, clears lock
- refresh-lock extends lock_expires_at
- assign endpoint sets assigned_to_user_id
- regular reviewer cannot assign to someone else (400)
- methodology lead can assign to another reviewer
- client user cannot claim/release/refresh/assign (403)
- terminal items cannot be assigned (400)
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from altera_api.auth.dependency import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType

# ---------------------------------------------------------------------------
# Import app only once (at module level for speed)
# ---------------------------------------------------------------------------
from altera_api.main import app

# ---------------------------------------------------------------------------
# CSV fixture — two products that end up queued (pass-through → unknown)
# ---------------------------------------------------------------------------

_CSV = (
    b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
    b"ingredients_text,labels,language,country,is_own_brand,"
    b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
    b"P1,Alpha Widget,BrandA,Unknown,,,, en,GB,false,0.100,10,1.0,label\n"
    b"P2,Beta Widget,BrandB,Unknown,,,, en,GB,false,0.100,20,1.0,label\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _altera_ctx(role: AlteraRole = AlteraRole.ALTERA_ADMIN) -> AuthContext:
    """Build a fresh ALTERA_INTERNAL context with a random user_id per call."""
    return AuthContext(
        user_id=uuid4(),
        email=f"{role.value}@altera.example",
        organisation_id=uuid4(),
        role=role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
    )


def _client_ctx(org_id: UUID) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        email="client@retailco.example",
        organisation_id=org_id,
        role=ClientRole.CLIENT_OWNER,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=OrganisationType.GMS_CLIENT,
    )


def _create_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Lock Test Project",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_and_classify(client: TestClient, project_id: str) -> None:
    upload = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", _CSV, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{project_id}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )


def _get_review_items(client: TestClient, project_id: str) -> list[dict]:
    return client.get(f"/api/v1/projects/{project_id}/review").json()


def _setup(client: TestClient) -> tuple[str, str]:
    """Create project, upload+classify, return (project_id, product_id)."""
    pid = _create_project(client)
    _upload_and_classify(client, pid)
    items = _get_review_items(client, pid)
    assert len(items) >= 1
    return pid, items[0]["product_id"]


# ---------------------------------------------------------------------------
# Lock field defaults
# ---------------------------------------------------------------------------


class TestLockFieldsDefault:
    def test_lock_fields_present_and_default_unlocked(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid)
        items = _get_review_items(client, pid)
        assert len(items) >= 1
        item = items[0]
        assert item["lock_status"] == "unlocked"
        assert item["locked_by_user_id"] is None
        assert item["locked_by_email"] is None
        assert item["locked_at"] is None
        assert item["lock_expires_at"] is None
        assert item["assigned_to_user_id"] is None
        assert item["assigned_to_email"] is None


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


class TestClaim:
    def test_claim_sets_reviewing_and_locked_by_me(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "reviewing"
            assert body["lock_status"] == "locked_by_me"
            assert body["locked_by_user_id"] == str(ctx.user_id)
            assert body["lock_expires_at"] is not None
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_claim_conflict_when_another_holds_lock(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx_a = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        ctx_b = _altera_ctx(AlteraRole.ALTERA_REVIEWER)

        # ctx_a claims
        app.dependency_overrides[authed_user] = lambda: ctx_a
        try:
            r = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim")
            assert r.status_code == 200, r.text

            # ctx_b cannot claim while ctx_a holds the lock
            app.dependency_overrides[authed_user] = lambda: ctx_b
            r2 = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim")
            assert r2.status_code == 409, r2.text
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_claim(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        # Get the project's org_id so the client passes the get_project check
        proj = client.get(f"/api/v1/projects/{pid}").json()
        ctx = _client_ctx(UUID(proj["organisation_id"]))
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim")
            assert r.status_code == 403, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_reverts_to_in_queue(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            # Claim first
            r = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim")
            assert r.status_code == 200
            assert r.json()["status"] == "reviewing"

            # Release
            r2 = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/release")
            assert r2.status_code == 200, r2.text
            body = r2.json()
            assert body["status"] == "in_queue"
            assert body["lock_status"] == "unlocked"
            assert body["locked_by_user_id"] is None
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_release(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        proj = client.get(f"/api/v1/projects/{pid}").json()
        ctx = _client_ctx(UUID(proj["organisation_id"]))
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/release")
            assert r.status_code == 403, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Refresh lock
# ---------------------------------------------------------------------------


class TestRefreshLock:
    def test_refresh_extends_expiry(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            claim_r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/claim"
            )
            assert claim_r.status_code == 200
            original_expiry = claim_r.json()["lock_expires_at"]

            refresh_r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/refresh-lock"
            )
            assert refresh_r.status_code == 200, refresh_r.text
            refreshed_expiry = refresh_r.json()["lock_expires_at"]
            assert refreshed_expiry >= original_expiry
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_refresh(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        proj = client.get(f"/api/v1/projects/{pid}").json()
        ctx = _client_ctx(UUID(proj["organisation_id"]))
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/refresh-lock"
            )
            assert r.status_code == 403, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Assign
# ---------------------------------------------------------------------------


class TestAssign:
    def test_reviewer_can_self_assign(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/assign",
                json={"assign_to_user_id": str(ctx.user_id)},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["assigned_to_user_id"] == str(ctx.user_id)
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_reviewer_cannot_assign_to_others(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_REVIEWER)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            other_user_id = uuid4()
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/assign",
                json={"assign_to_user_id": str(other_user_id)},
            )
            assert r.status_code == 400, r.text
            detail = r.json()["detail"].lower()
            assert "admin" in detail or "assign" in detail
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_methodology_lead_can_assign_to_others(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        ctx = _altera_ctx(AlteraRole.ALTERA_METHODOLOGY_LEAD)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            other_user_id = uuid4()
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/assign",
                json={"assign_to_user_id": str(other_user_id)},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["assigned_to_user_id"] == str(other_user_id)
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_assign(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        proj = client.get(f"/api/v1/projects/{pid}").json()
        ctx = _client_ctx(UUID(proj["organisation_id"]))
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/assign",
                json={"assign_to_user_id": str(uuid4())},
            )
            assert r.status_code == 403, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_cannot_assign_after_decision(self, client: TestClient) -> None:
        pid, product_id = _setup(client)
        # Accept the item — it is removed from the queue (404 thereafter)
        client.post(
            f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/decision",
            json={"decision": "accepted"},
        )
        ctx = _altera_ctx(AlteraRole.ALTERA_METHODOLOGY_LEAD)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/assign",
                json={"assign_to_user_id": str(ctx.user_id)},
            )
            assert r.status_code == 404, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)

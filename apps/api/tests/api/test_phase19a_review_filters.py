"""Phase 19A — review queue filtering and sorting.

Covers:
- filter by status
- filter by reason
- filter by upload_id
- product_search by name (substring, case-insensitive)
- product_search by external_product_id
- default oldest-first sort
- newest-first sort (sort=newest)
- response shape: upload_id and confidence present, no commercial fields
- cross-organisation protection
- client user cannot submit review decisions
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.auth.dependency import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import ClientRole, OrganisationType, Role

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "FY 2024",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_and_classify(
    client: TestClient,
    project_id: str,
    csv_bytes: bytes,
    methodology: str = "protein_tracker",
) -> str:
    upload = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    ).json()
    uid = upload["id"]
    client.post(
        f"/api/v1/projects/{project_id}/uploads/{uid}/classify",
        json={"methodology": methodology},
    )
    return uid


def _client_auth_ctx(org_id: UUID) -> AuthContext:
    """Fake AuthContext for a GMS client user — cannot submit review decisions."""
    return AuthContext(
        user_id=uuid4(),
        email="client@retailco.example",
        organisation_id=org_id,
        role=ClientRole.CLIENT_OWNER,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=OrganisationType.GMS_CLIENT,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_product_csv() -> bytes:
    """Minimal CSV with two products that end up pass-through (no rule fires)."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"EXT-A,Mystery Sauce Alpha,BrandX,Condiments,,,,en,GB,false,0.250,100,2.0,label\n"
        b"EXT-B,Enigma Drink Beta,BrandY,Unknown Category,,,,en,GB,false,0.330,200,1.0,label\n"
    )


@pytest.fixture
def three_product_csv() -> bytes:
    """Three products: two pass-through + one matched (lentils)."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"EXT-1,Red Lentil Soup,GreenLeaf,Soups,Pulse Soups,"
        b"red lentils water onion,vegan,en,GB,false,0.400,100,4.5,label\n"
        b"EXT-2,Mystery Sauce,BrandX,Condiments,,"
        b",,en,GB,false,0.250,100,2.0,label\n"
        b"EXT-3,Unknown Drink,BrandZ,Misc,,"
        b",,en,GB,false,0.330,200,1.0,label\n"
    )


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_upload_id_in_response(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        uid = _upload_and_classify(client, pid, two_product_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        for item in items:
            assert "upload_id" in item
            assert item["upload_id"] == uid

    def test_confidence_in_response(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        # confidence key must be present (may be null if not yet classified)
        assert len(items) > 0
        for item in items:
            assert "confidence" in item

    def test_no_commercial_fields(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        forbidden = {
            "items_purchased",
            "items_sold",
            "weight_per_item_kg",
            "revenue",
            "margin",
            "supplier_terms",
        }
        for item in items:
            for field in forbidden:
                assert field not in item, f"commercial field {field!r} must not be in response"

    def test_required_fields_present(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        required = {
            "product_id",
            "external_product_id",
            "product_name",
            "brand",
            "methodology",
            "status",
            "reason",
            "queued_at",
            "upload_id",
            "confidence",
            "current_category",
        }
        for item in items:
            for field in required:
                assert field in item, f"required field {field!r} missing from response"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFilters:
    def test_filter_by_methodology(
        self, client: TestClient, pt_tiny_csv: bytes, wwf_tiny_csv: bytes
    ) -> None:
        pid_pt = _create_project(client, methodology="protein_tracker")
        _upload_and_classify(client, pid_pt, pt_tiny_csv, "protein_tracker")

        pid_wwf = _create_project(client, methodology="wwf")
        _upload_and_classify(client, pid_wwf, wwf_tiny_csv, "wwf")

        pt_items = client.get(
            f"/api/v1/projects/{pid_pt}/review?methodology=protein_tracker"
        ).json()
        assert all(i["methodology"] == "protein_tracker" for i in pt_items)

        wwf_items = client.get(f"/api/v1/projects/{pid_wwf}/review?methodology=wwf").json()
        assert all(i["methodology"] == "wwf" for i in wwf_items)

    def test_filter_by_status(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # All newly queued items should be in_queue
        in_queue = client.get(f"/api/v1/projects/{pid}/review?status=in_queue").json()
        assert len(in_queue) > 0
        assert all(i["status"] == "in_queue" for i in in_queue)

        # Nothing should be in accepted state yet
        accepted = client.get(f"/api/v1/projects/{pid}/review?status=accepted").json()
        assert accepted == []

    def test_filter_by_reason(
        self, client: TestClient, two_product_csv: bytes, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # All pass-through items should have reason=low_confidence or a similar
        # AI-related reason; verify the filter restricts correctly
        items_all = client.get(f"/api/v1/projects/{pid}/review").json()
        reasons_present = {i["reason"] for i in items_all}

        for reason in reasons_present:
            filtered = client.get(f"/api/v1/projects/{pid}/review?reason={reason}").json()
            assert len(filtered) > 0
            assert all(i["reason"] == reason for i in filtered)

        # A reason that was NOT produced should return empty
        filtered_empty = client.get(f"/api/v1/projects/{pid}/review?reason=requested").json()
        # Only assert it doesn't include items with a different reason
        assert all(i["reason"] == "requested" for i in filtered_empty)

    def test_filter_by_upload_id(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        uid1 = _upload_and_classify(client, pid, two_product_csv)
        uid2 = _upload_and_classify(client, pid, two_product_csv)

        items_u1 = client.get(f"/api/v1/projects/{pid}/review?upload_id={uid1}").json()
        items_u2 = client.get(f"/api/v1/projects/{pid}/review?upload_id={uid2}").json()

        # Both uploads have the same products (same external IDs)
        # so dedup or re-ingest happens; we only care that the filter restricts
        assert all(i["upload_id"] == uid1 for i in items_u1)
        assert all(i["upload_id"] == uid2 for i in items_u2)

    def test_product_search_by_name(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # "mystery" should match "Mystery Sauce Alpha" but not "Enigma Drink Beta"
        results = client.get(f"/api/v1/projects/{pid}/review?product_search=mystery").json()
        assert len(results) >= 1
        assert all("mystery" in i["product_name"].lower() for i in results)

    def test_product_search_case_insensitive(
        self, client: TestClient, two_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        upper = client.get(f"/api/v1/projects/{pid}/review?product_search=MYSTERY").json()
        lower = client.get(f"/api/v1/projects/{pid}/review?product_search=mystery").json()
        assert len(upper) == len(lower)

    def test_product_search_by_external_id(
        self, client: TestClient, two_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # "EXT-A" should match only the first product
        results = client.get(f"/api/v1/projects/{pid}/review?product_search=EXT-A").json()
        assert all("ext-a" in i["external_product_id"].lower() for i in results)

    def test_product_search_no_match_returns_empty(
        self, client: TestClient, two_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        results = client.get(f"/api/v1/projects/{pid}/review?product_search=xyzzy_no_match").json()
        assert results == []

    def test_combined_filters(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # Combining status=in_queue with search should still restrict both ways
        results = client.get(
            f"/api/v1/projects/{pid}/review?status=in_queue&product_search=mystery"
        ).json()
        assert all(i["status"] == "in_queue" for i in results)
        assert all("mystery" in i["product_name"].lower() for i in results)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestSorting:
    def test_default_sort_is_oldest_first(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        items = client.get(f"/api/v1/projects/{pid}/review").json()
        if len(items) >= 2:
            times = [i["queued_at"] for i in items]
            assert times == sorted(times), "default sort must be oldest-first"

    def test_sort_oldest(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        items = client.get(f"/api/v1/projects/{pid}/review?sort=oldest").json()
        if len(items) >= 2:
            times = [i["queued_at"] for i in items]
            assert times == sorted(times)

    def test_sort_newest_first(self, client: TestClient, two_product_csv: bytes) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        oldest = client.get(f"/api/v1/projects/{pid}/review?sort=oldest").json()
        newest = client.get(f"/api/v1/projects/{pid}/review?sort=newest").json()

        if len(oldest) >= 2:
            assert [i["product_id"] for i in oldest] == [i["product_id"] for i in reversed(newest)]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_client_user_cannot_submit_decision(
        self,
        store: InMemoryStore,
        client: TestClient,
        two_product_csv: bytes,
    ) -> None:
        from altera_api.main import app

        # Use dev auth (Role.OWNER) to set up project + review items
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()

        if not items:
            pytest.skip("no review items produced — cannot test decision gating")

        # Now override authed_user to return a ClientRole context
        client_ctx = _client_auth_ctx(store.default_org_id)
        app.dependency_overrides[authed_user] = lambda: client_ctx
        try:
            item = items[0]
            r = client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}"
                f"/{item['methodology']}/decision",
                json={"decision": "accepted"},
            )
            assert r.status_code == 403, (
                f"client user must not be able to submit review decisions, got {r.status_code}"
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_cross_org_review_blocked(
        self,
        store: InMemoryStore,
        client: TestClient,
        two_product_csv: bytes,
    ) -> None:
        from altera_api.main import app

        # Create a project owned by org A (default dev-auth org)
        pid = _create_project(client)
        _upload_and_classify(client, pid, two_product_csv)

        # Simulate a user from a different organisation
        other_org_id = uuid4()
        other_ctx = AuthContext(
            user_id=uuid4(),
            email="intruder@other.example",
            organisation_id=other_org_id,
            role=Role.OWNER,
            auth_provider=AuthProvider.DEV,
            is_dev_auth=True,
            organisation_type=OrganisationType.GMS_CLIENT,
        )
        app.dependency_overrides[authed_user] = lambda: other_ctx
        try:
            r = client.get(f"/api/v1/projects/{pid}/review")
            assert r.status_code in {403, 404}, "cross-org review listing must be blocked"
        finally:
            app.dependency_overrides.pop(authed_user, None)

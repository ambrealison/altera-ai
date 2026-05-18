"""Phase 19C — bulk review actions.

Covers:
- bulk_accept multiple PT items
- bulk_defer multiple items
- bulk_change_pt_group
- each item creates an individual decision record
- bulk audit event emitted
- client user cannot bulk review (403)
- cross-organisation access blocked
- mixed methodology rejects bulk_change_pt_group
- invalid product ID rejects whole batch (all-or-nothing)
- terminal items rejected
- max batch size enforced
- response shape: action, requested_count, updated_count, decision_ids
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
# CSV fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_product_csv() -> bytes:
    """Four pass-through products → all queued as 'requested'."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"P1,Alpha Widget,BrandA,Unknown,,,, en,GB,false,0.100,10,1.0,label\n"
        b"P2,Beta Widget,BrandB,Unknown,,,, en,GB,false,0.100,20,1.0,label\n"
        b"P3,Gamma Widget,BrandC,Unknown,,,, en,GB,false,0.100,30,1.0,label\n"
        b"P4,Delta Widget,BrandD,Unknown,,,, en,GB,false,0.100,40,1.0,label\n"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Bulk Test Project",
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


def _get_review_product_ids(client: TestClient, project_id: str) -> list[str]:
    items = client.get(f"/api/v1/projects/{project_id}/review").json()
    return [i["product_id"] for i in items]


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


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class TestResponseShape:
    def test_bulk_accept_response_shape(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 2

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids[:2],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["action"] == "bulk_accept"
        assert body["requested_count"] == 2
        assert body["updated_count"] == 2
        assert len(body["decision_ids"]) == 2
        # decision_ids should be valid UUID strings
        for did in body["decision_ids"]:
            UUID(did)


# ---------------------------------------------------------------------------
# Bulk accept
# ---------------------------------------------------------------------------

class TestBulkAccept:
    def test_bulk_accept_removes_items_from_queue(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 2

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids[:2],
            },
        )
        assert r.status_code == 200, r.text

        remaining = _get_review_product_ids(client, pid)
        for accepted_id in ids[:2]:
            assert accepted_id not in remaining

    def test_bulk_accept_all_items(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) > 0

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated_count"] == len(ids)
        assert _get_review_product_ids(client, pid) == []

    def test_bulk_accept_creates_decision_records(
        self, client: TestClient, store: InMemoryStore, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        decision_count_before = len(store.review_decisions)

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids,
            },
        )
        assert r.status_code == 200, r.text
        assert len(store.review_decisions) == decision_count_before + len(ids)

    def test_bulk_accept_emits_audit_events(
        self, client: TestClient, store: InMemoryStore, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        audit_count_before = len(store.audit_events)

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids,
            },
        )
        assert r.status_code == 200, r.text
        # Expect N per-item events + 1 bulk-level event
        new_events = store.audit_events[audit_count_before:]
        bulk_events = [e for e in new_events if e.action.value == "review.bulk_action"]
        item_events = [e for e in new_events if e.action.value == "review.decision_made"]
        assert len(bulk_events) == 1
        assert len(item_events) == len(ids)


# ---------------------------------------------------------------------------
# Bulk defer
# ---------------------------------------------------------------------------

class TestBulkDefer:
    def test_bulk_defer_keeps_items_in_queue_as_deferred(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 2

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_defer",
                "methodology": "protein_tracker",
                "product_ids": ids[:2],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated_count"] == 2

        # Deferred items should still be in queue but not in in_queue state
        all_items = client.get(f"/api/v1/projects/{pid}/review").json()
        deferred = [i for i in all_items if i["status"] == "deferred"]
        deferred_ids = {i["product_id"] for i in deferred}
        for pid_item in ids[:2]:
            assert pid_item in deferred_ids


# ---------------------------------------------------------------------------
# Bulk change PT group
# ---------------------------------------------------------------------------

class TestBulkChangePtGroup:
    def test_bulk_change_pt_group_updates_classification(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 2

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_change_pt_group",
                "methodology": "protein_tracker",
                "product_ids": ids[:2],
                "to_pt_group": "plant_based_core",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated_count"] == 2

    def test_bulk_change_pt_group_requires_to_pt_group(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_change_pt_group",
                "methodology": "protein_tracker",
                "product_ids": ids[:1],
            },
        )
        assert r.status_code == 400

    def test_bulk_change_pt_group_rejects_system_states(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)

        for system_state in ("unknown", "out_of_scope"):
            r = client.post(
                f"/api/v1/projects/{pid}/review/bulk-action",
                json={
                    "action": "bulk_change_pt_group",
                    "methodology": "protein_tracker",
                    "product_ids": ids[:1],
                    "to_pt_group": system_state,
                },
            )
            assert r.status_code == 400, f"{system_state!r} should be rejected"

    def test_bulk_change_pt_group_rejects_wwf_methodology(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client, methodology="protein_tracker")
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_change_pt_group",
                "methodology": "wwf",
                "product_ids": ids[:1],
                "to_pt_group": "plant_based_core",
            },
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Validation — all-or-nothing
# ---------------------------------------------------------------------------

class TestValidation:
    def test_invalid_product_id_rejects_whole_batch(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 1

        bad_id = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids[:1] + [bad_id],
            },
        )
        assert r.status_code == 400

        # Valid item must NOT have been updated
        remaining = _get_review_product_ids(client, pid)
        assert ids[0] in remaining

    def test_terminal_items_rejected(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)
        assert len(ids) >= 2

        # Accept one item first
        client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids[:1],
            },
        )

        # Now try to include the already-accepted item in a second bulk action
        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": ids[:2],
            },
        )
        # Accepted items are removed from the queue, so the second attempt
        # fails with "not found" (which is still a 400 all-or-nothing failure).
        assert r.status_code == 400

    def test_max_batch_size_enforced(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)

        fake_ids = [str(uuid4()) for _ in range(101)]
        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": fake_ids,
            },
        )
        assert r.status_code == 422  # Pydantic max_length validation

    def test_empty_product_ids_rejected(
        self, client: TestClient, multi_product_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)

        r = client.post(
            f"/api/v1/projects/{pid}/review/bulk-action",
            json={
                "action": "bulk_accept",
                "methodology": "protein_tracker",
                "product_ids": [],
            },
        )
        assert r.status_code == 422  # Pydantic min_length validation


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_client_user_cannot_bulk_review(
        self,
        store: InMemoryStore,
        client: TestClient,
        multi_product_csv: bytes,
    ) -> None:
        from altera_api.main import app

        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)

        ctx = _client_ctx(store.default_org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/bulk-action",
                json={
                    "action": "bulk_accept",
                    "methodology": "protein_tracker",
                    "product_ids": ids[:1],
                },
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_cross_org_bulk_action_blocked(
        self,
        store: InMemoryStore,
        client: TestClient,
        multi_product_csv: bytes,
    ) -> None:
        from altera_api.main import app

        pid = _create_project(client)
        _upload_and_classify(client, pid, multi_product_csv)
        ids = _get_review_product_ids(client, pid)

        other_ctx = AuthContext(
            user_id=uuid4(),
            email="intruder@other.example",
            organisation_id=uuid4(),
            role=Role.OWNER,
            auth_provider=AuthProvider.DEV,
            is_dev_auth=True,
            organisation_type=OrganisationType.GMS_CLIENT,
        )
        app.dependency_overrides[authed_user] = lambda: other_ctx
        try:
            r = client.post(
                f"/api/v1/projects/{pid}/review/bulk-action",
                json={
                    "action": "bulk_accept",
                    "methodology": "protein_tracker",
                    "product_ids": ids[:1],
                },
            )
            assert r.status_code in {400, 403, 404}
        finally:
            app.dependency_overrides.pop(authed_user, None)

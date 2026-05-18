"""Phase 19E — review prioritisation foundation.

Covers:
- contradiction_detected queues as critical priority
- rule_collision queues as high priority
- low_confidence queues as medium priority
- requested queues as low priority
- ai_parse_failed queues as critical priority
- filter by priority_level returns only matching items
- sort=priority returns critical before high before medium before low
- priority fields present in response
- no commercial fields (items_purchased, revenue, etc.) in response
- pure assign_priority function covers all queue reasons
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from altera_api.domain.review import ManualReviewPriority, ManualReviewQueueReason
from altera_api.review.priority import assign_priority, priority_weight

# ---------------------------------------------------------------------------
# Unit tests — pure priority logic
# ---------------------------------------------------------------------------


class TestAssignPriority:
    def test_contradiction_detected_is_critical(self) -> None:
        prio, reasons = assign_priority(ManualReviewQueueReason.CONTRADICTION_DETECTED)
        assert prio is ManualReviewPriority.CRITICAL
        assert "contradiction_detected" in reasons

    def test_ai_parse_failed_is_critical(self) -> None:
        prio, _ = assign_priority(ManualReviewQueueReason.AI_PARSE_FAILED)
        assert prio is ManualReviewPriority.CRITICAL

    def test_ai_provider_error_is_critical(self) -> None:
        prio, _ = assign_priority(ManualReviewQueueReason.AI_PROVIDER_ERROR)
        assert prio is ManualReviewPriority.CRITICAL

    def test_rule_collision_is_high(self) -> None:
        prio, reasons = assign_priority(ManualReviewQueueReason.RULE_COLLISION)
        assert prio is ManualReviewPriority.HIGH
        assert "rule_collision" in reasons

    def test_low_confidence_is_medium(self) -> None:
        prio, reasons = assign_priority(ManualReviewQueueReason.LOW_CONFIDENCE)
        assert prio is ManualReviewPriority.MEDIUM
        assert "low_confidence" in reasons

    def test_requested_is_low(self) -> None:
        prio, _ = assign_priority(ManualReviewQueueReason.REQUESTED)
        assert prio is ManualReviewPriority.LOW

    def test_all_reasons_covered(self) -> None:
        for reason in ManualReviewQueueReason:
            prio, _ = assign_priority(reason)
            assert isinstance(prio, ManualReviewPriority)

    def test_priority_weight_ordering(self) -> None:
        assert (
            priority_weight(ManualReviewPriority.CRITICAL)
            > priority_weight(ManualReviewPriority.HIGH)
            > priority_weight(ManualReviewPriority.MEDIUM)
            > priority_weight(ManualReviewPriority.LOW)
        )


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

_PASS_THROUGH_CSV = (
    b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
    b"ingredients_text,labels,language,country,is_own_brand,"
    b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
    b"P1,Alpha Widget,BrandA,Unknown,,,, en,GB,false,0.100,10,1.0,label\n"
    b"P2,Beta Widget,BrandB,Unknown,,,, en,GB,false,0.100,20,1.0,label\n"
    b"P3,Gamma Widget,BrandC,Unknown,,,, en,GB,false,0.100,30,1.0,label\n"
)

# A CSV that triggers a contradiction (vegan label + chicken ingredient)
_CONTRADICTION_CSV = (
    b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
    b"ingredients_text,labels,language,country,is_own_brand,"
    b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
    b"CONTRA1,Vegan Chicken Burger,BrandX,Meat,,chicken breast,vegan,en,GB,false,0.200,5,25.0,label\n"
)


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Priority Test Project",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_and_classify(
    client: TestClient, project_id: str, csv_bytes: bytes, methodology: str = "protein_tracker"
) -> None:
    upload = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{project_id}/uploads/{upload['id']}/classify",
        json={"methodology": methodology},
    )


def _list_review(client: TestClient, project_id: str, **params: str) -> list[dict]:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/v1/projects/{project_id}/review"
    if q:
        url += f"?{q}"
    return client.get(url).json()


# ---------------------------------------------------------------------------
# Priority in API responses
# ---------------------------------------------------------------------------


class TestPriorityInResponse:
    def test_priority_fields_present(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        items = _list_review(client, pid)
        assert len(items) >= 1
        item = items[0]
        assert "priority_level" in item
        assert "priority_reasons" in item
        assert item["priority_level"] in ("low", "medium", "high", "critical")
        assert isinstance(item["priority_reasons"], list)

    def test_pass_through_items_are_low_priority(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        items = _list_review(client, pid)
        # Pass-through products end up with reason=REQUESTED
        for item in items:
            assert item["priority_level"] == "low"

    def test_contradiction_items_are_critical(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _CONTRADICTION_CSV)
        items = _list_review(client, pid)
        assert len(items) >= 1
        critical = [i for i in items if i["reason"] == "contradiction_detected"]
        assert len(critical) >= 1
        for item in critical:
            assert item["priority_level"] == "critical"
            assert "contradiction_detected" in item["priority_reasons"]

    def test_no_commercial_fields_in_response(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        items = _list_review(client, pid)
        assert len(items) >= 1
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
                assert field not in item, f"commercial field '{field}' exposed in review response"


# ---------------------------------------------------------------------------
# Filter by priority_level
# ---------------------------------------------------------------------------


class TestPriorityFilter:
    def test_filter_low_returns_only_low(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        items_low = _list_review(client, pid, priority_level="low")
        assert len(items_low) >= 1
        for item in items_low:
            assert item["priority_level"] == "low"

    def test_filter_critical_returns_contradiction(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _CONTRADICTION_CSV)
        critical = _list_review(client, pid, priority_level="critical")
        assert len(critical) >= 1
        for item in critical:
            assert item["priority_level"] == "critical"

    def test_filter_high_excludes_low_and_critical(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        _upload_and_classify(client, pid, _CONTRADICTION_CSV)
        high_items = _list_review(client, pid, priority_level="high")
        for item in high_items:
            assert item["priority_level"] == "high"

    def test_filter_critical_excludes_low(self, client: TestClient) -> None:
        pid = _create_project(client)
        # Queue both low (pass-through) and critical (contradiction) items
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        _upload_and_classify(client, pid, _CONTRADICTION_CSV)
        critical = _list_review(client, pid, priority_level="critical")
        assert all(i["priority_level"] == "critical" for i in critical)
        low_ids = {i["product_id"] for i in _list_review(client, pid, priority_level="low")}
        critical_ids = {i["product_id"] for i in critical}
        assert low_ids.isdisjoint(critical_ids)


# ---------------------------------------------------------------------------
# Sort by priority
# ---------------------------------------------------------------------------


class TestPrioritySort:
    def test_sort_priority_puts_critical_first(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)  # low
        _upload_and_classify(client, pid, _CONTRADICTION_CSV)  # critical
        items = _list_review(client, pid, sort="priority")
        assert len(items) >= 2
        priority_order = [i["priority_level"] for i in items]
        # Critical must appear before low
        critical_indices = [i for i, p in enumerate(priority_order) if p == "critical"]
        low_indices = [i for i, p in enumerate(priority_order) if p == "low"]
        if critical_indices and low_indices:
            assert max(critical_indices) < min(low_indices)

    def test_sort_priority_stable_within_same_level(self, client: TestClient) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, _PASS_THROUGH_CSV)
        by_priority = _list_review(client, pid, sort="priority")
        by_oldest = _list_review(client, pid, sort="oldest")
        # When all items share the same priority, order should match oldest-first
        if all(i["priority_level"] == by_priority[0]["priority_level"] for i in by_priority):
            assert [i["product_id"] for i in by_priority] == [i["product_id"] for i in by_oldest]

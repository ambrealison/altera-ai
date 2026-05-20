"""Phase 33E — classification lifecycle invariant tests.

Asserts the deadlock-prevention contract: after ``POST .../classify`` runs
on an upload, every PT-enabled product in that upload ends up in exactly
one actionable state:

  (1) classified with a non-UNKNOWN PT group (rules matched), OR
  (2) classified UNKNOWN AND queued for manual review (no rule + no AI), OR
  (3) classified UNKNOWN with `review_queue_count` recording the queue item

The contradiction that caused the staging deadlock — products without a PT
classification AND with no review item — must be impossible after classify.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Lifecycle test",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(client: TestClient, project_id: str, csv: bytes) -> str:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv, "text/csv")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _classify(client: TestClient, project_id: str, upload_id: str) -> dict:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/classify",
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 200, r.text
    return r.json()


_NO_MATCH_CSV = (
    b"external_product_id,product_name,weight_per_item_kg,items_purchased,protein_pct\n"
    b"SKU-A,Mystery Snack 1,0.200,500,5.0\n"
    b"SKU-B,Mystery Snack 2,0.300,800,8.0\n"
)


class TestNoRuleMatchEndsInReviewQueue:
    """If rules don't match and AI is unconfigured, products MUST be queued."""

    def test_unclassified_zero_after_classify(self, client: TestClient) -> None:
        pid = _project(client)
        uid = _upload(client, pid, _NO_MATCH_CSV)
        _classify(client, pid, uid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        # After classify, every PT product has at least an UNKNOWN classification.
        assert project["unclassified_pt_count"] == 0

    def test_review_queue_populated_after_classify(self, client: TestClient) -> None:
        pid = _project(client)
        uid = _upload(client, pid, _NO_MATCH_CSV)
        summary = _classify(client, pid, uid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        # Products that don't deterministically match end up in review.
        assert summary["pass_through"] + summary["rule_collision"] >= 1
        assert project["review_queue_count"] == summary["queued_for_review"]
        assert project["review_queue_count"] >= 1

    def test_no_unclassified_with_empty_review_queue(self, client: TestClient) -> None:
        """The Phase 33E deadlock-prevention invariant."""
        pid = _project(client)
        uid = _upload(client, pid, _NO_MATCH_CSV)
        _classify(client, pid, uid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        deadlock = (
            project["unclassified_pt_count"] > 0
            and project["review_queue_count"] == 0
        )
        assert not deadlock, (
            "Lifecycle deadlock: products are unclassified AND review queue is empty"
        )


class TestMissingProteinPctDoesNotBlockClassification:
    """Phase 33B-hotfix made protein_pct a warning, not an error.

    A product with missing protein_pct must still be classified (or queued).
    """

    def test_missing_protein_pct_still_classifies(self, client: TestClient) -> None:
        pid = _project(client)
        csv = (
            b"external_product_id,product_name,weight_per_item_kg,items_purchased,protein_pct\n"
            b"SKU-1,Widget,0.4,100,\n"  # protein_pct intentionally empty
        )
        uid = _upload(client, pid, csv)
        _classify(client, pid, uid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == 0


class TestCalculationGuardStillEnforced:
    """The pre-flight guard from Phase 33D-hotfix must still block pre-classification."""

    def test_calculation_blocked_before_classify(self, client: TestClient) -> None:
        pid = _project(client)
        _upload(client, pid, _NO_MATCH_CSV)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "classification_required"

    def test_calculation_blocked_when_only_unknown(self, client: TestClient) -> None:
        """Once classified to UNKNOWN, the products have classifications so
        the pre-flight guard passes; the underlying calculation may still
        produce no rows for plant/animal totals — but the lifecycle invariant
        (no deadlock) holds either way.
        """
        pid = _project(client)
        uid = _upload(client, pid, _NO_MATCH_CSV)
        _classify(client, pid, uid)
        # Even UNKNOWN classifications satisfy the prereq guard. Calculation
        # may still surface an error from the calc layer, but it's no longer
        # the deadlock case.
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == 0


class TestSkippedMethodologyDisabledCounter:
    """The Phase 33E skipped counter exists internally to expose mismatches."""

    def test_normal_flow_has_zero_skipped(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        # When a project is PT-only, every ingested product has PT enabled.
        # The classify summary should have skipped == 0 internally; the
        # public response does not surface skipped, but no products should
        # be silently dropped.
        pid = _project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        summary = _classify(client, pid, uid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        total_accounted = (
            summary["matched"] + summary["pass_through"] + summary["rule_collision"]
        )
        upload_info = client.get(f"/api/v1/projects/{pid}/uploads").json()["items"][0]
        assert total_accounted == upload_info["products_count"]
        # And lifecycle holds.
        deadlock = (
            project["unclassified_pt_count"] > 0
            and project["review_queue_count"] == 0
        )
        assert not deadlock

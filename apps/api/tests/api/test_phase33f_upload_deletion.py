"""Phase 33F — upload deletion endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Cleanup test",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    return r.json()["id"]


class TestDeleteUploadBasic:
    def test_delete_returns_204(self, client: TestClient, pt_tiny_csv: bytes) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        r = client.delete(f"/api/v1/projects/{pid}/uploads/{upload['id']}")
        assert r.status_code == 204, r.text

    def test_delete_unknown_upload_returns_404(self, client: TestClient) -> None:
        from uuid import uuid4

        pid = _create_project(client)
        r = client.delete(f"/api/v1/projects/{pid}/uploads/{uuid4()}")
        assert r.status_code == 404

    def test_delete_validation_failed_upload(self, client: TestClient) -> None:
        pid = _create_project(client)
        # CSV with a missing required field — produces validation_failed.
        bad = b"external_product_id,product_name\nSKU,Widget\n"
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("bad.csv", bad, "text/csv")},
        ).json()
        # Whatever status the upload ends in, delete must succeed.
        r = client.delete(f"/api/v1/projects/{pid}/uploads/{upload['id']}")
        assert r.status_code == 204


class TestDeleteRemovesProductsAndStats:
    def test_products_removed_from_project_after_delete(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        before = client.get(f"/api/v1/projects/{pid}").json()
        assert before["unclassified_pt_count"] > 0
        client.delete(f"/api/v1/projects/{pid}/uploads/{upload['id']}")
        after = client.get(f"/api/v1/projects/{pid}").json()
        assert after["unclassified_pt_count"] == 0
        assert after["upload_count"] == 0

    def test_deleted_upload_products_excluded_from_calculation(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        # Delete before any review.
        client.delete(f"/api/v1/projects/{pid}/uploads/{upload['id']}")
        # Run calculation — there are now zero PT products; the prereq
        # guard sees nothing to classify, so calculation proceeds.
        r = client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        assert r.status_code == 201
        assert r.json()["rows_count"] == 0

    def test_review_items_cleared_after_delete(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        # Classify to populate review queue.
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        before = client.get(f"/api/v1/projects/{pid}").json()
        # Some products will be queued or UNKNOWN-classified.
        client.delete(f"/api/v1/projects/{pid}/uploads/{upload['id']}")
        after = client.get(f"/api/v1/projects/{pid}").json()
        # Review queue should drain along with the upload.
        assert after["review_queue_count"] == 0
        assert after["upload_count"] == 0
        assert before["upload_count"] == 1


class TestCrossProjectIsolation:
    def test_cannot_delete_upload_from_other_project(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid_a = _create_project(client)
        pid_b = _create_project(client)
        upload_a = client.post(
            f"/api/v1/projects/{pid_a}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        # Attempt to delete A's upload via project B's path.
        r = client.delete(f"/api/v1/projects/{pid_b}/uploads/{upload_a['id']}")
        assert r.status_code == 404
        # Confirm upload A still exists.
        r2 = client.get(f"/api/v1/projects/{pid_a}/uploads/{upload_a['id']}")
        assert r2.status_code == 200

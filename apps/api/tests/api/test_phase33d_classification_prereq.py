"""Phase 33D-hotfix — classification prerequisite UX tests.

Covers:
- POST /runs returns structured 400 when products lack PT classification
- Structured error includes error_code="classification_required" and unclassified_count
- GET /projects/{id} includes unclassified_pt_count
- unclassified_pt_count drops to 0 after full classification + review
- Run succeeds after all products are classified
- Client cannot bypass the guard by calling POST /runs directly
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Test Project",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(client: TestClient, project_id: str, csv_bytes: bytes) -> str:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _classify(client: TestClient, project_id: str, upload_id: str) -> None:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads/{upload_id}/classify",
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 200, r.text


def _review_all(client: TestClient, project_id: str) -> None:
    queue = client.get(f"/api/v1/projects/{project_id}/review").json()["items"]
    for item in queue:
        client.post(
            f"/api/v1/projects/{project_id}/review/{item['product_id']}/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )


class TestRunRequiresClassification:
    def test_run_without_classification_returns_400(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 400, r.text

    def test_run_error_has_structured_error_code(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error_code"] == "classification_required"

    def test_run_error_includes_unclassified_count(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["unclassified_count"] > 0

    def test_run_error_message_is_human_readable(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        detail = r.json()["detail"]
        assert "classification" in detail["message"].lower()


class TestProjectUnclassifiedCount:
    def test_project_has_unclassified_pt_count_field(self, client: TestClient) -> None:
        pid = _create_project(client)
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert "unclassified_pt_count" in project

    def test_unclassified_pt_count_zero_for_empty_project(self, client: TestClient) -> None:
        pid = _create_project(client)
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == 0

    def test_unclassified_pt_count_equals_product_count_before_classification(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        assert upload["products_count"] > 0
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == upload["products_count"]

    def test_unclassified_pt_count_drops_after_classification(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        _classify(client, pid, uid)
        _review_all(client, pid)
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == 0

    def test_unclassified_pt_count_zero_for_wwf_only_project(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodology="wwf")
        project = client.get(f"/api/v1/projects/{pid}").json()
        assert project["unclassified_pt_count"] == 0


class TestRunSucceedsAfterClassification:
    def test_run_succeeds_after_full_classify_and_review(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        _classify(client, pid, uid)
        _review_all(client, pid)
        r = client.post(f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"})
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["methodology"] == "protein_tracker"
        assert run["rows_count"] > 0

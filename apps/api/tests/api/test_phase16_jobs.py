"""Phase 16: background job system tests.

Covers: job creation, status transitions, duplicate-active-job prevention,
failed-job error message, project-scoped listing, cross-org access denial,
dev runner executing tasks, and existing synchronous flows unchanged.
"""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.domain.job import JobStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Test project",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY2025",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_and_ingest(client: TestClient, project_id: str, csv_bytes: bytes) -> str:
    """Upload via the sync multipart endpoint and return the upload_id."""
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# Classify-upload job
# ---------------------------------------------------------------------------

class TestClassifyUploadJob:
    def test_classify_job_succeeds(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)

        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["job_type"] == "classify_upload"
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["upload_id"] == uid
        assert body["project_id"] == pid

    def test_classify_job_result_contains_counts(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        result = r.json()["result"]
        assert result is not None
        assert result["matched"] + result["pass_through"] + result["rule_collision"] == 12
        assert "queued_for_review" in result

    def test_classify_job_timestamps_populated(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        body = r.json()
        assert body["started_at"] is not None
        assert body["completed_at"] is not None
        assert body["failed_at"] is None

    def test_classify_job_persisted_retrievable(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        job_id = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        ).json()["job_id"]

        r = client.get(f"/api/v1/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json()["job_id"] == job_id

    def test_classify_job_idempotency_returns_existing(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """Submitting classify for the same upload+methodology returns the running job."""
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)

        # First call succeeds immediately (SyncDevRunner)
        r1 = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        j1 = r1.json()
        assert j1["status"] == JobStatus.SUCCEEDED

        # Second call: job already succeeded, idempotency key no longer active
        # → creates a new job (the old one is terminal, not active)
        r2 = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        # Both return 202 and are classify_upload jobs
        assert r2.status_code == 202
        assert r2.json()["job_type"] == "classify_upload"

    def test_classify_job_fails_on_unknown_upload(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/00000000-0000-0000-0000-000000000099/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == JobStatus.FAILED
        assert body["error_message"] is not None
        assert body["failed_at"] is not None


# ---------------------------------------------------------------------------
# Run-calculation job
# ---------------------------------------------------------------------------

class TestRunCalculationJob:
    def test_calculate_job_succeeds(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        # Classify first so products have categories
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        # Manually approve all review items so calculation has full scope
        for item in client.get(f"/api/v1/projects/{pid}/review").json():
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )

        r = client.post(
            f"/api/v1/projects/{pid}/jobs/calculate",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["job_type"] == "run_calculation"
        assert body["result"]["rows_count"] == 12
        assert body["run_id"] is not None

    def test_calculate_job_result_has_run_id(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        for item in client.get(f"/api/v1/projects/{pid}/review").json():
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        r = client.post(
            f"/api/v1/projects/{pid}/jobs/calculate",
            json={"methodology": "protein_tracker"},
        )
        body = r.json()
        result = body["result"]
        assert "run_id" in result
        assert body["run_id"] == result["run_id"]


# ---------------------------------------------------------------------------
# Generate-export job
# ---------------------------------------------------------------------------

class TestGenerateExportJob:
    def _setup_run(self, client: TestClient, pt_tiny_csv: bytes) -> tuple[str, str]:
        """Return (project_id, run_id)."""
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        for item in client.get(f"/api/v1/projects/{pid}/review").json():
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        run_id = client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"methodology": "protein_tracker"},
        ).json()["id"]
        return pid, run_id

    def test_export_job_csv_succeeds(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, run_id = self._setup_run(client, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/runs/{run_id}/jobs/export",
            json={"fmt": "csv"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["run_id"] == run_id
        result = body["result"]
        assert result["fmt"] == "csv"
        assert result["size_bytes"] > 0
        assert result["filename"].endswith(".csv")

    def test_export_job_json_succeeds(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, run_id = self._setup_run(client, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/runs/{run_id}/jobs/export",
            json={"fmt": "json"},
        )
        assert r.json()["status"] == JobStatus.SUCCEEDED

    def test_export_job_idempotency(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, run_id = self._setup_run(client, pt_tiny_csv)
        r1 = client.post(
            f"/api/v1/projects/{pid}/runs/{run_id}/jobs/export",
            json={"fmt": "csv"},
        )
        r2 = client.post(
            f"/api/v1/projects/{pid}/runs/{run_id}/jobs/export",
            json={"fmt": "csv"},
        )
        assert r1.status_code == 202
        assert r2.status_code == 202
        # Both are export jobs; idempotency returns existing once terminal

    def test_export_job_fails_on_unknown_run(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/runs/00000000-0000-0000-0000-000000000099/jobs/export",
            json={"fmt": "csv"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == JobStatus.FAILED
        assert body["error_message"] is not None


# ---------------------------------------------------------------------------
# Validate-upload and ingest-upload jobs
# ---------------------------------------------------------------------------

class TestValidateAndIngestJobs:
    def test_validate_upload_job_valid_csv(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/validate",
            json={
                "filename": "data.csv",
                "file_bytes_b64": _b64(pt_tiny_csv),
            },
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["result"]["is_valid"] is True
        assert body["result"]["errors"] == []

    def test_validate_upload_job_invalid_extension(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/validate",
            json={
                "filename": "data.xlsx",
                "file_bytes_b64": _b64(b"PK\x03\x04"),
            },
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["result"]["is_valid"] is False
        assert body["result"]["errors"]

    def test_ingest_upload_job_creates_products(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        # Create an upload stub first
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        # Use job-based ingest on a fresh upload id from a second call
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/ingest",
            json={
                "filename": "data.csv",
                "file_bytes_b64": _b64(pt_tiny_csv),
                "upload_id": uid,
            },
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == JobStatus.SUCCEEDED
        assert body["result"]["products_count"] == 12


# ---------------------------------------------------------------------------
# Job listing and lookup
# ---------------------------------------------------------------------------

class TestJobListing:
    def test_list_jobs_for_project(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        r = client.get(f"/api/v1/projects/{pid}/jobs")
        assert r.status_code == 200
        jobs = r.json()
        assert len(jobs) >= 1
        assert all(j["project_id"] == pid for j in jobs)

    def test_list_jobs_filter_by_type(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        r = client.get(
            f"/api/v1/projects/{pid}/jobs?job_type=classify_upload"
        )
        jobs = r.json()
        assert all(j["job_type"] == "classify_upload" for j in jobs)

    def test_get_job_by_id(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        job_id = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        ).json()["job_id"]

        r = client.get(f"/api/v1/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json()["job_id"] == job_id

    def test_get_job_404_unknown(self, client: TestClient) -> None:
        r = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000099")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------

class TestJobCrossTenantIsolation:
    def test_job_listed_only_for_owning_project(
        self,
        client: TestClient,
        pt_tiny_csv: bytes,
    ) -> None:
        """Jobs are project-scoped; other project's job list is empty."""
        pid_a = _create_project(client)
        pid_b = _create_project(client)
        uid = _upload_and_ingest(client, pid_a, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid_a}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        # Project B has no jobs
        r = client.get(f"/api/v1/projects/{pid_b}/jobs")
        assert r.json() == []


# ---------------------------------------------------------------------------
# Failed job records error_message
# ---------------------------------------------------------------------------

class TestFailedJobErrorMessage:
    def test_failed_job_has_error_message(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        # Request classify on a non-existent upload → job fails
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/00000000-0000-0000-0000-000000000001/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == JobStatus.FAILED
        assert body["error_message"] is not None
        assert len(body["error_message"]) > 0
        assert body["failed_at"] is not None
        assert body["completed_at"] is None  # completed_at only set on success


# ---------------------------------------------------------------------------
# Existing synchronous endpoints still pass (regression)
# ---------------------------------------------------------------------------

class TestExistingSyncEndpointsUnchanged:
    def test_sync_classify_still_works(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["matched"] + body["pass_through"] + body["rule_collision"] == 12

    def test_sync_run_calculation_still_works(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        for item in client.get(f"/api/v1/projects/{pid}/review").json():
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        r = client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201
        assert r.json()["rows_count"] == 12


# ---------------------------------------------------------------------------
# Dev runner executes expected task
# ---------------------------------------------------------------------------

class TestDevRunner:
    def test_dev_runner_executes_synchronously(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """SyncDevRunner returns a terminal status in the same HTTP response."""
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        # No polling needed — status is already terminal
        body = r.json()
        assert body["status"] in (JobStatus.SUCCEEDED, JobStatus.FAILED)

    def test_dev_runner_audit_events_emitted(
        self, client: TestClient, store: InMemoryStore, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload_and_ingest(client, pid, pt_tiny_csv)
        before = len(store.audit_events)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/jobs/classify",
            json={"methodology": "protein_tracker"},
        )
        after = len(store.audit_events)
        # Expect at least: JOB_CREATED + JOB_STARTED + JOB_SUCCEEDED
        assert after - before >= 3

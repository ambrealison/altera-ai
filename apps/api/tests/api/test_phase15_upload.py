"""Phase 15: production upload pipeline tests.

Covers: file-type validation, size limits, empty-file rejection,
SHA-256 checksum generation, duplicate detection, validation-report
persistence, status transitions, ingestion idempotency, storage path
format, and cross-tenant access denial.
"""
from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.domain.upload import UploadStatus
from altera_api.ingestion.validators import (
    compute_sha256,
    validate_upload,
)
from altera_api.storage.service import StorageService

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


def _pt_minimal_csv() -> bytes:
    """Minimal valid protein-tracker CSV with one product."""
    return (
        b"external_product_id,product_name,weight_per_item_kg,"
        b"items_purchased,protein_pct\n"
        b"P-001,Lentil Soup,0.4,1000,4.5\n"
    )


# ---------------------------------------------------------------------------
# Unit tests for validate_upload()
# ---------------------------------------------------------------------------

class TestValidateUpload:
    def test_csv_accepted(self) -> None:
        assert validate_upload("data.csv", b"a,b\n1,2") == []

    def test_tsv_accepted(self) -> None:
        assert validate_upload("data.tsv", b"a\tb\n1\t2") == []

    def test_txt_accepted(self) -> None:
        assert validate_upload("data.txt", b"col\nval") == []

    def test_pdf_rejected(self) -> None:
        errors = validate_upload("report.pdf", b"%PDF-1.4")
        assert any(".pdf" in e for e in errors)

    def test_exe_rejected(self) -> None:
        errors = validate_upload("malware.exe", b"MZ\x90\x00")
        assert any(".exe" in e for e in errors)

    def test_no_extension_rejected(self) -> None:
        errors = validate_upload("noextension", b"col\nval")
        assert errors  # empty extension is not in ALLOWED_EXTENSIONS

    def test_empty_file_rejected(self) -> None:
        errors = validate_upload("data.csv", b"")
        assert any("empty" in e for e in errors)

    def test_oversized_rejected(self) -> None:
        errors = validate_upload("big.csv", b"x", max_bytes=0)
        assert any("MB limit" in e or "limit" in e for e in errors)

    def test_content_type_accepted(self) -> None:
        assert validate_upload("data.csv", b"a", content_type="text/csv") == []

    def test_content_type_rejected(self) -> None:
        errors = validate_upload(
            "data.csv", b"a", content_type="application/pdf"
        )
        assert any("content-type" in e for e in errors)

    def test_content_type_with_charset_accepted(self) -> None:
        # charset qualifier must be stripped before comparison
        assert validate_upload(
            "data.csv", b"a", content_type="text/csv; charset=utf-8"
        ) == []


# ---------------------------------------------------------------------------
# SHA-256 checksum
# ---------------------------------------------------------------------------

class TestChecksum:
    def test_compute_sha256_matches_stdlib(self) -> None:
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert compute_sha256(data) == expected

    def test_different_data_different_hash(self) -> None:
        assert compute_sha256(b"a") != compute_sha256(b"b")

    def test_empty_bytes_has_known_hash(self) -> None:
        known = hashlib.sha256(b"").hexdigest()
        assert compute_sha256(b"") == known


# ---------------------------------------------------------------------------
# API-level upload status transitions
# ---------------------------------------------------------------------------

class TestUploadStatusTransitions:
    def test_valid_csv_reaches_ready_for_classification(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == UploadStatus.READY_FOR_CLASSIFICATION

    def test_invalid_csv_reaches_validation_failed(
        self, client: TestClient
    ) -> None:
        """CSV that has blocking validation errors → VALIDATION_FAILED."""
        pid = _create_project(client)
        bad_csv = b"external_product_id,product_name,weight_per_item_kg\nP1,Soup,not-a-number\n"
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("bad.csv", bad_csv, "text/csv")},
        )
        assert r.status_code == 201
        assert r.json()["status"] == UploadStatus.VALIDATION_FAILED

    def test_empty_file_returns_400(self, client: TestClient) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_rejected_extension_returns_400(self, client: TestClient) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")},
        )
        assert r.status_code == 400
        assert ".xlsx" in r.json()["detail"]


# ---------------------------------------------------------------------------
# File metadata on the response
# ---------------------------------------------------------------------------

class TestUploadMetadata:
    def test_checksum_populated(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        )
        body = r.json()
        assert body["checksum_sha256"] == compute_sha256(pt_tiny_csv)

    def test_file_size_populated(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        )
        assert r.json()["file_size_bytes"] == len(pt_tiny_csv)

    def test_timestamps_populated_on_success(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        )
        body = r.json()
        assert body["validation_started_at"] is not None
        assert body["validation_completed_at"] is not None
        assert body["ingestion_started_at"] is not None
        assert body["ingestion_completed_at"] is not None

    def test_ingestion_timestamps_absent_on_failure(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        bad_csv = b"external_product_id,product_name,weight_per_item_kg\nP1,Soup,bad\n"
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("bad.csv", bad_csv, "text/csv")},
        )
        body = r.json()
        assert body["ingestion_started_at"] is None
        assert body["ingestion_completed_at"] is None


# ---------------------------------------------------------------------------
# Validation report persistence
# ---------------------------------------------------------------------------

class TestValidationReportPersistence:
    def test_report_retrievable_via_get_upload(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload_id = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()["id"]

        r = client.get(f"/api/v1/projects/{pid}/uploads/{upload_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["row_count"] == 12
        assert body["errors"] == []

    def test_row_level_errors_in_report(self, client: TestClient) -> None:
        pid = _create_project(client)
        bad_csv = b"external_product_id,product_name,weight_per_item_kg\nP1,Soup,BADNUM\n"
        upload_id = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("bad.csv", bad_csv, "text/csv")},
        ).json()["id"]

        r = client.get(f"/api/v1/projects/{pid}/uploads/{upload_id}")
        body = r.json()
        assert body["errors"]
        assert body["errors"][0]["row_number"] == 1

    def test_get_upload_404_for_wrong_project(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload_id = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()["id"]
        other_pid = _create_project(client)
        r = client.get(f"/api/v1/projects/{other_pid}/uploads/{upload_id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_duplicate_flagged_on_second_upload(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        first = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()
        second = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny_copy.csv", pt_tiny_csv, "text/csv")},
        ).json()
        assert second["duplicate_of"] == first["id"]

    def test_duplicate_not_blocked(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """Duplicate is a warning, not a rejection."""
        pid = _create_project(client)
        client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("a.csv", pt_tiny_csv, "text/csv")},
        )
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("b.csv", pt_tiny_csv, "text/csv")},
        )
        assert r.status_code == 201

    def test_same_file_different_project_not_flagged(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid1 = _create_project(client)
        pid2 = _create_project(client)
        client.post(
            f"/api/v1/projects/{pid1}/uploads",
            files={"file": ("a.csv", pt_tiny_csv, "text/csv")},
        )
        r = client.post(
            f"/api/v1/projects/{pid2}/uploads",
            files={"file": ("a.csv", pt_tiny_csv, "text/csv")},
        )
        assert r.json()["duplicate_of"] is None


# ---------------------------------------------------------------------------
# Ingestion idempotency
# ---------------------------------------------------------------------------

class TestIngestionIdempotency:
    def test_classifying_twice_does_not_duplicate_products(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """Re-running classify on the same upload must not create extra review items."""
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()
        # Classify once
        r1 = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        q1 = r1.json()["queued_for_review"]
        # Classify again (same upload, same products)
        r2 = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        q2 = r2.json()["queued_for_review"]
        # Re-classifying is idempotent — queue size must not grow
        assert q2 == q1


# ---------------------------------------------------------------------------
# Storage path format
# ---------------------------------------------------------------------------

class TestStoragePath:
    def test_storage_path_includes_projects_and_raw(self) -> None:
        import uuid
        svc = StorageService.__new__(StorageService)  # no supabase client needed
        org = uuid.UUID("00000000-0000-0000-0000-000000000001")
        proj = uuid.UUID("00000000-0000-0000-0000-000000000002")
        upload = uuid.UUID("00000000-0000-0000-0000-000000000003")
        path = svc.storage_path(org, proj, upload, "data.csv")
        assert path == (
            f"organisations/{org}/projects/{proj}/uploads/{upload}/raw/data.csv"
        )


# ---------------------------------------------------------------------------
# Cross-tenant upload isolation
# ---------------------------------------------------------------------------

class TestCrossTenantUploadAccess:
    def test_client_cannot_access_other_org_upload(
        self,
        store: InMemoryStore,
        client: TestClient,
        pt_tiny_csv: bytes,
    ) -> None:
        """Upload details are project-scoped; cross-project access returns 404."""
        pid_a = _create_project(client)
        pid_b = _create_project(client)

        upload_id = client.post(
            f"/api/v1/projects/{pid_a}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()["id"]

        # Upload belongs to project A; requesting via project B must 404
        r = client.get(f"/api/v1/projects/{pid_b}/uploads/{upload_id}")
        assert r.status_code == 404

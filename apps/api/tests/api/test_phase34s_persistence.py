"""Phase 34S — Postgres persistence + large-CSV upload scalability.

Areas under test:

A. ``classification_job_to_row`` / ``classification_job_from_row``
   roundtrip a ClassificationJob through the Supabase wire shape
   without losing data.
B. The mappers cope with JSONB columns coming back as either Python
   lists (default Supabase client behaviour) or JSON strings.
C. The ``UploadResponse`` caps its ``errors`` / ``warnings`` arrays
   to at most ``UPLOAD_RESPONSE_DETAIL_LIMIT`` entries but still
   reports the full ``errors_total`` / ``warnings_total`` counts.
D. A synthetic 1050-row CSV ingest succeeds end-to-end and its
   response stays under a small payload-size envelope.
E. The classification_jobs SQL migration file exists and declares
   the documented columns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.classification_job import (
    ClassificationJob,
    ClassificationJobStatus,
)
from altera_api.domain.common import AlteraRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app
from altera_api.persistence.mappers import (
    classification_job_from_row,
    classification_job_to_row,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _promote(store: InMemoryStore) -> None:
    org_id = store.default_org_id
    user_id = store.default_user_id
    existing_org = store.organisations[org_id]
    store.organisations[org_id] = Organisation(
        id=org_id,
        name=existing_org.name,
        slug=existing_org.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing_org.created_at,
    )
    existing_user = store.users[user_id]
    store.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=existing_user.email,
            display_name=existing_user.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=existing_user.created_at,
        )
    )


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


# ---------------------------------------------------------------------------
# A + B. Mapper roundtrip
# ---------------------------------------------------------------------------


def _sample_job() -> ClassificationJob:
    now = datetime.now(UTC)
    return ClassificationJob(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        methodology=Methodology.PROTEIN_TRACKER,
        status=ClassificationJobStatus.RUNNING,
        total_products=42,
        processed_products=10,
        pending_product_ids=tuple(uuid4() for _ in range(5)),
        failed_product_ids=tuple(uuid4() for _ in range(2)),
        categorized_total=8,
        accepted_total=6,
        review_required_total=2,
        failed_total=2,
        unknown_total=0,
        out_of_scope_total=1,
        retry_batches=1,
        recovered_rows=3,
        overwrite=False,
        only_missing_or_failed=True,
        batch_size=25,
        cancel_requested=False,
        error_code=None,
        error_message=None,
        sample_errors=("partial_recovery: 3 row(s) salvaged",),
        created_by=uuid4(),
        created_at=now,
        started_at=now,
        updated_at=now,
        completed_at=None,
        cancelled_at=None,
    )


class TestMapperRoundtrip:
    def test_to_row_produces_serializable_dict(self) -> None:
        job = _sample_job()
        row = classification_job_to_row(job)
        # All values must be JSON-serializable (no UUIDs, no datetimes).
        json.dumps(row)  # would raise if not
        # Every documented column is present.
        for key in [
            "id",
            "organisation_id",
            "project_id",
            "upload_id",
            "methodology",
            "status",
            "total_products",
            "processed_products",
            "pending_product_ids",
            "failed_product_ids",
            "categorized_total",
            "accepted_total",
            "review_required_total",
            "failed_total",
            "unknown_total",
            "out_of_scope_total",
            "retry_batches",
            "recovered_rows",
            "overwrite",
            "only_missing_or_failed",
            "batch_size",
            "cancel_requested",
            "error_code",
            "error_message",
            "sample_errors",
            "created_by",
            "created_at",
            "started_at",
            "updated_at",
            "completed_at",
            "cancelled_at",
        ]:
            assert key in row, f"missing column in row: {key!r}"

    def test_roundtrip_preserves_fields(self) -> None:
        job = _sample_job()
        row = classification_job_to_row(job)
        # Simulate the Supabase REST API echoing the same JSON back.
        echoed = json.loads(json.dumps(row))
        rebuilt = classification_job_from_row(echoed)
        assert rebuilt.id == job.id
        assert rebuilt.organisation_id == job.organisation_id
        assert rebuilt.project_id == job.project_id
        assert rebuilt.upload_id == job.upload_id
        assert rebuilt.methodology is job.methodology
        assert rebuilt.status is job.status
        assert rebuilt.total_products == job.total_products
        assert rebuilt.processed_products == job.processed_products
        assert rebuilt.pending_product_ids == job.pending_product_ids
        assert rebuilt.failed_product_ids == job.failed_product_ids
        assert rebuilt.categorized_total == job.categorized_total
        assert rebuilt.accepted_total == job.accepted_total
        assert rebuilt.review_required_total == job.review_required_total
        assert rebuilt.failed_total == job.failed_total
        assert rebuilt.out_of_scope_total == job.out_of_scope_total
        assert rebuilt.retry_batches == job.retry_batches
        assert rebuilt.recovered_rows == job.recovered_rows
        assert rebuilt.overwrite == job.overwrite
        assert rebuilt.only_missing_or_failed == job.only_missing_or_failed
        assert rebuilt.batch_size == job.batch_size
        assert rebuilt.sample_errors == job.sample_errors
        # Timestamps roundtrip via ISO-8601 strings.
        assert rebuilt.created_at == job.created_at
        assert rebuilt.started_at == job.started_at
        assert rebuilt.updated_at == job.updated_at

    def test_jsonb_columns_tolerate_string_form(self) -> None:
        """Some clients return JSONB columns as JSON strings rather
        than parsed Python lists. The from_row mapper must handle both."""
        job = _sample_job()
        row = classification_job_to_row(job)
        # Re-encode the JSONB columns as strings, mimicking the
        # alternative client behaviour.
        row["pending_product_ids"] = json.dumps(row["pending_product_ids"])
        row["failed_product_ids"] = json.dumps(row["failed_product_ids"])
        row["sample_errors"] = json.dumps(row["sample_errors"])
        rebuilt = classification_job_from_row(row)
        assert rebuilt.pending_product_ids == job.pending_product_ids
        assert rebuilt.failed_product_ids == job.failed_product_ids
        assert rebuilt.sample_errors == job.sample_errors


# ---------------------------------------------------------------------------
# C. UploadResponse capping
# ---------------------------------------------------------------------------


class TestUploadResponseCapping:
    def test_response_caps_errors_and_warnings(
        self, client: TestClient
    ) -> None:
        """A CSV with errors on every row produces a *capped* errors
        array but the full counts are still exposed."""
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34s",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        pid = r.json()["id"]
        # Build a CSV that triggers a warning on every row.
        # Volume is required for PT but row weights below the floor
        # warn — synthesise 200 rows each missing weight.
        header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
        # Use a sentinel name that doesn't match deterministic rules
        # and an empty weight to force a validation issue.
        body = b"".join(
            f"Mystery {i},,1.0\n".encode() for i in range(200)
        )
        mapping = (
            '{"product_name_fr": "product_name",'
            ' "poids_unitaire_produit_g": "weight_per_item_g",'
            ' "volume": "items_purchased"}'
        )
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", header + body, "text/csv")},
            data={"column_mapping": mapping},
        )
        assert r_up.status_code == 201, r_up.text
        out = r_up.json()
        # Whether errors or warnings, the detail array is capped.
        for key in ("errors", "warnings"):
            assert isinstance(out[key], list)
            assert len(out[key]) <= 50, (
                f"{key} list not capped: got {len(out[key])}"
            )
        # The *_total counters expose the real numbers.
        assert "errors_total" in out
        assert "warnings_total" in out
        assert out["errors_total"] >= 0
        assert out["warnings_total"] >= 0
        # If we hit the cap, the total must reflect more than what
        # was returned.
        for key, tot_key in (("errors", "errors_total"), ("warnings", "warnings_total")):
            if len(out[key]) == 50:
                assert out[tot_key] >= len(out[key])


# ---------------------------------------------------------------------------
# D. 1050-row CSV upload + payload size
# ---------------------------------------------------------------------------


class TestLargeUpload:
    def test_thousand_fifty_row_upload_succeeds_with_compact_response(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34s2",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        pid = r.json()["id"]
        header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
        body = b"".join(
            f"Tofu Lot {i},150,1.0\n".encode() for i in range(1050)
        )
        mapping = (
            '{"product_name_fr": "product_name",'
            ' "poids_unitaire_produit_g": "weight_per_item_g",'
            ' "volume": "items_purchased"}'
        )
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", header + body, "text/csv")},
            data={"column_mapping": mapping},
        )
        assert r_up.status_code == 201, r_up.text
        out = r_up.json()
        assert out["row_count"] == 1050
        assert out["products_count"] == 1050
        # Phase 34S — the response body must stay small even for
        # 1050 rows. Re-serialise and assert it's well under 100 KB.
        size = len(json.dumps(out).encode())
        assert size < 100 * 1024, (
            f"response payload is {size} bytes — too large for 1050 rows"
        )


# ---------------------------------------------------------------------------
# E. Migration file exists
# ---------------------------------------------------------------------------


class TestMigration:
    def test_classification_jobs_migration_exists(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        migration = (
            repo_root
            / "supabase"
            / "migrations"
            / "0034_phase34s_classification_jobs.sql"
        )
        assert migration.is_file(), (
            f"missing migration at {migration}"
        )
        sql = migration.read_text(encoding="utf-8")
        # Sanity-check the documented columns are present.
        for column in [
            "classification_jobs",
            "total_products",
            "processed_products",
            "pending_product_ids",
            "failed_product_ids",
            "categorized_total",
            "accepted_total",
            "review_required_total",
            "failed_total",
            "unknown_total",
            "out_of_scope_total",
            "retry_batches",
            "recovered_rows",
            "overwrite",
            "only_missing_or_failed",
            "batch_size",
            "error_code",
            "error_message",
            "sample_errors",
            "updated_at",
            "completed_at",
            "cancelled_at",
        ]:
            assert column in sql, f"migration missing {column!r}"
        # RLS must be enabled — every multi-tenant table in this
        # project enables RLS for org isolation.
        assert "ENABLE ROW LEVEL SECURITY" in sql

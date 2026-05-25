"""Phase 34X — chunked, resumable CSV ingestion jobs.

Areas under test:

A. ``POST /uploads/{id}/ingestion-jobs`` parses the CSV up-front
   without inserting products, returns the job id + total_rows.
B. ``POST /ingestion-jobs/{jid}/advance`` processes one chunk —
   exactly ``chunk_size`` products inserted, ``pending_payload``
   trimmed, ``processed_rows`` bumped.
C. Multiple advances drive a 1050-row job to completion across
   exactly ceil(1050 / 500) = 3 chunks.
D. Status transitions queued → running → completed.
E. ``GET /ingestion-jobs/{jid}`` is a pure read (no advance side
   effect, no AI calls).
F. The advance response carries only counters (no raw product list
   or pending_payload blob).
G. Migration file exists with the documented columns.
H. Unknown job id returns structured 404 ``ingestion_job_not_found``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app


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


def _create_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "p34x",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _csv(n_rows: int) -> bytes:
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu Lot {i},150,1.0\n".encode() for i in range(n_rows)
    )
    return header + body


# ---------------------------------------------------------------------------
# A. Create ingestion job
# ---------------------------------------------------------------------------


class TestCreateIngestionJob:
    def test_create_returns_job_with_total_rows_and_no_products_yet(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        # Mint the upload_id client-side per the new prepare-then-
        # ingest pattern. The route accepts an upload_id path param
        # whether or not the upload already exists.
        uid = str(uuid4())
        # We have no separate "prepare" route in this MVP — the
        # ingestion-job creation endpoint accepts the file directly
        # and creates the upload record up-front (without inserting
        # products).
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(100), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "25"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["total_rows"] == 100
        assert body["processed_rows"] == 0
        assert body["inserted_products"] == 0
        # Products are NOT inserted yet — that's what advance does.
        assert len(store.products) == 0

    def test_invalid_mapping_returns_structured_400(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(5), "text/csv")},
            data={"column_mapping": "{not json"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_mapping"


# ---------------------------------------------------------------------------
# B + C. Advance processes one chunk; multiple advances complete the job
# ---------------------------------------------------------------------------


class TestAdvanceIngestionJob:
    def test_advance_processes_one_chunk_only(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(60), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "25"},
        )
        jid = r.json()["job_id"]
        # First advance: 25 products inserted, status=running.
        r1 = client.post(
            f"/api/v1/projects/{pid}/ingestion-jobs/{jid}/advance"
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["status"] == "running"
        assert body["processed_rows"] == 25
        assert body["inserted_products"] == 25
        assert len(store.products) == 25

    def test_1050_row_job_completes_in_three_chunks_of_500(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "500"},
        )
        jid = r.json()["job_id"]
        assert r.json()["total_rows"] == 1050

        chunks_used = 0
        for _ in range(10):  # safety bound
            r_adv = client.post(
                f"/api/v1/projects/{pid}/ingestion-jobs/{jid}/advance"
            )
            assert r_adv.status_code == 200
            chunks_used += 1
            body = r_adv.json()
            if body["status"] in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                break
        assert body["status"] == "completed", body
        assert body["processed_rows"] == 1050
        assert body["inserted_products"] == 1050
        # 1050 / 500 = 2.1 → 3 chunks.
        assert chunks_used == math.ceil(1050 / 500)
        assert len(store.products) == 1050


# ---------------------------------------------------------------------------
# E + F. GET is pure-read; response carries only counters
# ---------------------------------------------------------------------------


class TestIngestionJobResponseShape:
    def test_get_is_a_pure_read(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "25"},
        )
        jid = r.json()["job_id"]
        # Reading the job repeatedly does not insert products.
        for _ in range(3):
            rg = client.get(
                f"/api/v1/projects/{pid}/ingestion-jobs/{jid}"
            )
            assert rg.status_code == 200
            assert rg.json()["status"] == "queued"
        assert len(store.products) == 0

    def test_advance_response_does_not_leak_pending_payload(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "25"},
        )
        jid = r.json()["job_id"]
        body = client.post(
            f"/api/v1/projects/{pid}/ingestion-jobs/{jid}/advance"
        ).json()
        forbidden = {"pending_payload", "products", "rows"}
        assert forbidden.isdisjoint(body.keys()), (
            f"advance response leaks {set(body.keys()) & forbidden}"
        )

    def test_response_size_is_bounded_for_1050_rows(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "500"},
        )
        body = r.json()
        size = len(json.dumps(body).encode())
        # The create response carries counters + sample_errors only,
        # nothing per-row. Must be tiny even for 1050+ rows.
        assert size < 10 * 1024, (
            f"ingestion-job create response is {size} bytes — too big"
        )


# ---------------------------------------------------------------------------
# G. Migration file exists
# ---------------------------------------------------------------------------


class TestIngestionJobMigration:
    def test_migration_file_exists_with_documented_columns(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        migration = (
            repo_root
            / "supabase"
            / "migrations"
            / "0036_phase34x_ingestion_jobs.sql"
        )
        assert migration.is_file(), f"missing migration at {migration}"
        sql = migration.read_text(encoding="utf-8")
        for col in [
            "ingestion_jobs",
            "total_rows",
            "processed_rows",
            "inserted_products",
            "pending_payload",
            "sample_errors",
            "chunk_size",
            "next_row_offset",
            "error_code",
            "error_message",
            "updated_at",
            "completed_at",
        ]:
            assert col in sql, f"migration missing {col!r}"
        assert "ENABLE ROW LEVEL SECURITY" in sql


# ---------------------------------------------------------------------------
# H. Structured 404
# ---------------------------------------------------------------------------


class TestIngestionJobNotFound:
    def test_unknown_job_returns_structured_404(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        r = client.get(
            f"/api/v1/projects/{pid}/ingestion-jobs/{uuid4()}"
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "ingestion_job_not_found"

    def test_advance_unknown_job_returns_structured_404(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/ingestion-jobs/{uuid4()}/advance"
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "ingestion_job_not_found"

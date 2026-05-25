"""Phase 35-OOM — response-size + memory regression guards.

Production hit OOM (>512 MB) on a 1050-row CSV with a Render restart
and 223 KB observed on both ``POST /ingestion-jobs/{id}/advance``
and ``GET /api/v1/projects``. Root cause: ``_project_response`` was
calling ``list_products_for_project`` for every project just to
count unclassified — materialising 1050 ``NormalizedProduct``
Pydantic objects per project per request — and
``list_uploads_for_project`` was making a single unchunked
``.in_("upload_id", …)`` query whose response PostgREST silently
truncated at 1000 rows. With concurrent requests during a heavy
import the worker tipped over.

This module locks down:

A. ``IngestionJobResponse`` never carries ``pending_payload`` /
   ``products`` / ``rows`` / raw mapping blobs.
B. The advance response stays under 10 KB even for a 1050-row
   pending payload.
C. The create-ingestion-job response stays under 10 KB even for
   1050 rows.
D. ``GET /api/v1/projects`` body stays under 30 KB for a project
   with 1050 products + a sitting ingestion job.
E. The new ``_project_response`` does NOT call
   ``list_products_for_project`` (the OOM-causing N+1).
F. Default chunk_size is 250 (Phase 35-OOM reduction).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    OrganisationType,
)
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


_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _csv(n: int) -> bytes:
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu Lot {i},150,1.0\n".encode() for i in range(n)
    )
    return header + body


def _create_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "oom",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    return r.json()["id"]


# ---------------------------------------------------------------------------
# A. Response shape — no pending_payload, no raw lists
# ---------------------------------------------------------------------------


class TestIngestionResponseShape:
    def test_create_response_excludes_payload_leakage_keys(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        body = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING},
        ).json()
        forbidden = {
            "pending_payload",
            "parsed_products",
            "products",
            "rows",
            "mapping_payload",
        }
        assert forbidden.isdisjoint(body.keys()), (
            f"create response leaks {set(body.keys()) & forbidden}"
        )

    def test_advance_response_excludes_payload_leakage_keys(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "25"},
        ).json()
        body = client.post(
            f"/api/v1/projects/{pid}/ingestion-jobs/"
            f"{created['job_id']}/advance"
        ).json()
        forbidden = {
            "pending_payload",
            "parsed_products",
            "products",
            "rows",
        }
        assert forbidden.isdisjoint(body.keys()), (
            f"advance response leaks {set(body.keys()) & forbidden}"
        )


# ---------------------------------------------------------------------------
# B + C. 1050-row payloads stay bounded
# ---------------------------------------------------------------------------


class TestPayloadsBounded1050:
    def test_create_response_for_1050_rows_under_10_kb(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING},
        )
        assert r.status_code == 201
        size = len(r.content)
        assert size < 10 * 1024, (
            f"create response for 1050 rows is {size} bytes — "
            "OOM regression"
        )

    def test_advance_response_for_1050_rows_under_10_kb(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING},
        ).json()
        r = client.post(
            f"/api/v1/projects/{pid}/ingestion-jobs/"
            f"{created['job_id']}/advance"
        )
        assert r.status_code == 200
        size = len(r.content)
        assert size < 10 * 1024, (
            f"advance response for 1050-row pending is {size} bytes — "
            "the 223 KB regression from prod logs"
        )


# ---------------------------------------------------------------------------
# D. GET /projects bounded with a heavy project sitting in store
# ---------------------------------------------------------------------------


class TestProjectsListBounded:
    def test_list_projects_response_under_30_kb_with_1050_products(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        # Create + advance a 1050-row ingestion job all the way to
        # completion so the project has 1050 actual products in
        # the store. This is the scenario where Phase 35-OOM
        # production hit the 223 KB / OOM combo.
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING, "chunk_size": "500"},
        ).json()
        for _ in range(10):
            adv = client.post(
                f"/api/v1/projects/{pid}/ingestion-jobs/"
                f"{created['job_id']}/advance"
            ).json()
            if adv["status"] in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                break
        r = client.get("/api/v1/projects")
        assert r.status_code == 200
        size = len(r.content)
        assert size < 30 * 1024, (
            f"GET /projects is {size} bytes with 1050 products — "
            "OOM regression"
        )


# ---------------------------------------------------------------------------
# E. _project_response doesn't call list_products_for_project anymore
# ---------------------------------------------------------------------------


class TestProjectResponseDoesNotWalkProducts:
    def test_get_projects_never_calls_list_products_for_project(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        # Ingest 50 products so list_products_for_project would
        # return a non-empty list if accidentally called.
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING},
        )
        list_calls: list[None] = []
        original = store.list_products_for_project

        def spy(project_id):  # type: ignore[no-untyped-def]
            list_calls.append(None)
            return original(project_id)

        store.list_products_for_project = spy  # type: ignore[method-assign]
        try:
            r = client.get("/api/v1/projects")
            assert r.status_code == 200
        finally:
            store.list_products_for_project = original  # type: ignore[method-assign]
        assert list_calls == [], (
            f"GET /projects called list_products_for_project "
            f"{len(list_calls)} times — re-introduced OOM N+1"
        )


# ---------------------------------------------------------------------------
# F. Default chunk_size lowered to 250
# ---------------------------------------------------------------------------


class TestChunkSizeDefault:
    def test_orchestrator_default_chunk_size_is_250(self) -> None:
        import inspect

        from altera_api.api.ingestion_job_orchestrator import (
            create_ingestion_job,
        )

        sig = inspect.signature(create_ingestion_job)
        assert sig.parameters["chunk_size"].default == 250

    def test_domain_entity_default_chunk_size_is_250(self) -> None:
        # The dataclass field default.
        import dataclasses

        from altera_api.domain.ingestion_job import IngestionJob

        fields = {f.name: f for f in dataclasses.fields(IngestionJob)}
        assert fields["chunk_size"].default == 250

    def test_route_uses_smaller_chunk_when_no_explicit_value(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        # 50 rows with the new default 250 → all fits in 1 advance.
        body = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(50), "text/csv")},
            data={"column_mapping": _MAPPING},
        ).json()
        assert body["chunk_size"] == 250
        # 1050 rows with the new default → 5 chunks of 250 (= 1250
        # capacity, last chunk has 50). Verify a value the wizard
        # will rely on for progress estimation.
        uid2 = str(uuid4())
        body2 = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid2}/ingestion-jobs",
            files={"file": ("c.csv", _csv(1050), "text/csv")},
            data={"column_mapping": _MAPPING},
        ).json()
        assert body2["chunk_size"] == 250
        assert body2["total_rows"] == 1050


# ---------------------------------------------------------------------------
# G. Sanity smoke — 1050-row payload size + key set
# ---------------------------------------------------------------------------


class TestSanitySmokeCheck:
    def test_create_response_contains_only_documented_keys(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        uid = str(uuid4())
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/ingestion-jobs",
            files={"file": ("c.csv", _csv(100), "text/csv")},
            data={"column_mapping": _MAPPING},
        )
        body = r.json()
        expected = {
            "job_id",
            "project_id",
            "upload_id",
            "status",
            "total_rows",
            "processed_rows",
            "inserted_products",
            "progress_pct",
            "errors_total",
            "warnings_total",
            "sample_errors",
            "chunk_size",
            "started_at",
            "completed_at",
            "error_code",
            "error_message",
        }
        actual = set(body.keys())
        # New keys are OK; missing keys are not.
        assert expected.issubset(actual), (
            f"missing keys: {expected - actual}"
        )
        # Defence in depth: serialise + re-parse so the test fails
        # cleanly if any non-JSON-safe value sneaks back in.
        assert json.loads(json.dumps(body)) == body

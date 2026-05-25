"""Phase 34Z-fix — regression guard for the PostgREST URL-length trap.

Phase 34Z replaced per-product N+1 in ``compute_workflow_status`` but
two helpers it called — ``list_review_items_for_project`` and
``list_enrichment_records_for_project`` — kept stuffing all 1050
product UUIDs into a single ``.in_("product_id", […])`` filter. The
resulting ~38 KB URL hit PostgREST's URL-length limit, Supabase
surfaced the upstream 400 as ``"JSON could not be generated"``, and
the wizard saw a 500 right after a successful 1050-row import.

This module asserts that:

A. ``_chunked_ids`` produces no chunk larger than ``_IN_FILTER_CHUNK``
   (200), so every ``.in_(...)`` URL stays well under the ~8 KB
   practical PostgREST limit.
B. The chunk constant is at the documented value so a future refactor
   that bumps it back to 500+ fails this test.
C. ``project_has_any_enrichment`` exists on every store and returns
   a boolean — replacing the legacy "load all enrichment records to
   test truthiness" pattern.
D. The InMemoryStore probe returns True/False correctly.
E. ``compute_workflow_status`` uses ``project_has_any_enrichment``,
   NOT ``list_enrichment_records_for_project`` (verified by spying
   on the store).
F. The full workflow-status route serialises cleanly for a 1050-product
   project under the in-memory store — guards against any
   non-JSON-safe value sneaking in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    OrganisationType,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app
from altera_api.persistence.postgres import _IN_FILTER_CHUNK, _chunked_ids


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


def _project_with_n_products(client: TestClient, n: int) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "p34zfix",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    pid = r.json()["id"]
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu Lot {i},150,1.0\n".encode() for i in range(n)
    )
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", header + body, "text/csv")},
        data={"column_mapping": _MAPPING},
    )
    assert r_up.status_code == 201, r_up.text
    return pid


# ---------------------------------------------------------------------------
# A + B. Chunk size is bounded
# ---------------------------------------------------------------------------


class TestChunkSizeBounded:
    def test_in_filter_chunk_is_at_most_200(self) -> None:
        # 200 UUIDs × ~37 chars ≈ 7.4 KB URL — fits under PostgREST's
        # practical ~8 KB limit. A bigger chunk would re-introduce the
        # production 500.
        assert _IN_FILTER_CHUNK <= 200

    def test_chunked_ids_never_exceeds_size(self) -> None:
        ids = [str(uuid4()) for _ in range(1050)]
        for chunk in _chunked_ids(ids, _IN_FILTER_CHUNK):
            assert len(chunk) <= _IN_FILTER_CHUNK
        # Reassemble — no ids lost.
        rebuilt: list[str] = []
        for chunk in _chunked_ids(ids, _IN_FILTER_CHUNK):
            rebuilt.extend(chunk)
        assert rebuilt == ids

    def test_url_estimate_fits_under_8kb(self) -> None:
        """A back-of-envelope check that 200 UUIDs encoded as a
        PostgREST ``.in_()`` filter stays under 8KB. Future
        refactor: any change to chunk size must keep this true."""
        ids = [str(uuid4()) for _ in range(_IN_FILTER_CHUNK)]
        # Mimic the encoded shape: `product_id=in.(uuid,uuid,...)`.
        encoded = "product_id=in.(" + ",".join(ids) + ")"
        assert len(encoded) < 8 * 1024


# ---------------------------------------------------------------------------
# C + D. project_has_any_enrichment probe
# ---------------------------------------------------------------------------


class TestProjectHasAnyEnrichment:
    def test_returns_false_for_project_with_no_enrichment(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _project_with_n_products(client, 50)
        assert store.project_has_any_enrichment(UUID(pid)) is False

    def test_returns_true_after_one_enrichment_record_exists(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _project_with_n_products(client, 50)
        # Pick any product in the project and seed one ENRICHED record.
        product = next(
            p for p in store.products.values() if str(p.project_id) == pid
        )
        store.enrichment_records[product.id] = [
            NutritionEnrichmentRecord(
                product_id=product.id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("5.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.NEVO,
                confidence=Decimal("0.9"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="t",
                created_at=datetime.now(UTC),
                match_method="deterministic",
            )
        ]
        assert store.project_has_any_enrichment(UUID(pid)) is True

    def test_ignores_enrichment_records_for_other_projects(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_a = _project_with_n_products(client, 5)
        pid_b = _project_with_n_products(client, 5)
        product_b = next(
            p for p in store.products.values() if str(p.project_id) == pid_b
        )
        store.enrichment_records[product_b.id] = [
            NutritionEnrichmentRecord(
                product_id=product_b.id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("5.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.NEVO,
                confidence=Decimal("0.9"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="t",
                created_at=datetime.now(UTC),
                match_method="deterministic",
            )
        ]
        # Project A should still report False.
        assert store.project_has_any_enrichment(UUID(pid_a)) is False
        assert store.project_has_any_enrichment(UUID(pid_b)) is True


# ---------------------------------------------------------------------------
# E. compute_workflow_status uses the probe, not list_enrichment_records
# ---------------------------------------------------------------------------


class TestWorkflowStatusUsesProbe:
    def test_workflow_status_calls_probe_not_full_list(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _project_with_n_products(client, 50)
        list_calls: list[None] = []
        probe_calls: list[None] = []
        orig_list = store.list_enrichment_records_for_project
        orig_probe = store.project_has_any_enrichment

        def spy_list(project_id):  # type: ignore[no-untyped-def]
            list_calls.append(None)
            return orig_list(project_id)

        def spy_probe(project_id):  # type: ignore[no-untyped-def]
            probe_calls.append(None)
            return orig_probe(project_id)

        store.list_enrichment_records_for_project = spy_list  # type: ignore[method-assign]
        store.project_has_any_enrichment = spy_probe  # type: ignore[method-assign]
        try:
            r = client.get(f"/api/v1/projects/{pid}/workflow-status")
            assert r.status_code == 200, r.text
        finally:
            store.list_enrichment_records_for_project = orig_list  # type: ignore[method-assign]
            store.project_has_any_enrichment = orig_probe  # type: ignore[method-assign]
        # Probe should be called; full list MUST NOT be called.
        assert len(probe_calls) >= 1
        assert list_calls == [], (
            f"workflow-status still calls list_enrichment_records_for_project "
            f"{len(list_calls)} times — regression of the URL-length 500"
        )


# ---------------------------------------------------------------------------
# F. End-to-end serialisation guard on 1050-product project
# ---------------------------------------------------------------------------


class TestWorkflowStatus1050Serializes:
    def test_workflow_status_serialises_cleanly_for_1050(
        self, client: TestClient
    ) -> None:
        pid = _project_with_n_products(client, 1050)
        r = client.get(f"/api/v1/projects/{pid}/workflow-status")
        assert r.status_code == 200, r.text
        body = r.json()
        # Bounded.
        size = len(json.dumps(body).encode())
        assert size < 30 * 1024, (
            f"workflow-status response is {size} bytes — too big"
        )
        # No NaN / Infinity literals leaked through.
        body_text = r.text
        for forbidden in ("NaN", "Infinity", "-Infinity"):
            assert forbidden not in body_text

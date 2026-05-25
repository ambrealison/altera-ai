"""Phase 34W — bulk product insert + O(1) project detail.

Areas under test:

A. ``InMemoryStore.add_products_bulk`` writes N products in one
   locked operation. The Postgres variant is structurally identical
   (single ``.insert(rows).execute()`` per 500-row chunk) but cannot
   be unit-tested here without a live Supabase.

B. ``ingest_upload`` orchestrator uses ``add_products_bulk`` — verified
   by spying on a wrapper store that counts how many times each
   write method was called for a 100-row CSV.

C. ``InMemoryStore.get_pt_classifications_bulk`` returns a dict
   keyed by product_id for present classifications and omits
   missing ones.

D. ``_project_response`` uses the bulk classification fetch, not the
   per-product loop. Verified by spying on a wrapper store and
   asserting ``get_pt_classification`` was never called from the
   route while ``get_pt_classifications_bulk`` was called.

E. Synthetic 1050-row CSV ingest completes promptly (well under
   a 10-second envelope) under the in-memory store. This is the
   sanity test that the new bulk path doesn't regress; the real
   gain is on Postgres where each HTTP round-trip costs ~50ms.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, ClassificationSource, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
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


def _make_product(name: str, project_id: UUID, upload_id: UUID) -> NormalizedProduct:
    from datetime import UTC, datetime

    return NormalizedProduct(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        row_number=1,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        weight_per_item_kg=Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("1")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A. add_products_bulk in InMemoryStore
# ---------------------------------------------------------------------------


class TestAddProductsBulk:
    def test_bulk_insert_persists_all_products(
        self, store: InMemoryStore
    ) -> None:
        pid = uuid4()
        uid = uuid4()
        products = [_make_product(f"P{i}", pid, uid) for i in range(50)]
        store.add_products_bulk(products)
        listed = store.list_products_for_project(pid)
        assert len(listed) == 50

    def test_bulk_insert_empty_list_is_noop(
        self, store: InMemoryStore
    ) -> None:
        store.add_products_bulk([])
        assert len(store.products) == 0


# ---------------------------------------------------------------------------
# B. ingest_upload uses the bulk path
# ---------------------------------------------------------------------------


class TestIngestUsesBulk:
    def test_100_row_csv_calls_add_products_bulk_not_per_row(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Wrap store methods to count calls.
        bulk_calls: list[int] = []
        single_calls: list[None] = []
        original_bulk = store.add_products_bulk
        original_single = store.add_product

        def spied_bulk(products):  # type: ignore[no-untyped-def]
            bulk_calls.append(len(products))
            return original_bulk(products)

        def spied_single(product):  # type: ignore[no-untyped-def]
            single_calls.append(None)
            return original_single(product)

        store.add_products_bulk = spied_bulk  # type: ignore[method-assign]
        store.add_product = spied_single  # type: ignore[method-assign]
        try:
            r = client.post(
                "/api/v1/projects",
                json={
                    "name": "p34w",
                    "methodologies_enabled": ["protein_tracker"],
                    "reporting_period_label": "FY 2024",
                },
            )
            pid = r.json()["id"]
            header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
            body = b"".join(
                f"Tofu Lot {i},150,1.0\n".encode() for i in range(100)
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
            assert r_up.status_code == 201
            assert r_up.json()["row_count"] == 100
            # The bulk path was used.
            assert bulk_calls, "ingest_upload didn't call add_products_bulk"
            assert sum(bulk_calls) == 100
            # The per-product path was NOT used during ingestion. (It
            # may still be called elsewhere — we only assert ingest
            # itself uses bulk.)
            assert len(single_calls) == 0, (
                "ingest still calls add_product per row"
            )
        finally:
            store.add_products_bulk = original_bulk  # type: ignore[method-assign]
            store.add_product = original_single  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# C. get_pt_classifications_bulk
# ---------------------------------------------------------------------------


class TestPtClassificationsBulk:
    def test_returns_dict_of_present_classifications(
        self, store: InMemoryStore
    ) -> None:
        from datetime import UTC, datetime

        ids = [uuid4() for _ in range(5)]
        # Seed classifications for 3 of the 5.
        for i in range(3):
            store.pt_classifications[ids[i]] = (
                ProteinTrackerProductClassification(
                    product_id=ids[i],
                    pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                    source=ClassificationSource.AI,
                    confidence=Decimal("0.9"),
                    rule_id=None,
                    ai_prompt_version="v1",
                    ai_model="test",
                    updated_at=datetime.now(UTC),
                )
            )
        out = store.get_pt_classifications_bulk(ids)
        assert len(out) == 3
        assert set(out.keys()) == set(ids[:3])

    def test_empty_input_returns_empty_dict(
        self, store: InMemoryStore
    ) -> None:
        assert store.get_pt_classifications_bulk([]) == {}


# ---------------------------------------------------------------------------
# D. _project_response uses the bulk path (no per-product calls)
# ---------------------------------------------------------------------------


class TestProjectResponseUsesBulk:
    def test_get_project_does_not_call_get_pt_classification_per_product(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Seed 50 PT-eligible products.
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34w2",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        pid = UUID(r.json()["id"])
        header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
        body = b"".join(
            f"Tofu Lot {i},150,1.0\n".encode() for i in range(50)
        )
        mapping = (
            '{"product_name_fr": "product_name",'
            ' "poids_unitaire_produit_g": "weight_per_item_g",'
            ' "volume": "items_purchased"}'
        )
        client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", header + body, "text/csv")},
            data={"column_mapping": mapping},
        )
        # Spy on the two lookup methods.
        per_product_calls: list[None] = []
        bulk_calls: list[int] = []
        original_pp = store.get_pt_classification
        original_bulk = store.get_pt_classifications_bulk

        def spied_pp(pid):  # type: ignore[no-untyped-def]
            per_product_calls.append(None)
            return original_pp(pid)

        def spied_bulk(ids):  # type: ignore[no-untyped-def]
            bulk_calls.append(len(ids))
            return original_bulk(ids)

        store.get_pt_classification = spied_pp  # type: ignore[method-assign]
        store.get_pt_classifications_bulk = spied_bulk  # type: ignore[method-assign]
        try:
            r_get = client.get(f"/api/v1/projects/{pid}")
            assert r_get.status_code == 200
            # New path: ONE bulk call, ZERO per-product calls in
            # _project_response. (Other routes may still call
            # get_pt_classification — we only assert the project
            # detail no longer does.)
            assert len(bulk_calls) >= 1
            assert per_product_calls == [], (
                f"project detail still calls get_pt_classification "
                f"{len(per_product_calls)} times (N+1 regression)"
            )
        finally:
            store.get_pt_classification = original_pp  # type: ignore[method-assign]
            store.get_pt_classifications_bulk = original_bulk  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# E. 1050-row CSV ingestion completes within wall-time budget
# ---------------------------------------------------------------------------


class TestLargeIngestPerf:
    def test_1050_row_ingest_completes_within_envelope(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34w3",
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
        t0 = time.perf_counter()
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", header + body, "text/csv")},
            data={"column_mapping": mapping},
        )
        elapsed = time.perf_counter() - t0
        assert r_up.status_code == 201
        # The in-memory store is fast; Postgres bears the real
        # ~60s→<2s improvement. Here we assert a generous envelope.
        assert elapsed < 10.0, (
            f"1050-row ingest took {elapsed:.2f}s on in-memory store — "
            "the bulk path likely regressed"
        )

    def test_1050_row_project_detail_completes_within_envelope(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34w4",
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
        client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", header + body, "text/csv")},
            data={"column_mapping": mapping},
        )
        t0 = time.perf_counter()
        r_get = client.get(f"/api/v1/projects/{pid}")
        elapsed = time.perf_counter() - t0
        assert r_get.status_code == 200
        # The previous N+1 path took ~95s in production for this
        # size. With the bulk fetch we expect well under 5s even on
        # cold cache, and trivially under 1s in-memory.
        assert elapsed < 5.0, (
            f"GET /projects/{{id}} took {elapsed:.2f}s on in-memory store — "
            "N+1 regression?"
        )

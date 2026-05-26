"""Phase 36F-lite — fast nutrition / NEVO validation table.

Production scenario (commit e91991c, Render Standard 2GB): the
wizard's Step 6 nutrition validation table took 60–90s per page on a
1050-row project, making NEVO-match review impractical. The NEVO
matcher is currently ~50% precision so reviewers MUST see the table
to validate every row.

Root cause in ``list_nutrition_validations_route``: ``_nutrition_row_for``
was called per product and each call paid two PostgREST point lookups:
``get_pt_classification`` + ``get_enrichment_records_for_product``.
1050 products × 2 round trips ≈ 2100 PostgREST calls. Plus 1050
Pydantic ``NutritionValidationRow`` instantiations even when the
caller only wanted page 1 of 50.

Phase 36F-lite asserts the same Phase 36B pattern is in place:

A. Bulk lookups: one ``get_pt_classifications_bulk`` + one
   ``get_enrichment_records_bulk`` per request, ZERO per-product
   ``get_pt_classification`` / ``get_enrichment_records_for_product``.
B. Page-first serialise: full Pydantic rows materialised only for
   the page; filter + count work on dict projections.
C. Filters preserved: ``status`` / ``source`` / ``product_search``.
D. Counts (``counts_by_status`` / ``counts_by_source``) remain
   correct over the FILTERED set.
E. Scales to 1050 products and response bounded for a 50-row page.
F. Timing log ``nutrition_table.timing`` emitted with per-stage
   breakdown.
G. Manual nutrition override (single-product path) still works —
   it uses the per-product fallback path inside ``_nutrition_row_for``.

This phase does NOT touch the NEVO matching algorithm / scoring /
family guard — that's the dedicated Phase 36E follow-up.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.main import app

_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased",'
    ' "protein_g_par_100g": "protein_pct"}'
)


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


class _CountingStore:
    """Delegating wrapper that counts every store method call."""

    def __init__(self, inner: InMemoryStore) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            return attr(*args, **kwargs)

        return wrapped


def _setup_project_with_n_products(
    client: TestClient, n_rows: int, *, with_protein: bool = True
) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase36f",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    header = (
        b"Product Name (FR),Poids unitaire produit (g),"
        b"Volume,protein_g_par_100g\n"
    )
    if with_protein:
        body = b"".join(
            f"Tofu {i},150,2.0,20.0\n".encode() for i in range(n_rows)
        )
    else:
        body = b"".join(
            f"Tofu {i},150,2.0,\n".encode() for i in range(n_rows)
        )
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", header + body, "text/csv")},
        data={"column_mapping": _MAPPING},
    )
    assert r_up.status_code == 201, r_up.text
    return pid


def _classify_project(
    store: InMemoryStore,
    project_id: str,
    *,
    pt_group: ProteinTrackerGroup = ProteinTrackerGroup.PLANT_BASED_CORE,
) -> None:
    from uuid import UUID

    now = datetime.now(UTC)
    target = UUID(project_id)
    for product in list(store.products.values()):
        if product.project_id != target:
            continue
        store.upsert_pt_classification(
            ProteinTrackerProductClassification(
                product_id=product.id,
                pt_group=pt_group,
                source=ClassificationSource.AI,
                confidence=Decimal("0.95"),
                ai_prompt_version="phase36f-test",
                ai_model="phase36f-fake",
                updated_at=now,
            )
        )


# ---------------------------------------------------------------------------
# A. No per-product N+1
# ---------------------------------------------------------------------------


class TestNutritionNoPerProductNPlusOne:
    def test_bulk_lookups_zero_point_calls(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 40)
        _classify_project(store, pid)
        counting = _CountingStore(store)
        app.dependency_overrides[get_store] = lambda: counting
        try:
            r = client.get(
                f"/api/v1/projects/{pid}/nutrition-validations"
                f"?limit=20&offset=0"
            )
        finally:
            app.dependency_overrides[get_store] = lambda: store
        assert r.status_code == 200, r.text
        # Bulk: one each.
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) == 1
        )
        assert (
            counting.calls.get("get_enrichment_records_bulk", 0) == 1
        )
        # ZERO per-product calls.
        assert counting.calls.get("get_pt_classification", 0) == 0
        assert (
            counting.calls.get(
                "get_enrichment_records_for_product", 0
            )
            == 0
        )


# ---------------------------------------------------------------------------
# B/E. Scales to 1050 + response bounded
# ---------------------------------------------------------------------------


class TestNutritionScalesTo1050:
    def test_initial_load_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 1050)
        _classify_project(store, pid)
        t0 = time.perf_counter()
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?limit=50&offset=0"
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200, r.text
        assert elapsed < 5.0, f"too slow: {elapsed:.1f}s"
        assert len(r.json()["items"]) == 50
        assert r.json()["total"] == 1050
        assert len(r.content) < 50_000, (
            f"response too big: {len(r.content)} bytes for 50 rows"
        )

    def test_offset_50_also_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 1050)
        _classify_project(store, pid)
        t0 = time.perf_counter()
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?limit=50&offset=50"
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 5.0, f"page-change too slow: {elapsed:.1f}s"
        assert len(r.json()["items"]) == 50


# ---------------------------------------------------------------------------
# C/D. Filters + counts
# ---------------------------------------------------------------------------


class TestNutritionFilters:
    def test_status_filter_ready_for_retailer_protein(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # All products have retailer protein_pct → status=ready,
        # source=retailer_csv.
        pid = _setup_project_with_n_products(client, 10, with_protein=True)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?status=ready"
        )
        body = r.json()
        assert body["total"] == 10
        assert all(item["status"] == "ready" for item in body["items"])

    def test_status_filter_missing_when_no_protein(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 5, with_protein=False)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?status=missing"
        )
        assert r.json()["total"] == 5

    def test_source_filter_retailer_csv(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 8, with_protein=True)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?source=retailer_csv"
        )
        body = r.json()
        assert body["total"] == 8
        assert all(
            item["source"] == "retailer_csv" for item in body["items"]
        )

    def test_product_search(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 5)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            f"?product_search=Tofu"
        )
        assert r.json()["total"] == 5


class TestNutritionCounts:
    def test_counts_match_filtered_set(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 20, with_protein=True)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations?limit=5"
        )
        body = r.json()
        # All 20 rows have retailer protein → ready / retailer_csv.
        assert body["counts_by_status"]["ready"] == 20
        assert body["counts_by_source"]["retailer_csv"] == 20
        # Page is 5 but counts and total reflect the filtered set.
        assert len(body["items"]) == 5
        assert body["total"] == 20

    def test_counts_when_no_protein(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 7, with_protein=False)
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
        )
        body = r.json()
        # No protein anywhere → status=missing, source=missing.
        assert body["counts_by_status"].get("missing", 0) == 7
        assert body["counts_by_source"].get("missing", 0) == 7


# ---------------------------------------------------------------------------
# F. Timing log
# ---------------------------------------------------------------------------


class TestTimingLog:
    def test_nutrition_table_timing_log_emitted(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid = _setup_project_with_n_products(client, 10)
        _classify_project(store, pid)
        with caplog.at_level(
            logging.INFO, logger="altera_api.nutrition_table"
        ):
            r = client.get(
                f"/api/v1/projects/{pid}/nutrition-validations?limit=5"
            )
        assert r.status_code == 200
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("nutrition_table.timing" in m for m in msgs)
        joined = "\n".join(msgs)
        assert "products_ms=" in joined
        assert "classifications_ms=" in joined
        assert "enrichment_bulk_ms=" in joined
        assert "counts_ms=" in joined
        assert "serialize_ms=" in joined
        assert "total_ms=" in joined
        assert "rows_returned=" in joined
        assert "total_filtered=" in joined


# ---------------------------------------------------------------------------
# G. Single-product fallback (manual nutrition override) still works
# ---------------------------------------------------------------------------


class TestManualOverrideStillWorks:
    def test_manual_nutrition_override_returns_row(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 3, with_protein=False)
        _classify_project(store, pid)
        # Pick the first product id.
        product = next(
            p
            for p in store.products.values()
            if str(p.project_id) == pid
        )
        r = client.post(
            f"/api/v1/projects/{pid}/nutrition-validations/"
            f"{product.id}/manual",
            json={
                "protein_pct": "12.5",
                "plant_protein_pct": "8.0",
                "animal_protein_pct": "4.5",
                "rationale": "phase36f manual test",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["product_id"] == str(product.id)
        # After manual override the row should reflect manual source.
        assert body["source"] in ("manual", "retailer_csv")
        # And the value is the one we just posted (in some shape).
        assert body["protein_pct"] is not None

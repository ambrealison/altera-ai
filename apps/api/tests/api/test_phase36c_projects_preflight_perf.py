"""Phase 36C — GET /projects + calculation-preflight performance.

Production signals (Render Standard 2GB, commit 45940b6):

- ``GET /api/v1/projects`` occasionally spiked to 30–35s.
- ``GET /projects/{id}/calculation-preflight`` spiked to 40–46s.

Root causes:

A. ``calculation_preflight_route`` walked every PT-eligible product
   and called, per product:
     * ``store.get_pt_classification(p.id)``
     * ``store.get_enrichment_records_for_product(p.id)``
   On a 1050-row project that's 2100 PostgREST round trips at ~40ms
   each. Plus the route called ``len(store.list_nevo_entries())``
   which materialised 2300+ Pydantic ``NevoEntry`` rows for a single
   integer count.

B. ``_project_response`` (one per project on the list endpoint)
   called ``len(store.list_review_items_for_project(project.id))``,
   forcing the postgres impl to paginate the project's products
   AGAIN (after ``list_uploads_for_project`` already did) AND fetch
   ~38KB of manual_reviews rows just to call ``len()``.

This module asserts the fixes:

1. ``calculation-preflight`` uses ``get_pt_classifications_bulk`` +
   ``get_enrichment_records_bulk`` + ``count_nevo_entries`` — ZERO
   per-product ``get_pt_classification`` /
   ``get_enrichment_records_for_product`` calls.
2. ``calculation-preflight`` counts remain numerically identical
   to a controlled scenario (classified vs. unclassified products
   with / without protein, volume, weight).
3. GET ``/projects`` uses ``count_review_items_for_product_ids``
   instead of ``list_review_items_for_project`` for the count, AND
   doesn't call ``get_pt_classification`` per product anywhere in
   the response.
4. Timing logs (``calculation_preflight.timing`` /
   ``projects_list.timing``) are emitted.
5. Both endpoints scale to a 1050-product project under wall-time
   bounds in pytest.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

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
            "name": "phase36c",
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
        # Empty protein column — preflight should mark
        # missing_nutrition.
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
                ai_prompt_version="phase36c-test",
                ai_model="phase36c-fake",
                updated_at=now,
            )
        )


# ---------------------------------------------------------------------------
# A. calculation-preflight: bulk + no N+1
# ---------------------------------------------------------------------------


class TestPreflightNoNPlusOne:
    def test_preflight_uses_bulk_not_per_product(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 30)
        _classify_project(store, pid)
        counting = _CountingStore(store)
        app.dependency_overrides[get_store] = lambda: counting
        try:
            r = client.get(
                f"/api/v1/projects/{pid}/calculation-preflight"
            )
        finally:
            app.dependency_overrides[get_store] = lambda: store
        assert r.status_code == 200, r.text
        # ZERO per-product classification / enrichment calls.
        assert counting.calls.get("get_pt_classification", 0) == 0
        assert (
            counting.calls.get(
                "get_enrichment_records_for_product", 0
            )
            == 0
        )
        # ONE bulk call each, plus count_nevo_entries (cheap).
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) == 1
        )
        assert (
            counting.calls.get("get_enrichment_records_bulk", 0) == 1
        )
        assert counting.calls.get("count_nevo_entries", 0) == 1
        # And critically: not ``list_nevo_entries`` (would materialise
        # 2300+ entries).
        assert counting.calls.get("list_nevo_entries", 0) == 0


class TestPreflightCountsCorrect:
    def test_counts_when_all_classified_and_full_nutrition(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(
            client, 25, with_protein=True
        )
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        )
        body = r.json()
        assert body["total_products"] == 25
        assert body["classified_products"] == 25
        assert body["products_with_volume"] == 25
        assert body["products_with_weight"] == 25
        assert body["products_with_total_protein"] == 25
        assert body["products_ready_for_calculation"] == 25
        assert body["products_missing_classification"] == 0
        assert body["products_missing_nutrition"] == 0

    def test_counts_when_classified_but_missing_protein(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(
            client, 10, with_protein=False
        )
        _classify_project(store, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        )
        body = r.json()
        assert body["total_products"] == 10
        assert body["classified_products"] == 10
        assert body["products_with_total_protein"] == 0
        # No protein → not ready, missing_nutrition counted.
        assert body["products_ready_for_calculation"] == 0
        assert body["products_missing_nutrition"] == 10

    def test_counts_when_unclassified(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 5)
        # No classify call.
        r = client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        )
        body = r.json()
        assert body["total_products"] == 5
        assert body["classified_products"] == 0
        assert body["products_missing_classification"] == 5
        assert body["products_ready_for_calculation"] == 0


# ---------------------------------------------------------------------------
# B. GET /projects: no per-product N+1, count-only review probe
# ---------------------------------------------------------------------------


class TestProjectsListNoNPlusOne:
    def test_projects_list_uses_count_probe_not_list(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 20)
        _classify_project(store, pid)
        counting = _CountingStore(store)
        app.dependency_overrides[get_store] = lambda: counting
        try:
            r = client.get("/api/v1/projects")
        finally:
            app.dependency_overrides[get_store] = lambda: store
        assert r.status_code == 200, r.text
        # No per-product calls anywhere in the projects list.
        assert counting.calls.get("get_pt_classification", 0) == 0
        # The bulk classification probe is fine — it's how we count
        # unclassified rows efficiently.
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) >= 1
        )
        # Phase 36C — review queue count uses the head=True probe,
        # NOT the row-fetching list method.
        assert (
            counting.calls.get(
                "count_review_items_for_product_ids", 0
            )
            >= 1
        )
        assert (
            counting.calls.get("list_review_items_for_project", 0)
            == 0
        )


# ---------------------------------------------------------------------------
# C. Scales to 1050 products in pytest wall-time
# ---------------------------------------------------------------------------


class TestScalesTo1050:
    def test_preflight_1050_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 1050)
        _classify_project(store, pid)
        t0 = time.perf_counter()
        r = client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 5.0, f"preflight too slow: {elapsed:.1f}s"
        body = r.json()
        assert body["total_products"] == 1050
        assert body["classified_products"] == 1050

    def test_projects_list_1050_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, 1050)
        _classify_project(store, pid)
        t0 = time.perf_counter()
        r = client.get("/api/v1/projects")
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 5.0, (
            f"projects list too slow: {elapsed:.1f}s"
        )
        assert len(r.json()["items"]) >= 1


# ---------------------------------------------------------------------------
# D. Timing logs emitted
# ---------------------------------------------------------------------------


class TestTimingLogs:
    def test_preflight_timing_log(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid = _setup_project_with_n_products(client, 10)
        _classify_project(store, pid)
        with caplog.at_level(
            logging.INFO,
            logger="altera_api.calculation_preflight",
        ):
            r = client.get(
                f"/api/v1/projects/{pid}/calculation-preflight"
            )
        assert r.status_code == 200
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any(
            "calculation_preflight.timing" in m for m in msgs
        )
        joined = "\n".join(msgs)
        assert "list_products_ms=" in joined
        assert "classifications_bulk_ms=" in joined
        assert "enrichment_bulk_ms=" in joined
        assert "nevo_refs_ms=" in joined
        assert "loop_ms=" in joined
        assert "total_ms=" in joined

    def test_projects_list_timing_log(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _setup_project_with_n_products(client, 5)
        with caplog.at_level(
            logging.INFO, logger="altera_api.projects_list"
        ):
            r = client.get("/api/v1/projects")
        assert r.status_code == 200
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("projects_list.timing" in m for m in msgs)
        joined = "\n".join(msgs)
        assert "list_projects_ms=" in joined
        assert "per_project_summary_ms=" in joined
        assert "total_ms=" in joined


# ---------------------------------------------------------------------------
# E. Store-level: new methods behave as expected
# ---------------------------------------------------------------------------


class TestStoreLevelHelpers:
    def test_count_nevo_entries_zero_on_empty_store(
        self, store: InMemoryStore
    ) -> None:
        assert store.count_nevo_entries() == 0

    def test_count_review_items_for_product_ids_empty(
        self, store: InMemoryStore
    ) -> None:
        assert (
            store.count_review_items_for_product_ids([]) == 0
        )
        assert (
            store.count_review_items_for_product_ids([uuid4()])
            == 0
        )

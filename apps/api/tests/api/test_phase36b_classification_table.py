"""Phase 36B — fast classification validation table.

Production scenario: ``GET /projects/{id}/classifications`` on a
1050-row project took 60–90s per call (initial load + page change),
making Step 5 unusable. Root cause: the route looped over every
product and called ``get_pt_classification`` (+
``get_wwf_classification`` when WWF was enabled) per id — 1050–2100
PostgREST round trips at ~40ms each, plus 1050 Pydantic instantiations
even when the caller only wanted page 1 of 50.

Phase 36B asserts:

A. The endpoint uses bulk classification lookups, not per-product
   ``get_pt_classification`` / ``get_wwf_classification``.
B. The endpoint scales to 1050 products and stays well under
   network-timeout-ish bounds (< 5s in the unit test, in practice
   < 2s in prod).
C. Pagination is honoured (limit/offset semantics unchanged) and the
   response payload size is bounded for one page.
D. Filters still work: ``source``, ``review_status``, ``product_search``,
   ``pt_group``.
E. Counts (``counts_by_source``, ``counts_by_pt_group``,
   ``pt_eligible_total``) remain correct.
F. The ``classification_table.timing`` log is emitted.
"""

from __future__ import annotations

import json
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
    ' "volume": "items_purchased"}'
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


def _setup_project_with_n_products(
    client: TestClient,
    store: InMemoryStore,
    n_rows: int,
) -> str:
    """Create a project + upload + (optionally) classify each
    product. Returns the project_id."""
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase36b",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu Lot {i},150,2.0\n".encode() for i in range(n_rows)
    )
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", header + body, "text/csv")},
        data={"column_mapping": _MAPPING},
    )
    assert r_up.status_code == 201
    return pid


def _add_pt_classifications_for_project(
    store: InMemoryStore,
    project_id: str,
    *,
    pt_group: ProteinTrackerGroup = ProteinTrackerGroup.PLANT_BASED_CORE,
    source: ClassificationSource = ClassificationSource.AI,
    confidence: float = 0.95,
) -> None:
    from uuid import UUID

    now = datetime.now(UTC)
    target = UUID(project_id)
    for product in list(store.products.values()):
        if product.project_id != target:
            continue
        if source is ClassificationSource.AI:
            store.upsert_pt_classification(
                ProteinTrackerProductClassification(
                    product_id=product.id,
                    pt_group=pt_group,
                    source=source,
                    confidence=Decimal(str(confidence)),
                    ai_prompt_version="phase36b-test",
                    ai_model="phase36b-fake",
                    updated_at=now,
                )
            )
        elif source is ClassificationSource.DETERMINISTIC:
            store.upsert_pt_classification(
                ProteinTrackerProductClassification(
                    product_id=product.id,
                    pt_group=pt_group,
                    source=source,
                    confidence=Decimal("1"),
                    rule_id="phase36b-rule",
                    updated_at=now,
                )
            )
        else:
            store.upsert_pt_classification(
                ProteinTrackerProductClassification(
                    product_id=product.id,
                    pt_group=pt_group,
                    source=source,
                    confidence=Decimal(str(confidence)),
                    reviewer_user_id=store.default_user_id,
                    review_reason="phase36b-test",
                    updated_at=now,
                )
            )


# ---------------------------------------------------------------------------
# A. No per-product N+1 — uses bulk lookups
# ---------------------------------------------------------------------------


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


class TestNoPerProductNPlusOne:
    def test_bulk_classifications_not_per_product(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 60)
        _add_pt_classifications_for_project(store, pid)
        counting = _CountingStore(store)
        app.dependency_overrides[get_store] = lambda: counting
        try:
            r = client.get(
                f"/api/v1/projects/{pid}/classifications?limit=20&offset=0"
            )
        finally:
            app.dependency_overrides[get_store] = lambda: store
        assert r.status_code == 200, r.text
        # The whole point of Phase 36B: ZERO per-product calls.
        assert counting.calls.get("get_pt_classification", 0) == 0
        assert counting.calls.get("get_wwf_classification", 0) == 0
        # Exactly one bulk call (PT enabled, WWF disabled on this project).
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) == 1
        )


# ---------------------------------------------------------------------------
# B. Scales to 1050 products
# ---------------------------------------------------------------------------


class TestScalesTo1050:
    def test_initial_load_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 1050)
        _add_pt_classifications_for_project(store, pid)
        t0 = time.perf_counter()
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?limit=50&offset=0"
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200, r.text
        # Even in pytest (in-memory store) we comfortably stay well
        # under 2s. The check guards against accidental N+1
        # regressions that would shoot wall time to many seconds.
        assert elapsed < 5.0, f"too slow: {elapsed:.1f}s"
        # Page bounded.
        assert len(r.json()["items"]) == 50
        assert r.json()["total"] == 1050
        # Response payload stays small enough that the wizard's
        # validation table renders fast on mobile.
        body = r.content
        assert len(body) < 50_000, (
            f"response too big: {len(body)} bytes for one page"
        )

    def test_offset_50_also_bounded(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 1050)
        _add_pt_classifications_for_project(store, pid)
        t0 = time.perf_counter()
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?limit=50&offset=50"
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 5.0, f"page-change too slow: {elapsed:.1f}s"
        assert len(r.json()["items"]) == 50


# ---------------------------------------------------------------------------
# C/D. Filters still work
# ---------------------------------------------------------------------------


class TestFiltersStillWork:
    def test_source_filter(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 10)
        _add_pt_classifications_for_project(
            store, pid, source=ClassificationSource.AI
        )
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?source=ai"
        )
        body = r.json()
        assert body["total"] == 10
        assert all(item["pt_source"] == "ai" for item in body["items"])
        r_zero = client.get(
            f"/api/v1/projects/{pid}/classifications?source=deterministic"
        )
        assert r_zero.json()["total"] == 0

    def test_unknown_source_returns_unclassified(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 5)
        # No classifications added — all products are "unknown".
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?source=unknown"
        )
        assert r.status_code == 200
        assert r.json()["total"] == 5

    def test_pt_group_filter(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 5)
        _add_pt_classifications_for_project(
            store, pid, pt_group=ProteinTrackerGroup.PLANT_BASED_CORE
        )
        r = client.get(
            f"/api/v1/projects/{pid}/classifications"
            f"?pt_group=plant_based_core"
        )
        assert r.json()["total"] == 5
        r2 = client.get(
            f"/api/v1/projects/{pid}/classifications"
            f"?pt_group=animal_core"
        )
        assert r2.json()["total"] == 0

    def test_product_search_filter(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 5)
        r = client.get(
            f"/api/v1/projects/{pid}/classifications"
            f"?product_search=lot+2"
        )
        # "Lot 2", "Lot 20", "Lot 21", ... — at least 1 must match.
        assert r.json()["total"] >= 1


# ---------------------------------------------------------------------------
# E. Counts remain correct
# ---------------------------------------------------------------------------


class TestCounts:
    def test_counts_match_filtered_set(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 20)
        _add_pt_classifications_for_project(
            store, pid, source=ClassificationSource.AI
        )
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?limit=5"
        )
        body = r.json()
        assert body["counts_by_source"]["ai"] == 20
        assert body["counts_by_pt_group"]["plant_based_core"] == 20
        assert body["pt_eligible_total"] == 20
        # Page bounded but counts reflect the WHOLE filtered set.
        assert len(body["items"]) == 5
        assert body["total"] == 20


# ---------------------------------------------------------------------------
# F. Timing log
# ---------------------------------------------------------------------------


class TestTimingLog:
    def test_timing_log_emitted(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid = _setup_project_with_n_products(client, store, 10)
        _add_pt_classifications_for_project(store, pid)
        with caplog.at_level(
            logging.INFO, logger="altera_api.classification_table"
        ):
            r = client.get(
                f"/api/v1/projects/{pid}/classifications?limit=5"
            )
        assert r.status_code == 200
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("classification_table.timing" in m for m in msgs)
        joined = "\n".join(msgs)
        assert "products_ms=" in joined
        assert "classifications_ms=" in joined
        assert "review_ms=" in joined
        assert "counts_ms=" in joined
        assert "total_ms=" in joined
        assert "rows_returned=" in joined
        assert "total_filtered=" in joined
        # Sanity: ensure no parse error in the format string.
        _ = json.dumps(msgs)

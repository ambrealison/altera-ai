"""Phase 35-perf — kill N+1 in classification create + advance.

Production scenario (Render Standard 2GB, commit 97904f7):

- ``POST /classification-jobs`` for a 1050-row upload took 126s.
  Root cause: ``_eligible_product_ids`` walked the upload's
  ``product_ids`` and called ``get_product`` + ``get_pt_classification``
  per product — 2000+ Supabase round trips per create.

- Each advance (batch_size=25) took 56–66s. Root cause:
  ``_refresh_coverage_counters`` walked all 1050 product ids and
  called ``get_pt_classification`` per product, then called
  ``list_review_items_for_project`` TWICE — ~1100 round trips per
  advance, dominated by the coverage refresh.

This module asserts the N+1 patterns are gone:

A. ``list_products_by_ids`` exists on the store and returns the
   matching products in input order.
B. ``get_wwf_classifications_bulk`` exists and mirrors PT.
C. ``_eligible_product_ids`` calls ``list_products_by_ids`` once
   and ``get_pt_classifications_bulk`` once — NOT N times each.
D. ``_refresh_coverage_counters`` calls
   ``get_pt_classifications_bulk`` once and
   ``list_review_items_for_project`` once.
E. The advance batch product fetch uses ``list_products_by_ids``.
F. ``ALTERA_AI_CLASSIFICATION_BATCH_SIZE`` env overrides the default
   when the request omits batch_size.
G. Detailed timing logs (``classify.create.timing`` /
   ``classify.advance.timing``) are emitted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    MAX_BATCH_SIZE,
    _default_batch_size,
    create_classification_job,
)
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

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


@dataclass
class _FakeProvider(ClassifierProvider):
    """Returns plant_based_core 0.95 for every row, batched."""

    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return "phase35-perf-fake"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
        rows = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row:
                continue
            rows.append(
                {
                    "id": row["id"],
                    "pt_group": "plant_based_core",
                    "confidence": 0.95,
                    "rationale": "fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="phase35-perf-fake",
        )


@pytest.fixture
def fake_provider() -> _FakeProvider:
    return _FakeProvider()


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(
    store: InMemoryStore,
    fake_provider: _FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setattr(
        "altera_api.ai.config.get_ai_provider", lambda: fake_provider
    )
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _setup_upload(client: TestClient, n_rows: int) -> tuple[str, str]:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase35-perf",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    pid = r.json()["id"]
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    rows = b"".join(
        f"Tofu Lot {i},150,2.0\n".encode() for i in range(n_rows)
    )
    csv = header + rows
    mapping = (
        '{"product_name_fr": "product_name",'
        ' "poids_unitaire_produit_g": "weight_per_item_g",'
        ' "volume": "items_purchased"}'
    )
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", csv, "text/csv")},
        data={"column_mapping": mapping},
    )
    assert r_up.status_code == 201, r_up.text
    return pid, r_up.json()["id"]


# ---------------------------------------------------------------------------
# A/B. Bulk store methods
# ---------------------------------------------------------------------------


class TestBulkStoreMethods:
    def test_list_products_by_ids_returns_in_order(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        _setup_upload(client, 5)
        # Pick a stable ordering from the store.
        all_products = list(store.products.values())
        ids = [p.id for p in all_products[:3]]
        out = store.list_products_by_ids(ids)
        assert [p.id for p in out] == ids

    def test_list_products_by_ids_skips_missing(
        self, store: InMemoryStore
    ) -> None:
        # Empty store — every id is missing.
        ghost = uuid4()
        assert store.list_products_by_ids([ghost]) == []

    def test_get_wwf_classifications_bulk_empty(
        self, store: InMemoryStore
    ) -> None:
        assert store.get_wwf_classifications_bulk([]) == {}
        assert store.get_wwf_classifications_bulk([uuid4()]) == {}


# ---------------------------------------------------------------------------
# C. _eligible_product_ids no longer N+1s
# ---------------------------------------------------------------------------


class _CountingStore:
    """Thin wrapper that delegates to a real InMemoryStore and counts
    method calls. The orchestrator must NOT call ``get_product`` or
    ``get_pt_classification`` per-id in the create path."""

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


class TestCreateNoNPlusOne:
    def test_create_uses_bulk_not_per_product(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # 60 products — a normal production upload. (Not 25/50: those are the
        # demo-golden catalogue sizes, which trigger one extra bulk recognition
        # fetch by design; this test guards the per-product N+1 invariant.)
        pid, upload_id = _setup_upload(client, 60)
        from uuid import UUID

        counting = _CountingStore(store)
        job = create_classification_job(
            counting,  # type: ignore[arg-type]
            organisation_id=store.default_org_id,
            project_id=UUID(pid),
            upload_id=UUID(upload_id),
            methodology=Methodology.PROTEIN_TRACKER,
        )
        assert job.total_products == 60
        # Bulk product fetch: ONE call regardless of N.
        assert counting.calls.get("list_products_by_ids", 0) == 1
        # Bulk classification fetch: ONE call.
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) == 1
        )
        # And critically: no per-product ``get_product`` or
        # ``get_pt_classification`` calls.
        assert counting.calls.get("get_product", 0) == 0
        assert counting.calls.get("get_pt_classification", 0) == 0


# ---------------------------------------------------------------------------
# D/E. Advance no longer N+1s
# ---------------------------------------------------------------------------


class TestAdvanceNoNPlusOne:
    def test_advance_uses_bulk_for_chunk_and_coverage(
        self,
        client: TestClient,
        store: InMemoryStore,
        fake_provider: _FakeProvider,
    ) -> None:
        from uuid import UUID

        from altera_api.api.classification_job_orchestrator import (
            advance_classification_job,
        )

        pid, upload_id = _setup_upload(client, 60)
        job = create_classification_job(
            store,
            organisation_id=store.default_org_id,
            project_id=UUID(pid),
            upload_id=UUID(upload_id),
            methodology=Methodology.PROTEIN_TRACKER,
            batch_size=25,
        )

        counting = _CountingStore(store)
        advance_classification_job(
            counting,  # type: ignore[arg-type]
            job.id,
            ai_provider=fake_provider,
        )

        # Per-batch product load: ONE bulk call.
        assert counting.calls.get("list_products_by_ids", 0) == 1
        # Coverage refresh: ONE bulk classifications call, ONE
        # review-items call (not 2 like before).
        assert (
            counting.calls.get("get_pt_classifications_bulk", 0) == 1
        )
        assert (
            counting.calls.get("list_review_items_for_project", 0)
            == 1
        )
        # The N+1 patterns must be gone.
        assert counting.calls.get("get_product", 0) == 0
        assert counting.calls.get("get_pt_classification", 0) == 0


# ---------------------------------------------------------------------------
# F. Env override for batch_size
# ---------------------------------------------------------------------------


class TestBatchSizeEnvOverride:
    def test_default_batch_size_reads_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "40")
        assert _default_batch_size() == 40

    def test_default_batch_size_clamped_to_max(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "9999"
        )
        assert _default_batch_size() == MAX_BATCH_SIZE

    def test_default_batch_size_invalid_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "not-a-number"
        )
        assert _default_batch_size() == 25

    def test_route_uses_env_default_when_omitted(
        self,
        client: TestClient,
        store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "40"
        )
        pid, upload_id = _setup_upload(client, 10)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/"
            f"classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["batch_size"] == 40

    def test_route_honours_explicit_batch_size(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid, upload_id = _setup_upload(client, 10)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/"
            f"classification-jobs",
            json={
                "methodology": "protein_tracker",
                "batch_size": 15,
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["batch_size"] == 15


# ---------------------------------------------------------------------------
# G. Timing logs are emitted
# ---------------------------------------------------------------------------


class TestTimingLogs:
    def test_create_emits_timing_log(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid, upload_id = _setup_upload(client, 10)
        with caplog.at_level(
            logging.INFO, logger="altera_api.classification_create"
        ):
            r = client.post(
                f"/api/v1/projects/{pid}/uploads/{upload_id}/"
                f"classification-jobs",
                json={"methodology": "protein_tracker"},
            )
            assert r.status_code == 201, r.text
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("classify.create.timing" in m for m in msgs)
        # Spot-check the breakdown is included.
        joined = "\n".join(msgs)
        assert "get_upload_ms=" in joined
        assert "list_products_ms=" in joined
        assert "existing_cls_ms=" in joined
        assert "add_job_ms=" in joined

    def test_advance_emits_timing_log(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid, upload_id = _setup_upload(client, 10)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/"
            f"classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        job_id = r.json()["job_id"]
        with caplog.at_level(
            logging.INFO, logger="altera_api.classification_advance"
        ):
            r2 = client.post(
                f"/api/v1/projects/{pid}/classification-jobs/"
                f"{job_id}/advance"
            )
            assert r2.status_code == 200, r2.text
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("classify.advance.timing" in m for m in msgs)
        joined = "\n".join(msgs)
        assert "provider_ms=" in joined
        assert "db_write_ms=" in joined
        assert "coverage_ms=" in joined

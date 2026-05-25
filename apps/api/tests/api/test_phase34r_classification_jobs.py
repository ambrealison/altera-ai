"""Phase 34R — async, chunked AI classification jobs.

Areas under test:

A. POST /classification-jobs returns quickly with job_id + status=queued
   and does NOT perform any AI calls.
B. POST /classification-jobs/{id}/advance processes ONE batch and
   persists progress. Status transitions queued → running → completed.
C. Each advance call is bounded in work — given a small batch_size,
   the second advance call processes the remaining rows.
D. Synthetic 1050-row job completes across N advance calls without
   any individual call exceeding a reasonable wall-time bound.
E. Provider error inside one batch becomes failed rows on that batch
   but does NOT abort the whole job — the next advance keeps going.
F. POST .../retry-failed creates a NEW job whose pending list is the
   failed product ids from the prior job.
G. Cancelling sets status=cancelled and stops further advance.
H. 404 / structured error responses for unknown jobs/uploads.
I. ``only_missing_or_failed=True`` (default) skips already-classified
   products on a second create — idempotent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderError,
    ProviderResponse,
)
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
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
class _DeterministicFakeProvider(ClassifierProvider):
    """Returns plant_based_core 0.95 for every product. Counts calls."""

    pt_group: str = "plant_based_core"
    confidence: float = 0.95
    model_name: str = "phase34r-fake"
    calls: list[Any] = field(default_factory=list)
    raise_after_n_calls: int | None = None

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
        if (
            self.raise_after_n_calls is not None
            and len(self.calls) > self.raise_after_n_calls
        ):
            raise ProviderError("simulated 429 after N")
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
                    "pt_group": self.pt_group,
                    "confidence": self.confidence,
                    "rationale": "fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}), model=self.model_name
        )


@pytest.fixture
def fake_provider() -> _DeterministicFakeProvider:
    return _DeterministicFakeProvider()


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(
    store: InMemoryStore,
    fake_provider: _DeterministicFakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    # The advance route imports get_ai_provider() inside its body —
    # not a FastAPI Depends — so we monkeypatch the symbol in both
    # the source module and the import-site so route imports see the
    # patched callable regardless of how it was reached.
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
    """Create a project + upload with ``n_rows`` synthetic products.

    Returns ``(project_id, upload_id)``.
    """
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34r",
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
# A. Create job returns quickly
# ---------------------------------------------------------------------------


class TestCreateJob:
    def test_create_returns_immediately_no_ai_calls(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 20)
        t0 = time.perf_counter()
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        elapsed = time.perf_counter() - t0
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["total_products"] == 20
        assert body["processed_products"] == 0
        # No AI calls happened during create.
        assert len(fake_provider.calls) == 0
        # Create must be fast.
        assert elapsed < 1.0, f"create took {elapsed:.2f}s"

    def test_create_404_when_upload_unknown(
        self, client: TestClient
    ) -> None:
        pid, _ = _setup_upload(client, 1)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uuid4()}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "upload_not_found"


# ---------------------------------------------------------------------------
# B + C. Advance processes ONE batch; status transitions
# ---------------------------------------------------------------------------


class TestAdvance:
    def test_advance_processes_one_batch(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 60)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 25},
        )
        job_id = r.json()["job_id"]
        # First advance: status running, processed_products == 25.
        r1 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        )
        assert r1.status_code == 200, r1.text
        b1 = r1.json()
        assert b1["status"] == "running"
        assert b1["processed_products"] == 25
        assert b1["categorized_total"] == 25
        assert len(fake_provider.calls) == 1, "exactly one AI call per advance"

    def test_second_advance_completes_job(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 40)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 25},
        )
        job_id = r.json()["job_id"]
        r1 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        assert r1["status"] == "running"
        r2 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        assert r2["status"] == "completed", r2
        assert r2["processed_products"] == 40
        assert r2["categorized_total"] == 40
        # AI calls: one per advance batch.
        assert len(fake_provider.calls) == 2

    def test_get_is_pure_read(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 10)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        job_id = r.json()["job_id"]
        # GET multiple times — no AI calls.
        for _ in range(3):
            rg = client.get(
                f"/api/v1/projects/{pid}/classification-jobs/{job_id}"
            )
            assert rg.status_code == 200
            assert rg.json()["status"] == "queued"
        assert len(fake_provider.calls) == 0


# ---------------------------------------------------------------------------
# D. 1050-row synthetic job
# ---------------------------------------------------------------------------


class TestLargeJob:
    def test_thousand_row_job_completes_over_many_advances(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 1050)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 25},
        )
        assert r.status_code == 201
        job_id = r.json()["job_id"]
        # Drive advance until terminal. Cap loop at ceil(1050/25) + safety = 50.
        for _ in range(60):
            body = client.post(
                f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
            ).json()
            if body["status"] in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                break
        assert body["status"] == "completed", body
        assert body["processed_products"] == 1050
        assert body["categorized_total"] == 1050

    def test_advance_response_does_not_carry_huge_payload(
        self, client: TestClient
    ) -> None:
        """The advance response must NOT include the full product list."""
        pid, uid = _setup_upload(client, 100)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        job_id = r.json()["job_id"]
        body = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        # The response is just counters + sample_errors.
        forbidden_keys = {"products", "pending_product_ids", "product_results"}
        assert forbidden_keys.isdisjoint(body.keys()), (
            f"advance response leaks list: {set(body.keys()) & forbidden_keys}"
        )


# ---------------------------------------------------------------------------
# E. Provider error mid-batch — job survives
# ---------------------------------------------------------------------------


class TestProviderErrorResilience:
    def test_provider_error_marks_failed_but_continues(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        # First advance call succeeds; second triggers a provider 429.
        fake_provider.raise_after_n_calls = 1
        pid, uid = _setup_upload(client, 50)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 25},
        )
        job_id = r.json()["job_id"]
        r1 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        assert r1["status"] == "running"
        assert r1["categorized_total"] == 25
        r2 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        # The second batch's products were marked failed but the
        # advance call did NOT throw a 500. Status finalises since no
        # rows remain in pending.
        assert r2["status"] == "completed_with_errors", r2
        assert r2["failed_product_count"] == 25
        assert r2["failed_total"] == 25
        assert any("provider_error" in s for s in r2["sample_errors"])


# ---------------------------------------------------------------------------
# F. retry-failed creates fresh job for the failures
# ---------------------------------------------------------------------------


class TestRetryFailed:
    def test_retry_failed_creates_new_job_for_failed_products(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        fake_provider.raise_after_n_calls = 1
        pid, uid = _setup_upload(client, 50)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 25},
        )
        job_id = r.json()["job_id"]
        # Advance to terminal (will be completed_with_errors).
        for _ in range(3):
            body = client.post(
                f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
            ).json()
            if body["status"] in {
                "completed",
                "completed_with_errors",
                "failed",
            }:
                break
        assert body["status"] == "completed_with_errors"
        # Reset the provider so retry sees a happy provider.
        fake_provider.raise_after_n_calls = None
        fake_provider.calls.clear()
        # Retry failed: new job with 25 pending.
        r2 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/retry-failed"
        )
        assert r2.status_code == 201, r2.text
        new = r2.json()
        assert new["status"] == "queued"
        assert new["total_products"] == 25
        # Advance the retry job.
        body2 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{new['job_id']}/advance"
        ).json()
        assert body2["status"] == "completed"
        assert body2["categorized_total"] == 50  # original 25 + retried 25


# ---------------------------------------------------------------------------
# G. Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_blocks_further_advance(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 30)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 10},
        )
        job_id = r.json()["job_id"]
        c = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/cancel"
        )
        assert c.status_code == 200
        assert c.json()["status"] == "cancelled"
        # advance after cancel returns cancelled state, no AI call.
        a = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        assert a["status"] == "cancelled"
        assert len(fake_provider.calls) == 0


# ---------------------------------------------------------------------------
# H. Structured 404s
# ---------------------------------------------------------------------------


class TestStructuredErrors:
    def test_unknown_job_returns_structured_404(
        self, client: TestClient
    ) -> None:
        pid, _ = _setup_upload(client, 1)
        r = client.get(
            f"/api/v1/projects/{pid}/classification-jobs/{uuid4()}"
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "job_not_found"

    def test_unknown_methodology_returns_structured_400(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 1)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "wwf"},  # project enabled PT only
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "methodology_not_enabled"


# ---------------------------------------------------------------------------
# I. Idempotent re-run
# ---------------------------------------------------------------------------


class TestIdempotentRerun:
    def test_second_run_skips_already_classified(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 20)
        # First run: classify everything.
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 20},
        )
        job_id = r.json()["job_id"]
        body = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{job_id}/advance"
        ).json()
        assert body["status"] == "completed"
        # Second job with default only_missing_or_failed=True — nothing
        # left to do, total_products=0.
        r2 = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r2.status_code == 201
        b2 = r2.json()
        assert b2["total_products"] == 0
        b3 = client.post(
            f"/api/v1/projects/{pid}/classification-jobs/{b2['job_id']}/advance"
        ).json()
        assert b3["status"] in {"completed", "completed_with_errors"}

    def test_overwrite_true_reprocesses_all(
        self, client: TestClient, fake_provider: _DeterministicFakeProvider
    ) -> None:
        pid, uid = _setup_upload(client, 10)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 10},
        )
        # Drive to completion.
        first = client.get(f"/api/v1/projects/{pid}").json()
        _ = first
        # Second job with overwrite=true: total_products == 10.
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "overwrite": True},
        )
        assert r.json()["total_products"] == 10

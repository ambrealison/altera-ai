"""Phase 35 — resume endpoint + heavy-job guard.

Areas under test:

A. ``GET /classification-jobs/active?upload_id=…&methodology=…``
   returns the most-recent non-terminal job, or 404 with
   ``error_code=no_active_job``.

B. Creating a second classification job for the SAME (upload,
   methodology) while one is already non-terminal returns 409 with
   ``error_code=classification_job_active`` and a ``job_id``
   pointing at the existing job — the frontend uses this to
   auto-resume.

C. Heavy-job guard: a 1000-row upload's classification creation
   is rejected when another heavy classification or ingestion job
   is already non-terminal in the caller's RLS scope. Returns
   409 ``error_code=heavy_job_in_progress``.

D. Heavy-job guard does NOT block small jobs (50 products).

E. Heavy-job guard does NOT block resume of the same job (which
   is short-circuited by the resume check before the heavy guard).

F. ``find_active_classification_job`` returns None after every
   non-terminal job has been driven to a terminal state.

G. Advance log line is emitted with the documented fields
   (smoke check via capsys / log capture).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    Methodology,
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
def client(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # Fake provider that always succeeds so create_classification_job
    # has a real provider when advance is called.
    import json

    from altera_api.ai.provider import (
        ClassifierProvider,
        ProviderResponse,
    )

    class _FakeProvider(ClassifierProvider):
        @property
        def model(self) -> str:
            return "fake"

        def supports_batch(self) -> bool:
            return True

        def classify(self, prompt: Any):
            raise NotImplementedError

        def batch_classify(self, prompt: Any):
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
                        "rationale": "ok",
                    }
                )
            return ProviderResponse(
                raw_text=json.dumps({"results": rows}), model="fake"
            )

    monkeypatch.setattr(
        "altera_api.ai.config.get_ai_provider", lambda: _FakeProvider()
    )
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _setup_upload(client: TestClient, n: int) -> tuple[str, str]:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "p35",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    pid = r.json()["id"]
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu Lot {i},150,1.0\n".encode() for i in range(n)
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
    return pid, r_up.json()["id"]


# ---------------------------------------------------------------------------
# A. Active-job endpoint
# ---------------------------------------------------------------------------


class TestActiveJobEndpoint:
    def test_returns_404_when_no_active_job(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 5)
        r = client.get(
            f"/api/v1/projects/{pid}/classification-jobs/active"
            f"?upload_id={uid}&methodology=protein_tracker"
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "no_active_job"

    def test_returns_job_when_non_terminal_exists(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 5)
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        ).json()
        r = client.get(
            f"/api/v1/projects/{pid}/classification-jobs/active"
            f"?upload_id={uid}&methodology=protein_tracker"
        )
        assert r.status_code == 200
        assert r.json()["job_id"] == created["job_id"]

    def test_returns_404_after_job_completes(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 5)
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 5},
        ).json()
        jid = created["job_id"]
        # Drive to completion.
        for _ in range(5):
            body = client.post(
                f"/api/v1/projects/{pid}/classification-jobs/{jid}/advance"
            ).json()
            if body["status"] in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                break
        r = client.get(
            f"/api/v1/projects/{pid}/classification-jobs/active"
            f"?upload_id={uid}&methodology=protein_tracker"
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# B. Resume short-circuit
# ---------------------------------------------------------------------------


class TestResumeShortCircuit:
    def test_second_create_returns_409_with_existing_job_id(
        self, client: TestClient
    ) -> None:
        pid, uid = _setup_upload(client, 5)
        first = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        ).json()
        # Second create with same (upload, methodology) → 409.
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error_code"] == "classification_job_active"
        assert detail["job_id"] == first["job_id"]
        assert detail["status"] in {"queued", "running"}


# ---------------------------------------------------------------------------
# C + D. Heavy-job guard
# ---------------------------------------------------------------------------


class TestHeavyJobGuard:
    def test_heavy_job_blocks_another_heavy_classification(
        self, client: TestClient
    ) -> None:
        # Project A with 500+ products → heavy. Create its job.
        pid_a, uid_a = _setup_upload(client, 500)
        client.post(
            f"/api/v1/projects/{pid_a}/uploads/{uid_a}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        # Project B with 500+ products → also heavy. Second creation
        # should be rejected with heavy_job_in_progress.
        pid_b, uid_b = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid_b}/uploads/{uid_b}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "heavy_job_in_progress"
        assert detail["active_classification_jobs"] >= 1

    def test_small_job_is_not_blocked_by_active_heavy(
        self, client: TestClient
    ) -> None:
        # One heavy classification.
        pid_a, uid_a = _setup_upload(client, 500)
        client.post(
            f"/api/v1/projects/{pid_a}/uploads/{uid_a}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        # Small (100-product) job on another project — must be admitted.
        pid_b, uid_b = _setup_upload(client, 100)
        r = client.post(
            f"/api/v1/projects/{pid_b}/uploads/{uid_b}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# E. Resume short-circuit beats heavy-job guard
# ---------------------------------------------------------------------------


class TestResumeBeatsHeavyGuard:
    def test_resuming_own_job_is_not_blocked_by_heavy_guard(
        self, client: TestClient
    ) -> None:
        # User has one heavy classification active. Trying to create
        # another job for the SAME upload+methodology should NOT
        # surface as heavy_job_in_progress — it should surface as
        # classification_job_active (resume) so the wizard knows to
        # auto-resume rather than tell the user to wait.
        pid, uid = _setup_upload(client, 500)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 409
        # Must be the resume code, not the heavy-job code.
        assert (
            r.json()["detail"]["error_code"] == "classification_job_active"
        )


# ---------------------------------------------------------------------------
# F. find_active_classification_job (in-memory)
# ---------------------------------------------------------------------------


class TestFindActiveClassificationJob:
    def test_returns_none_when_no_jobs(self, store: InMemoryStore) -> None:

        assert (
            store.find_active_classification_job(
                upload_id=uuid4(), methodology=Methodology.PROTEIN_TRACKER
            )
            is None
        )

    def test_returns_most_recent_non_terminal_job(
        self, store: InMemoryStore
    ) -> None:
        from altera_api.domain.classification_job import (
            ClassificationJob,
            ClassificationJobStatus,
        )

        uid = uuid4()
        # One terminal, one running. Running wins.
        store.add_classification_job(
            ClassificationJob(
                id=uuid4(),
                organisation_id=uuid4(),
                project_id=uuid4(),
                upload_id=uid,
                methodology=Methodology.PROTEIN_TRACKER,
                status=ClassificationJobStatus.COMPLETED,
                total_products=10,
                created_at=datetime.now(UTC),
            )
        )
        running_id = uuid4()
        store.add_classification_job(
            ClassificationJob(
                id=running_id,
                organisation_id=uuid4(),
                project_id=uuid4(),
                upload_id=uid,
                methodology=Methodology.PROTEIN_TRACKER,
                status=ClassificationJobStatus.RUNNING,
                total_products=10,
                created_at=datetime.now(UTC),
            )
        )
        out = store.find_active_classification_job(
            upload_id=uid, methodology=Methodology.PROTEIN_TRACKER
        )
        assert out is not None
        assert out.id == running_id


# ---------------------------------------------------------------------------
# G. Advance log line is emitted (Phase 35D smoke)
# ---------------------------------------------------------------------------


class TestAdvanceLogEmitted:
    def test_advance_emits_structured_log_line(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        pid, uid = _setup_upload(client, 5)
        created = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker", "batch_size": 5},
        ).json()
        with caplog.at_level(
            logging.INFO, logger="altera_api.classification_advance"
        ):
            client.post(
                f"/api/v1/projects/{pid}/classification-jobs/"
                f"{created['job_id']}/advance"
            )
        # At least one ok-line for this advance.
        ok_lines = [
            r
            for r in caplog.records
            if "advance.ok" in r.getMessage()
        ]
        assert ok_lines, "no advance.ok log line emitted"
        msg = ok_lines[-1].getMessage()
        # Documented fields are all present in the format string.
        for token in (
            "job_id=",
            "org=",
            "project=",
            "upload=",
            "batch_size=",
            "processed=",
            "total=",
            "duration_ms=",
        ):
            assert token in msg, (
                f"advance.ok log missing field {token!r}: {msg!r}"
            )

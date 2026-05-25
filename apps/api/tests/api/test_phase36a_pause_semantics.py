"""Phase 36A — paused vs running heavy-job semantics.

Before Phase 36A the heavy-job guard treated ANY classification or
ingestion job in ``queued``/``running`` whose ``updated_at`` was within
``_HEAVY_JOB_STALE_MINUTES`` (30 min) as a blocker. That meant a job
whose browser tab had been closed 5 min ago — strictly paused, no
advance running — still locked out every new heavy job on the platform.

Phase 36A splits the spectrum into three buckets keyed by idle age:

- ``active``  — advanced within ``_HEAVY_JOB_ACTIVE_MINUTES`` (default
  2 min). Real worker traffic; still blocks.
- ``paused``  — between active and stale windows. Resumable (same-
  upload short-circuit still finds it via Phase 35A) but NOT a global
  blocker.
- ``stale``   — > ``_HEAVY_JOB_STALE_MINUTES``. Auto-cancelled by the
  self-heal pass (Phase 35-stale).

Tests below cover:

A. Paused classification (idle 5 min) does NOT block a new heavy
   classification on another upload.
B. Recent-advance running classification (idle < active window) DOES
   block.
C. Same-upload paused job is still proposed for resume (Phase 35A
   resume short-circuit wins over the guard regardless of idle age).
D. Stale jobs continue to be ignored — Phase 35-stale behaviour
   intact.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.classification_job import (
    ClassificationJob,
    ClassificationJobStatus,
)
from altera_api.domain.common import (
    AlteraRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
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


def _paused_classification_job(
    *, minutes_idle: float = 5.0, total: int = 500
) -> ClassificationJob:
    """Build a queued/running classification job whose updated_at is
    ``minutes_idle`` minutes ago — beyond the 2-min active window but
    short of the 30-min stale window."""
    idle = datetime.now(UTC) - timedelta(minutes=minutes_idle)
    return ClassificationJob(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        methodology=Methodology.PROTEIN_TRACKER,
        status=ClassificationJobStatus.RUNNING,
        total_products=total,
        processed_products=100,
        created_at=idle,
        updated_at=idle,
    )


def _setup_upload(
    client: TestClient, n_rows: int
) -> tuple[str, str]:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase36a",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    header = b"Product Name (FR),Poids unitaire produit (g),Volume\n"
    body = b"".join(
        f"Tofu {i},150,2.0\n".encode() for i in range(n_rows)
    )
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", header + body, "text/csv")},
        data={"column_mapping": _MAPPING},
    )
    assert r_up.status_code == 201
    return pid, r_up.json()["id"]


# ---------------------------------------------------------------------------
# A. Paused classification does NOT block another heavy job
# ---------------------------------------------------------------------------


class TestPausedDoesNotBlock:
    def test_paused_class_5min_old_does_not_block_new_class(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Plant a paused classification (5 min idle — past active
        # window, before stale window).
        store.add_classification_job(
            _paused_classification_job(minutes_idle=5)
        )
        # Now try a fresh heavy classification on a DIFFERENT upload.
        # Pre-Phase-36A this would have been 409 heavy_job_in_progress.
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text

    def test_paused_class_just_outside_active_window(
        self,
        client: TestClient,
        store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Just outside the 2-min active window.
        store.add_classification_job(
            _paused_classification_job(minutes_idle=3)
        )
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# B. Active (recent advance) DOES block
# ---------------------------------------------------------------------------


class TestActiveStillBlocks:
    def test_recent_advance_within_active_window_blocks(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # 30 seconds idle — well within the 2-min active window.
        store.add_classification_job(
            _paused_classification_job(minutes_idle=0.5)
        )
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["detail"]["error_code"] == "heavy_job_in_progress"
        # Message must point to "actuellement" (currently running),
        # not the old "déjà" (which paused jobs also satisfied).
        assert "actuellement" in body["detail"]["message"]
        # And it must mention that a paused job on the user's own
        # file is still reprenable — so the user doesn't think
        # their previous work is lost.
        assert "reprenable" in body["detail"]["message"]


# ---------------------------------------------------------------------------
# C. Same-upload resume short-circuit still wins
# ---------------------------------------------------------------------------


class TestSameUploadResumeWinsOverGuard:
    def test_paused_same_upload_job_is_resumable_not_blocked(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # First: create a real heavy classification job, then
        # backdate it 5 min so it's "paused".
        pid, uid = _setup_upload(client, 500)
        first = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["job_id"]
        # Backdate the job's updated_at to simulate a paused state.
        from uuid import UUID

        job = store.get_classification_job(UUID(first_id))
        assert job is not None
        old = datetime.now(UTC) - timedelta(minutes=5)
        # Use the dataclass replace pattern via with_progress.
        store.update_classification_job(
            job.with_progress(updated_at=old)
            if hasattr(job, "with_progress")
            else job
        )
        # Now resend the same create — must surface the resume
        # short-circuit (Phase 35A), not heavy_job_in_progress.
        second = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert second.status_code == 409
        body = second.json()
        assert (
            body["detail"]["error_code"] == "classification_job_active"
        )
        assert body["detail"]["job_id"] == first_id


# ---------------------------------------------------------------------------
# D. Stale jobs still ignored / cleaned (Phase 35-stale intact)
# ---------------------------------------------------------------------------


class TestStaleStillIgnored:
    def test_stale_job_does_not_block_and_gets_healed(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # 2 hours old — past stale threshold.
        ghost = _paused_classification_job(minutes_idle=120)
        store.add_classification_job(ghost)
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text
        # Self-heal transitioned the ghost to terminal.
        healed = store.get_classification_job(ghost.id)
        assert healed is not None
        assert healed.is_terminal

"""Phase 35-stale — stale jobs no longer block new heavy jobs.

Production scenario: Render OOM-restart leaves classification /
ingestion jobs in ``queued``/``running`` state. The heavy-job guard
(Phase 35B) treated these ghosts as live blockers, locking out every
subsequent heavy job until manual cleanup.

This module asserts:

A. ``count_active_heavy_*`` with ``max_age_minutes`` filters out
   stale jobs.
B. The heavy-job guard route uses the cutoff and admits new jobs
   when only stale ones exist.
C. ``cancel_stale_*`` transitions stale jobs to terminal:
   - classification → ``cancelled``
   - ingestion → ``completed_with_errors`` if processed>=total and
     errors>0, otherwise ``cancelled``.
D. The guard's opportunistic self-heal runs on a blocked create.
E. Recent (non-stale) heavy jobs still block — the guard hasn't
   become permissive.
F. Same-upload resume short-circuit (Phase 35A) still wins over
   the heavy guard regardless of staleness.
G. Admin endpoints:
   - ``GET /admin/heavy-jobs/active`` returns lists and ages.
   - ``POST /admin/heavy-jobs/cancel-stale`` heals + returns counts.
   - Both reject non-Altera-internal callers.
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
    ClientRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.ingestion_job import (
    IngestionJob,
    IngestionJobStatus,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app


def _promote(
    store: InMemoryStore, role: AlteraRole = AlteraRole.ALTERA_ANALYST
) -> None:
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
            role=role,
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


def _stale_classification_job(
    *, hours_old: float = 2.0, total: int = 500
) -> ClassificationJob:
    """Build a queued/running classification job whose updated_at
    is in the distant past — the "OOM-restart orphan" shape."""
    old = datetime.now(UTC) - timedelta(hours=hours_old)
    return ClassificationJob(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        methodology=Methodology.PROTEIN_TRACKER,
        status=ClassificationJobStatus.RUNNING,
        total_products=total,
        processed_products=200,  # partial — looked like work-in-progress
        created_at=old,
        updated_at=old,
    )


def _stale_ingestion_job(
    *,
    hours_old: float = 2.0,
    total: int = 1050,
    processed: int = 500,
    errors: int = 0,
) -> IngestionJob:
    old = datetime.now(UTC) - timedelta(hours=hours_old)
    return IngestionJob(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        status=IngestionJobStatus.RUNNING,
        total_rows=total,
        processed_rows=processed,
        errors_total=errors,
        created_at=old,
        updated_at=old,
    )


# ---------------------------------------------------------------------------
# A. count_active_heavy_* honours max_age_minutes
# ---------------------------------------------------------------------------


class TestCountActiveWithAgeFilter:
    def test_stale_classification_not_counted_when_filter_set(
        self, store: InMemoryStore
    ) -> None:
        store.add_classification_job(_stale_classification_job())
        # No filter: counts the stale ghost.
        assert (
            store.count_active_heavy_classification_jobs(
                min_total_products=500
            )
            == 1
        )
        # With filter: doesn't.
        assert (
            store.count_active_heavy_classification_jobs(
                min_total_products=500, max_age_minutes=30
            )
            == 0
        )

    def test_recent_classification_still_counted(
        self, store: InMemoryStore
    ) -> None:
        # Brand-new running job — updated_at is now-ish.
        fresh = _stale_classification_job(hours_old=0)
        store.add_classification_job(fresh)
        assert (
            store.count_active_heavy_classification_jobs(
                min_total_products=500, max_age_minutes=30
            )
            == 1
        )

    def test_stale_ingestion_not_counted_when_filter_set(
        self, store: InMemoryStore
    ) -> None:
        store.add_ingestion_job(_stale_ingestion_job())
        assert (
            store.count_active_heavy_ingestion_jobs(min_total_rows=1000)
            == 1
        )
        assert (
            store.count_active_heavy_ingestion_jobs(
                min_total_rows=1000, max_age_minutes=30
            )
            == 0
        )


# ---------------------------------------------------------------------------
# C. cancel_stale_* transitions to terminal
# ---------------------------------------------------------------------------


class TestCancelStale:
    def test_cancel_stale_classification_marks_cancelled(
        self, store: InMemoryStore
    ) -> None:
        j = _stale_classification_job()
        store.add_classification_job(j)
        n = store.cancel_stale_classification_jobs(stale_after_minutes=30)
        assert n == 1
        stored = store.get_classification_job(j.id)
        assert stored is not None
        assert stored.status is ClassificationJobStatus.CANCELLED
        assert stored.error_code == "stale_auto_cancelled"

    def test_cancel_stale_does_not_touch_recent_jobs(
        self, store: InMemoryStore
    ) -> None:
        fresh = _stale_classification_job(hours_old=0)
        store.add_classification_job(fresh)
        assert store.cancel_stale_classification_jobs() == 0
        stored = store.get_classification_job(fresh.id)
        assert stored is not None
        assert not stored.is_terminal

    def test_cancel_stale_ingestion_handles_done_but_running(
        self, store: InMemoryStore
    ) -> None:
        # processed_rows >= total_rows + errors_total>0
        # → completed_with_errors.
        j = _stale_ingestion_job(
            total=1050, processed=1050, errors=5
        )
        store.add_ingestion_job(j)
        n = store.cancel_stale_ingestion_jobs(stale_after_minutes=30)
        assert n == 1
        stored = store.get_ingestion_job(j.id)
        assert stored is not None
        assert stored.status is IngestionJobStatus.COMPLETED_WITH_ERRORS

    def test_cancel_stale_ingestion_cancels_genuinely_stalled(
        self, store: InMemoryStore
    ) -> None:
        j = _stale_ingestion_job(
            total=1050, processed=300, errors=0
        )
        store.add_ingestion_job(j)
        store.cancel_stale_ingestion_jobs(stale_after_minutes=30)
        stored = store.get_ingestion_job(j.id)
        assert stored is not None
        assert stored.status is IngestionJobStatus.CANCELLED
        assert stored.error_code == "stale_auto_cancelled"


# ---------------------------------------------------------------------------
# B + D + E. Heavy-job guard route behaviour
# ---------------------------------------------------------------------------


_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _setup_upload(client: TestClient, n: int) -> tuple[str, str]:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "p35stale",
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
    assert r_up.status_code == 201
    return pid, r_up.json()["id"]


class TestGuardWithStaleJobs:
    def test_stale_ingestion_does_not_block_new_classification(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Plant a stale ingestion ghost.
        store.add_ingestion_job(_stale_ingestion_job(hours_old=2))
        # Now create a 500-product classification job — would previously
        # have been blocked.
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201, r.text

    def test_stale_classification_does_not_block_new(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        store.add_classification_job(_stale_classification_job(hours_old=2))
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 201

    def test_fresh_active_classification_still_blocks(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Fresh running job in same scope.
        store.add_classification_job(_stale_classification_job(hours_old=0))
        pid, uid = _setup_upload(client, 500)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "heavy_job_in_progress"

    def test_same_upload_proposes_resume_even_with_stale_others(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Plant stale ghosts (would have blocked under old guard).
        store.add_ingestion_job(_stale_ingestion_job(hours_old=2))
        store.add_classification_job(_stale_classification_job(hours_old=2))
        pid, uid = _setup_upload(client, 500)
        # Create the FIRST job — admitted because ghosts are stale.
        first = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert first.status_code == 201
        first_job_id = first.json()["job_id"]
        # Create a SECOND for the same (upload, methodology) — must
        # be resume short-circuit (Phase 35A), NOT heavy_job_in_progress.
        second = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        assert second.status_code == 409
        assert (
            second.json()["detail"]["error_code"]
            == "classification_job_active"
        )
        assert second.json()["detail"]["job_id"] == first_job_id

    def test_guard_opportunistic_self_heal_runs_on_create(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Plant 2 stale ghosts.
        stale_class = _stale_classification_job(hours_old=2)
        stale_ingest = _stale_ingestion_job(hours_old=2)
        store.add_classification_job(stale_class)
        store.add_ingestion_job(stale_ingest)
        # Trigger a heavy create. The guard's self-heal should
        # transition both ghosts to terminal.
        pid, uid = _setup_upload(client, 500)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classification-jobs",
            json={"methodology": "protein_tracker"},
        )
        # Inspect the ghosts.
        assert store.get_classification_job(stale_class.id).is_terminal  # type: ignore[union-attr]
        assert store.get_ingestion_job(stale_ingest.id).is_terminal  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# G. Admin endpoints
# ---------------------------------------------------------------------------


class TestAdminEndpoints:
    def test_admin_heavy_jobs_active_returns_lists_with_ages(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        store.add_classification_job(_stale_classification_job(hours_old=2))
        store.add_ingestion_job(_stale_ingestion_job(hours_old=2))
        r = client.get("/api/v1/admin/heavy-jobs/active")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["classification"]) == 1
        assert len(body["ingestion"]) == 1
        # Age must be sane (> 1h in seconds).
        assert body["classification"][0]["age_seconds"] > 3600
        assert body["stale_after_minutes"] == 30

    def test_admin_cancel_stale_returns_counts_and_heals(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        store.add_classification_job(_stale_classification_job(hours_old=2))
        store.add_ingestion_job(_stale_ingestion_job(hours_old=2))
        r = client.post("/api/v1/admin/heavy-jobs/cancel-stale")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cancelled_classification"] == 1
        assert body["cancelled_ingestion"] == 1
        # Subsequent /active call should now show empty lists.
        r2 = client.get("/api/v1/admin/heavy-jobs/active")
        assert r2.json()["classification"] == []
        assert r2.json()["ingestion"] == []

    def test_admin_cancel_stale_rejects_invalid_minutes(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/admin/heavy-jobs/cancel-stale?stale_after_minutes=0"
        )
        assert r.status_code == 400
        assert (
            r.json()["detail"]["error_code"] == "invalid_stale_minutes"
        )

    def test_admin_endpoints_reject_non_altera_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Build a fresh store + client where the demo user is a
        # gms_client (non-Altera). The admin endpoints must 403.
        s = InMemoryStore()
        org_id = s.default_org_id
        user_id = s.default_user_id
        existing_org = s.organisations[org_id]
        s.organisations[org_id] = Organisation(
            id=org_id,
            name=existing_org.name,
            slug=existing_org.slug,
            organisation_type=OrganisationType.GMS_CLIENT,
            created_at=existing_org.created_at,
        )
        existing_user = s.users[user_id]
        s.upsert_user(
            UserProfile(
                user_id=user_id,
                organisation_id=org_id,
                email=existing_user.email,
                display_name=existing_user.display_name,
                role=ClientRole.CLIENT_VIEWER,
                created_at=existing_user.created_at,
            )
        )
        app.dependency_overrides[get_store] = lambda: s
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/admin/heavy-jobs/active")
                assert r.status_code == 403
                r2 = c.post("/api/v1/admin/heavy-jobs/cancel-stale")
                assert r2.status_code == 403
        finally:
            app.dependency_overrides.pop(get_store, None)

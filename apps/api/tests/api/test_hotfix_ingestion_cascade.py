"""Hotfix-Upload — chunk-failure cascade.

Before this hotfix, ``advance_ingestion_job`` kept ``pending_payload``
intact on chunk failure, so retries re-processed the same failing
chunk. A 100-row file with a missing required field generated ~7800
errors (78 advance retries × 100 rows).

Covered:
  A. On chunk failure ``pending_payload`` is trimmed past the failed
     chunk so a single permanent error counts each row once.
  B. When the very first chunk fails (no row has been inserted yet),
     the job is marked FAILED with ``error_code="chunk_validation_failed"``
     instead of staying RUNNING forever.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from altera_api.api.ingestion_job_orchestrator import advance_ingestion_job
from altera_api.api.state import InMemoryStore
from altera_api.domain.common import AlteraRole, Methodology, OrganisationType
from altera_api.domain.ingestion_job import (
    IngestionJob,
    IngestionJobStatus,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.project import Project


def _promote(store: InMemoryStore) -> tuple[UUID, UUID]:
    org_id = store.default_org_id
    user_id = store.default_user_id
    existing = store.organisations[org_id]
    store.organisations[org_id] = Organisation(
        id=org_id,
        name=existing.name,
        slug=existing.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing.created_at,
    )
    u = store.users[user_id]
    store.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=u.email,
            display_name=u.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=u.created_at,
        )
    )
    return org_id, user_id


@pytest.fixture
def patched_product_from_row(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Stub ``product_from_row`` to always raise so we can prove the
    advance orchestrator doesn't cascade errors when every row fails.
    """
    def _raise(*_args, **_kwargs):
        raise ValueError("simulated missing required field: items_purchased")

    monkeypatch.setattr(
        "altera_api.api.ingestion_job_orchestrator.product_from_row",
        _raise,
    )
    yield


def _make_job(
    store: InMemoryStore,
    org_id: UUID,
    user_id: UUID,
    *,
    chunk_size: int,
    row_count: int,
) -> tuple[IngestionJob, Project]:
    project = store.create_project(
        name="hotfix-upload",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="FY 2024",
        organisation_id=org_id,
        created_by=user_id,
    )
    upload_id = uuid4()
    now = datetime.now(UTC)
    payload = tuple({"row_number": i, "product_name": f"P{i}"} for i in range(row_count))
    job = IngestionJob(
        id=uuid4(),
        organisation_id=org_id,
        project_id=project.id,
        upload_id=upload_id,
        status=IngestionJobStatus.QUEUED,
        total_rows=row_count,
        processed_rows=0,
        inserted_products=0,
        errors_total=0,
        warnings_total=0,
        sample_errors=(),
        pending_payload=payload,
        mapping={},
        chunk_size=chunk_size,
        next_row_offset=0,
        error_code=None,
        error_message=None,
        created_by=user_id,
        created_at=now,
        started_at=None,
        updated_at=now,
        completed_at=None,
    )
    store.add_ingestion_job(job)
    return job, project


class TestChunkFailureCascade:
    def test_first_chunk_failure_marks_job_failed(
        self, patched_product_from_row: None
    ) -> None:
        store = InMemoryStore()
        org_id, user_id = _promote(store)
        # 100 rows in a SINGLE chunk → the first (and only) chunk
        # fails entirely, so the job must be marked FAILED.
        job, project = _make_job(
            store, org_id, user_id, chunk_size=100, row_count=100
        )
        result = advance_ingestion_job(store, job.id, project=project)
        assert result.status is IngestionJobStatus.FAILED
        assert result.errors_total == 100
        # error_code/message must be populated so the wizard surfaces
        # a clear message instead of polling indefinitely.
        assert result.error_code == "chunk_validation_failed"
        assert result.error_message is not None

    def test_multiple_failing_chunks_do_not_cascade(
        self, patched_product_from_row: None
    ) -> None:
        store = InMemoryStore()
        org_id, user_id = _promote(store)
        # 100 rows in 4 chunks of 25 — every advance should fail and
        # trim. After 4 advances errors_total == 100 (once per row),
        # NOT 100 × 4 = 400.
        job, project = _make_job(
            store, org_id, user_id, chunk_size=25, row_count=100
        )
        for _ in range(5):
            job = advance_ingestion_job(store, job.id, project=project)
            if job.is_terminal:
                break
        assert job.is_terminal
        # Critical assertion: errors_total == row count, not a multiple
        # caused by retries.
        assert job.errors_total == 100

    def test_partial_chunk_failure_still_advances(
        self, patched_product_from_row: None
    ) -> None:
        # 50 rows / chunk_size=25 → first chunk fails. Before this
        # hotfix the second advance would re-fail the same chunk
        # forever. Now it trims and processes the second (which also
        # fails per our stub) until terminal.
        store = InMemoryStore()
        org_id, user_id = _promote(store)
        job, project = _make_job(
            store, org_id, user_id, chunk_size=25, row_count=50
        )
        first = advance_ingestion_job(store, job.id, project=project)
        assert first.processed_rows == 25
        assert len(first.pending_payload) == 25  # trimmed past failed chunk
        # First chunk's failure is NOT terminal (50 rows remain) so
        # status stays RUNNING and the wizard can advance again.
        assert first.status is IngestionJobStatus.RUNNING
        # Drain remaining advances until terminal.
        result = first
        for _ in range(4):
            result = advance_ingestion_job(store, result.id, project=project)
            if result.is_terminal:
                break
        assert result.is_terminal
        assert result.errors_total == 50

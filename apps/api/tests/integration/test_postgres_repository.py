"""Integration tests for PostgresRepository.

Requires a live Supabase project.  Skipped automatically when
``SUPABASE_URL`` is not set in the environment.

Run with:

    SUPABASE_URL=https://... SUPABASE_SERVICE_ROLE_KEY=... \\
        uv run pytest tests/integration -m integration -v

All tests use a dedicated org/project created during the session and
cleaned up in the fixture teardown.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_repo():
    """Return a live PostgresRepository or skip if env vars are absent."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        pytest.skip("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")

    from supabase import create_client

    from altera_api.persistence.postgres import PostgresRepository

    return PostgresRepository(create_client(url, key))


@pytest.fixture
def project(pg_repo):
    """Create a test project and tear it down after the test."""
    from altera_api.domain.common import Methodology

    proj = pg_repo.create_project(
        name=f"int-test-{uuid4().hex[:8]}",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024-Q1",
    )
    yield proj
    # Teardown: cascading deletes handle uploads / products / runs.
    pg_repo._c.table("projects").delete().eq("id", str(proj.id)).execute()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_and_get_project(pg_repo, project):
    fetched = pg_repo.get_project(project.id)
    assert fetched is not None
    assert fetched.name == project.name


@pytest.mark.integration
def test_list_projects_contains_project(pg_repo, project):
    ids = {p.id for p in pg_repo.list_projects()}
    assert project.id in ids


# ---------------------------------------------------------------------------
# Uploads + products
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_and_get_upload(pg_repo, project):
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields
    from altera_api.domain.upload import Upload, UploadStatus

    upload_id = uuid4()
    product_id = uuid4()
    now = datetime.now(UTC)

    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="test.csv",
        status=UploadStatus.VALID,
        row_count=1,
        dropped_columns=(),
        uploaded_by=uuid4(),
        created_at=now,
    )
    product = NormalizedProduct(
        id=product_id,
        upload_id=upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        row_number=1,
        external_product_id="EXT-001",
        product_name="Test Chicken Breast",
        weight_per_item_kg=Decimal("0.5"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=Decimal("25"),
        ),
        created_at=now,
    )
    pg_repo.add_product(product)
    pg_repo.add_upload(upload, product_ids=[product_id])

    record = pg_repo.get_upload(upload_id)
    assert record is not None
    assert record.upload.original_filename == "test.csv"
    assert product_id in record.product_ids


# ---------------------------------------------------------------------------
# Classifications
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upsert_and_get_pt_classification(pg_repo, project):
    from decimal import Decimal

    from altera_api.domain.common import ClassificationSource, Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields
    from altera_api.domain.protein_tracker import (
        ProteinTrackerGroup,
        ProteinTrackerProductClassification,
    )
    from altera_api.domain.upload import Upload, UploadStatus

    upload_id = uuid4()
    product_id = uuid4()
    now = datetime.now(UTC)

    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="cls.csv",
        status=UploadStatus.VALID,
        row_count=1,
        dropped_columns=(),
        uploaded_by=uuid4(),
        created_at=now,
    )
    product = NormalizedProduct(
        id=product_id,
        upload_id=upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        row_number=1,
        external_product_id="CLS-001",
        product_name="Lentils",
        weight_per_item_kg=Decimal("0.4"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("50"),
            protein_pct=Decimal("9"),
        ),
        created_at=now,
    )
    pg_repo.add_product(product)
    pg_repo.add_upload(upload, product_ids=[product_id])

    classification = ProteinTrackerProductClassification(
        product_id=product_id,
        pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="pt.legumes",
        updated_at=now,
    )
    pg_repo.upsert_pt_classification(classification)

    fetched = pg_repo.get_pt_classification(product_id)
    assert fetched is not None
    assert fetched.pt_group == ProteinTrackerGroup.PLANT_BASED_CORE


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_and_get_run(pg_repo, project):
    from altera_api.api.state import RunRecord
    from altera_api.domain.common import Methodology

    now = datetime.now(UTC)
    run_id = uuid4()
    record = RunRecord(
        id=run_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        methodology=Methodology.PROTEIN_TRACKER,
        started_at=now,
        finished_at=now,
        triggered_by=uuid4(),
        rows_payload=[],
        summary_payload={
            "run_id": str(run_id),
            "methodology_version": "1.0.0",
            "methodology_source_edition": "test",
            "taxonomy_version": "1.0.0",
            "rules_version": "0.1.0",
            "reporting_period_label": "2024-Q1",
        },
        rows_count=0,
    )
    pg_repo.add_run(record)

    fetched = pg_repo.get_run(run_id)
    assert fetched is not None
    assert fetched.methodology == Methodology.PROTEIN_TRACKER

    runs = pg_repo.list_runs_for_project(project.id)
    assert any(r.id == run_id for r in runs)

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
def test_update_upload(pg_repo, project):
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields
    from altera_api.domain.upload import Upload, UploadStatus

    upload_id = uuid4()
    now = datetime.now(UTC)
    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="upd.csv",
        status=UploadStatus.VALIDATION_PENDING,
        row_count=None,
        dropped_columns=(),
        uploaded_by=uuid4(),
        created_at=now,
    )
    product = NormalizedProduct(
        id=uuid4(),
        upload_id=upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        row_number=1,
        external_product_id="U-001",
        product_name="Test product",
        weight_per_item_kg=Decimal("0.5"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("10"),
            protein_pct=Decimal("20"),
        ),
        created_at=now,
    )
    pg_repo.add_product(product)
    pg_repo.add_upload(upload, product_ids=[product.id])

    updated = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="upd.csv",
        status=UploadStatus.VALID,
        row_count=10,
        dropped_columns=(),
        uploaded_by=upload.uploaded_by,
        created_at=now,
        checksum_sha256="a" * 64,
        file_size_bytes=1024,
    )
    pg_repo.update_upload(updated)
    fetched = pg_repo.get_upload(upload_id)
    assert fetched is not None
    assert fetched.upload.status == UploadStatus.VALID
    assert fetched.upload.checksum_sha256 == "a" * 64


@pytest.mark.integration
def test_set_and_get_validation_report(pg_repo, project):
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields
    from altera_api.domain.upload import Upload, UploadStatus
    from altera_api.domain.validation import ValidationReport

    upload_id = uuid4()
    now = datetime.now(UTC)
    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="vr.csv",
        status=UploadStatus.VALID,
        row_count=5,
        dropped_columns=(),
        uploaded_by=uuid4(),
        created_at=now,
    )
    product = NormalizedProduct(
        id=uuid4(),
        upload_id=upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        row_number=1,
        external_product_id="VR-001",
        product_name="Validation test",
        weight_per_item_kg=Decimal("0.3"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("5"),
            protein_pct=Decimal("15"),
        ),
        created_at=now,
    )
    pg_repo.add_product(product)
    pg_repo.add_upload(upload, product_ids=[product.id])

    report = ValidationReport(upload_id=upload_id, total_rows=5)
    pg_repo.set_upload_validation_report(upload_id, report)
    fetched = pg_repo.get_upload_validation_report(upload_id)
    assert fetched is not None
    assert fetched.total_rows == 5


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


# ---------------------------------------------------------------------------
# Review decisions (Phase 19C)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_review_decision(pg_repo, project):
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields
    from altera_api.domain.review import ManualReviewDecision, ManualReviewDecisionType
    from altera_api.domain.upload import Upload, UploadStatus

    now = datetime.now(UTC)
    upload_id = uuid4()
    product_id = uuid4()
    reviewer_id = uuid4()

    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=f"test/{upload_id}",
        original_filename="rd.csv",
        status=UploadStatus.VALID,
        row_count=1,
        dropped_columns=(),
        uploaded_by=reviewer_id,
        created_at=now,
    )
    product = NormalizedProduct(
        id=product_id,
        upload_id=upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        row_number=1,
        external_product_id="RD-001",
        product_name="Lentils",
        weight_per_item_kg=Decimal("0.4"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("10"),
            protein_pct=Decimal("9"),
        ),
        created_at=now,
    )
    pg_repo.add_product(product)
    pg_repo.add_upload(upload, product_ids=[product_id])

    decision = ManualReviewDecision(
        id=uuid4(),
        product_id=product_id,
        methodology=Methodology.PROTEIN_TRACKER,
        decision=ManualReviewDecisionType.ACCEPTED,
        reviewer_user_id=reviewer_id,
        from_category="animal_core",
        to_category="plant_based_core",
        reason="clearly plant-based",
        created_at=now,
    )
    pg_repo.add_review_decision(decision)
    # add_review_decision is fire-and-forget; no fetch API yet — just verify no exception


# ---------------------------------------------------------------------------
# Recommendations (Phase 25B)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_upsert_and_list_recommendations(pg_repo, project):
    from altera_api.api.state import PersistedRecommendation

    now = datetime.now(UTC)
    run_id = uuid4()

    rec = PersistedRecommendation(
        id=uuid4(),
        organisation_id=project.organisation_id,
        project_id=project.id,
        run_id=run_id,
        methodology="protein_tracker",
        action_type="increase_plant_core_share",
        category="plant_protein",
        title="Increase plant share",
        description="Plant share is below target.",
        rationale="Evidence-based recommendation.",
        expected_direction="improving",
        priority="high",
        confidence="medium",
        evidence=["plant share < 40%"],
        caveats=[],
        status="draft",
        client_facing=True,
        created_at=now,
        updated_at=now,
    )
    pg_repo.upsert_recommendations_for_run([rec])

    recs = pg_repo.list_recommendations_for_run(run_id)
    assert len(recs) == 1
    assert recs[0].action_type == "increase_plant_core_share"

    proj_recs = pg_repo.list_recommendations_for_project(project.id)
    assert any(r.action_type == "increase_plant_core_share" for r in proj_recs)


@pytest.mark.integration
def test_get_and_update_recommendation_status(pg_repo, project):
    from altera_api.api.state import PersistedRecommendation

    now = datetime.now(UTC)
    rec_id = uuid4()
    run_id = uuid4()
    updater_id = uuid4()

    rec = PersistedRecommendation(
        id=rec_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        run_id=run_id,
        methodology="protein_tracker",
        action_type="reduce_animal_core_dependency",
        category="animal_protein",
        title="Reduce animal dependency",
        description="Animal protein share is high.",
        rationale="High animal share detected.",
        expected_direction="improving",
        priority="medium",
        confidence="high",
        evidence=[],
        caveats=[],
        status="draft",
        client_facing=False,
        created_at=now,
        updated_at=now,
    )
    pg_repo.upsert_recommendations_for_run([rec])

    fetched = pg_repo.get_recommendation(rec_id)
    assert fetched is not None
    assert fetched.status == "draft"

    updated = pg_repo.update_recommendation_status(
        rec_id, status="proposed", by_user_id=updater_id
    )
    assert updated is not None
    assert updated.status == "proposed"

    refetched = pg_repo.get_recommendation(rec_id)
    assert refetched is not None
    assert refetched.status == "proposed"


# ---------------------------------------------------------------------------
# Scenarios (Phase 26A)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_add_and_get_scenario(pg_repo, project):
    from altera_api.api.state import ScenarioRecord

    now = datetime.now(UTC)
    scenario_id = uuid4()
    run_id = uuid4()

    record = ScenarioRecord(
        id=scenario_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        base_run_id=run_id,
        name="Test scenario",
        description="Integration test",
        status="draft",
        methodology="protein_tracker",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    pg_repo.add_scenario(record)

    fetched = pg_repo.get_scenario(scenario_id)
    assert fetched is not None
    assert fetched.name == "Test scenario"

    scenarios = pg_repo.list_scenarios_for_project(project.id)
    assert any(s.id == scenario_id for s in scenarios)


@pytest.mark.integration
def test_update_scenario_status(pg_repo, project):
    from altera_api.api.state import ScenarioRecord

    now = datetime.now(UTC)
    scenario_id = uuid4()

    record = ScenarioRecord(
        id=scenario_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        base_run_id=uuid4(),
        name="Status test",
        description="",
        status="draft",
        methodology="protein_tracker",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    pg_repo.add_scenario(record)
    updated = pg_repo.update_scenario_status(scenario_id, status="active")
    assert updated is not None
    assert updated.status == "active"


@pytest.mark.integration
def test_scenario_operations_and_result(pg_repo, project):
    from altera_api.api.state import ScenarioOperationRecord, ScenarioRecord, ScenarioResultRecord

    now = datetime.now(UTC)
    scenario_id = uuid4()
    run_id = uuid4()

    scenario = ScenarioRecord(
        id=scenario_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        base_run_id=run_id,
        name="Ops test",
        description="",
        status="draft",
        methodology="protein_tracker",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    pg_repo.add_scenario(scenario)

    op = ScenarioOperationRecord(
        id=uuid4(),
        scenario_id=scenario_id,
        operation_type="increase_plant_core_protein",
        parameters={"amount_kg": 10},
        rationale="test",
        order=1,
        created_at=now,
    )
    pg_repo.add_scenario_operation(op)

    ops = pg_repo.list_scenario_operations(scenario_id)
    assert len(ops) == 1
    assert ops[0].operation_type == "increase_plant_core_protein"

    result_record = ScenarioResultRecord(
        scenario_id=scenario_id,
        base_run_id=run_id,
        methodology="protein_tracker",
        result_payload={"pt_projected": {"base_plant_protein_kg": "50.0"}},
        created_at=now,
    )
    pg_repo.save_scenario_result(result_record)

    fetched_result = pg_repo.get_scenario_result(scenario_id)
    assert fetched_result is not None
    assert fetched_result.methodology == "protein_tracker"


# ---------------------------------------------------------------------------
# Meta: StoreProtocol compliance
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_repository_satisfies_store_protocol(pg_repo):
    """PostgresRepository must satisfy StoreProtocol at runtime."""
    from altera_api.persistence.protocol import StoreProtocol

    assert isinstance(pg_repo, StoreProtocol), (
        "PostgresRepository does not satisfy StoreProtocol — missing methods detected. "
        "Run: python -c \"from altera_api.persistence.postgres import PostgresRepository; "
        "from altera_api.persistence.protocol import StoreProtocol; "
        "import inspect; "
        "proto_methods = {n for n, _ in inspect.getmembers(StoreProtocol, predicate=inspect.isfunction)}; "
        "pg_methods = {n for n, _ in inspect.getmembers(PostgresRepository, predicate=inspect.isfunction)}; "
        "print(proto_methods - pg_methods)\""
    )

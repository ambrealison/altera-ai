"""Phase Product-UX-F — workflow progress reaches 100% when complete.

Root cause of the observed "Step 6 of 6 · 37%": for a WWF-only project
the PT-only steps (manual review, NEVO, nutrition validation) were
``locked`` (not ``not_needed``), the AI-classification step was gated on
PT counts (so it never completed for WWF-only), and the calculation step
never became ``complete`` once a run existed. All four dragged
``overall_progress_pct`` below 100.

These tests seed a fully-completed project (upload + classified products
+ a run) for each methodology mode and assert progress is 100, plus that
non-required PT steps are ``not_needed`` for WWF-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.api.workflow import compute_workflow_status
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.wwf import WWFFoodGroup, WWFProductClassification

_NOW = datetime.now(UTC)


def _uid(n: int) -> UUID:
    return UUID(int=n)


def _pt_product(store: InMemoryStore, project, n: int) -> UUID:
    pid = uuid4()
    store.add_product(
        NormalizedProduct(
            id=pid,
            upload_id=_uid(9001),
            project_id=project.id,
            organisation_id=project.organisation_id,
            row_number=n,
            external_product_id=f"P-{n}",
            product_name=f"PT product {n}",
            weight_per_item_kg=Decimal("0.5"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(
                items_purchased=Decimal("10"),
                # Retailer-provided protein → no missing nutrition, so the
                # NEVO + nutrition-validation steps resolve to complete-neutral.
                protein_pct=Decimal("12"),
                protein_source=ProteinSource.LABEL,
            ),
            created_at=_NOW,
        )
    )
    store.upsert_pt_classification(
        ProteinTrackerProductClassification(
            product_id=pid,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="ux-f-test",
            updated_at=_NOW,
        )
    )
    return pid


def _wwf_product(store: InMemoryStore, project, n: int) -> UUID:
    pid = uuid4()
    store.add_product(
        NormalizedProduct(
            id=pid,
            upload_id=_uid(9001),
            project_id=project.id,
            organisation_id=project.organisation_id,
            row_number=n,
            external_product_id=f"W-{n}",
            product_name=f"WWF product {n}",
            is_own_brand=False,
            weight_per_item_kg=Decimal("0.5"),
            methodologies_enabled=frozenset({Methodology.WWF}),
            wwf_fields=WWFProductFields(
                items_sold=Decimal("10"),
                retail_channel=RetailChannel.FRESH,
                is_own_brand=False,
            ),
            created_at=_NOW,
        )
    )
    store.upsert_wwf_classification(
        WWFProductClassification(
            product_id=pid,
            wwf_food_group=WWFFoodGroup.FG4,
            wwf_is_composite=False,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="ux-f-test",
            updated_at=_NOW,
        )
    )
    return pid


def _seed_upload(store: InMemoryStore, project, product_ids: list[UUID]) -> None:
    store.add_upload(
        Upload(
            id=_uid(9001),
            organisation_id=project.organisation_id,
            project_id=project.id,
            storage_path="orgs/x/uploads/u.csv",
            original_filename="u.csv",
            status=UploadStatus.INGESTION_COMPLETED,
            row_count=len(product_ids),
            uploaded_by=_uid(7),
            created_at=_NOW,
        ),
        product_ids,
    )


def _seed_run(store: InMemoryStore, project, methodology: Methodology) -> None:
    store.add_run(
        RunRecord(
            id=uuid4(),
            project_id=project.id,
            methodology=methodology,
            started_at=_NOW,
            finished_at=_NOW,
            triggered_by=_uid(7),
            rows_payload=[],
            summary_payload={},
            rows_count=1,
            organisation_id=project.organisation_id,
        )
    )


def _project(store: InMemoryStore, methodologies: set[Methodology]):
    return store.create_project(
        name="P",
        methodologies_enabled=frozenset(methodologies),
        reporting_period_label="2024",
    )


def test_wwf_only_completed_project_is_100() -> None:
    store = InMemoryStore()
    project = _project(store, {Methodology.WWF})
    pid = _wwf_product(store, project, 1)
    _seed_upload(store, project, [pid])
    _seed_run(store, project, Methodology.WWF)

    status = compute_workflow_status(store, project)
    assert status.overall_progress_pct == 100, [
        (s.key, s.status) for s in status.steps
    ]
    by_key = {s.key: s.status for s in status.steps}
    # PT-only steps are complete-neutral, not locked, for WWF-only.
    assert by_key["nutrition_enrichment_nevo"] == "not_needed"
    assert by_key["nutrition_validation"] == "not_needed"
    assert by_key["manual_classification_review"] == "not_needed"
    assert by_key["calculation"] == "complete"
    assert by_key["report"] == "complete"


def test_pt_only_completed_project_is_100() -> None:
    store = InMemoryStore()
    project = _project(store, {Methodology.PROTEIN_TRACKER})
    pid = _pt_product(store, project, 1)
    _seed_upload(store, project, [pid])
    _seed_run(store, project, Methodology.PROTEIN_TRACKER)

    status = compute_workflow_status(store, project)
    assert status.overall_progress_pct == 100, [
        (s.key, s.status) for s in status.steps
    ]
    by_key = {s.key: s.status for s in status.steps}
    assert by_key["ai_classification"] == "complete"
    assert by_key["calculation"] == "complete"
    assert by_key["report"] == "complete"


def test_pt_wwf_completed_project_is_100() -> None:
    store = InMemoryStore()
    project = _project(store, {Methodology.PROTEIN_TRACKER, Methodology.WWF})
    pt_pid = _pt_product(store, project, 1)
    wwf_pid = _wwf_product(store, project, 2)
    _seed_upload(store, project, [pt_pid, wwf_pid])
    _seed_run(store, project, Methodology.PROTEIN_TRACKER)

    status = compute_workflow_status(store, project)
    assert status.overall_progress_pct == 100, [
        (s.key, s.status) for s in status.steps
    ]


def test_wwf_only_in_progress_not_dragged_by_pt_steps() -> None:
    """A WWF-only project mid-flow (no run yet) is not pulled down by the
    non-required PT steps — they are complete-neutral."""
    store = InMemoryStore()
    project = _project(store, {Methodology.WWF})
    pid = _wwf_product(store, project, 1)
    _seed_upload(store, project, [pid])
    # No run yet → calculation + report not complete, but PT steps must
    # already be not_needed (not locked).
    status = compute_workflow_status(store, project)
    by_key = {s.key: s.status for s in status.steps}
    assert by_key["nutrition_enrichment_nevo"] == "not_needed"
    assert by_key["nutrition_validation"] == "not_needed"
    assert by_key["ai_classification"] == "complete"  # the WWF product is classified
    # Progress is below 100 (no run) but well above the old 37%.
    assert status.overall_progress_pct < 100
    assert status.overall_progress_pct >= 75

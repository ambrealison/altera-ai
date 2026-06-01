"""Phase Product-UX-A — WWF-only calculation preflight + workflow.

Bug: a WWF-only project's calculation step required Protein Tracker
categories ("Aucun produit Protein Tracker importé", "0 sur 0 prêts")
because both the workflow calc-step builder and the preflight route
were hardcoded to PT.

Covered:
  A. WWF-only workflow calc step emits NO Protein Tracker blocker and
     becomes ``ready`` once WWF products are classified.
  B. WWF-only preflight reports WWF readiness with no nutrition
     requirement (requires_nutrition=False, methodology="wwf").
  C. PT-only project is unchanged (still PT blockers + nutrition).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from altera_api.api.routes import _wwf_calculation_preflight
from altera_api.api.state import InMemoryStore
from altera_api.api.workflow import compute_workflow_status
from altera_api.domain.common import (
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)


def _wwf_product(project_id: UUID, org_id: UUID, upload_id: UUID, name: str):
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=f"ext-{name[:8]}-{uuid4().hex[:6]}",
        product_name=name,
        brand=None,
        is_own_brand=False,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.WWF}),
        pt_fields=None,
        wwf_fields=WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.GROCERY_AMBIENT,
            is_own_brand=False,
        ),
        created_at=datetime.now(UTC),
    )


def _pt_product(project_id: UUID, org_id: UUID, upload_id: UUID, name: str):
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=f"ext-{name[:8]}-{uuid4().hex[:6]}",
        product_name=name,
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("100")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


def _seed_project(store: InMemoryStore, methodology: Methodology):
    project = store.create_project(
        name="phase-product-ux-a",
        methodologies_enabled=frozenset({methodology}),
        reporting_period_label="FY 2024",
    )
    return project


def _register_upload(store: InMemoryStore, project, product_ids: list[UUID]):
    upload = Upload(
        id=uuid4(),
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path="x.csv",
        original_filename="x.csv",
        status=UploadStatus.READY_FOR_CLASSIFICATION,
        row_count=len(product_ids),
        uploaded_by=store.default_user_id,
        created_at=datetime.now(UTC),
    )
    store.add_upload(upload, product_ids)


class TestWwfOnlyCalculation:
    def test_wwf_only_calc_step_has_no_pt_blocker_and_is_ready(self) -> None:
        store = InMemoryStore()
        project = _seed_project(store, Methodology.WWF)
        names = ["Lentilles vertes", "Carottes", "Tomates"]
        products = [
            _wwf_product(project.id, project.organisation_id, uuid4(), n)
            for n in names
        ]
        for p in products:
            store.add_product(p)
        _register_upload(store, project, [p.id for p in products])
        # Classify all to a real WWF food group.
        for p in products:
            store.upsert_wwf_classification(
                WWFProductClassification(
                    product_id=p.id,
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_is_composite=False,
                    fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                    source=ClassificationSource.AI,
                    confidence=Decimal("0.9"),
                    ai_prompt_version="t",
                    ai_model="t",
                    updated_at=datetime.now(UTC),
                )
            )
        status = compute_workflow_status(store, project)
        calc = next(s for s in status.steps if s.key == "calculation")
        # No Protein Tracker wording anywhere in the blockers.
        joined = " ".join(
            f"{b.code} {b.label}" for b in calc.blocking_reasons
        ).lower()
        assert "protein tracker" not in joined
        assert "protein_tracker" not in joined
        assert calc.status == "ready"
        assert calc.blocking_reasons == []
        assert calc.counts.get("eligible_rows", 0) == 3

    def test_wwf_only_unclassified_blocks_with_wwf_label(self) -> None:
        store = InMemoryStore()
        project = _seed_project(store, Methodology.WWF)
        products = [
            _wwf_product(project.id, project.organisation_id, uuid4(), "P")
        ]
        for p in products:
            store.add_product(p)
        _register_upload(store, project, [p.id for p in products])
        # No classification → blocked, but with WWF wording only.
        status = compute_workflow_status(store, project)
        calc = next(s for s in status.steps if s.key == "calculation")
        assert calc.status == "blocked"
        codes = {b.code for b in calc.blocking_reasons}
        assert "classification_required_wwf" in codes
        assert "classification_required" not in codes  # no PT blocker

    def test_wwf_preflight_no_nutrition_requirement(self) -> None:
        store = InMemoryStore()
        project = _seed_project(store, Methodology.WWF)
        products = [
            _wwf_product(project.id, project.organisation_id, uuid4(), n)
            for n in ("Lentilles", "Carottes")
        ]
        for p in products:
            store.add_product(p)
        _register_upload(store, project, [p.id for p in products])
        for p in products:
            store.upsert_wwf_classification(
                WWFProductClassification(
                    product_id=p.id,
                    wwf_food_group=WWFFoodGroup.FG4,
                    wwf_is_composite=False,
                    source=ClassificationSource.AI,
                    confidence=Decimal("0.9"),
                    ai_prompt_version="t",
                    ai_model="t",
                    updated_at=datetime.now(UTC),
                )
            )
        pre = _wwf_calculation_preflight(store, project)
        assert pre.methodology == "wwf"
        assert pre.requires_nutrition is False
        assert pre.total_products == 2
        assert pre.products_ready_for_calculation == 2
        assert pre.products_missing_nutrition == 0
        assert pre.products_with_total_protein == 0


class TestPtOnlyUnchanged:
    def test_pt_only_still_requires_classification(self) -> None:
        store = InMemoryStore()
        project = _seed_project(store, Methodology.PROTEIN_TRACKER)
        products = [
            _pt_product(project.id, project.organisation_id, uuid4(), "P")
        ]
        for p in products:
            store.add_product(p)
        _register_upload(store, project, [p.id for p in products])
        # No PT classification → PT blocker present.
        status = compute_workflow_status(store, project)
        calc = next(s for s in status.steps if s.key == "calculation")
        codes = {b.code for b in calc.blocking_reasons}
        assert "classification_required" in codes
        # No WWF blocker on a PT-only project.
        assert "classification_required_wwf" not in codes
        assert "no_eligible_products_wwf" not in codes

    def test_pt_only_classified_then_eligible_or_nutrition_blocked(
        self,
    ) -> None:
        store = InMemoryStore()
        project = _seed_project(store, Methodology.PROTEIN_TRACKER)
        p = _pt_product(project.id, project.organisation_id, uuid4(), "Tofu")
        store.add_product(p)
        _register_upload(store, project, [p.id])
        store.upsert_pt_classification(
            ProteinTrackerProductClassification(
                product_id=p.id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.AI,
                confidence=Decimal("0.9"),
                ai_prompt_version="t",
                ai_model="t",
                updated_at=datetime.now(UTC),
            )
        )
        status = compute_workflow_status(store, project)
        calc = next(s for s in status.steps if s.key == "calculation")
        # Classified but no nutrition → PT nutrition blocker (unchanged).
        codes = {b.code for b in calc.blocking_reasons}
        assert "classification_required" not in codes
        assert "nutrition_required" in codes

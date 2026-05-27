"""Hotfix-Validation — three bugs surfaced after Phase UX-Validation-S.

Covered:
  A. ``submit_decision`` with ``decision="changed"`` and the SAME
     target as the current classification no longer raises
     ``IllegalTransitionError``; it acts as an accept and clears the
     review item.
  B. Nutrition read path prefers the latest MANUAL_ALTERA enrichment
     record over an earlier NEVO record for the same nutrient, so
     manual overrides actually appear after reload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from altera_api.api.orchestrator import submit_decision
from altera_api.api.routes import _nutrition_row_fields
from altera_api.api.state import InMemoryStore
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    Methodology,
    OrganisationType,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)


def _seed_org_user(store: InMemoryStore):
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


# ---------------------------------------------------------------------------
# A. submit_decision same-category acts as accept
# ---------------------------------------------------------------------------


class TestSameCategoryActsAsAccept:
    def test_pt_change_same_category_clears_review_via_accept(self) -> None:
        store = InMemoryStore()
        org_id, user_id = _seed_org_user(store)
        product = _make_pt_product_no_pct(uuid4())
        product_id = product.id
        store.add_product(product)
        now = datetime.now(UTC)
        # Seed an existing PT classification + a review item.
        store.upsert_pt_classification(
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.AI,
                confidence=Decimal("0.8"),
                ai_prompt_version="test",
                ai_model="test",
                updated_at=now,
            )
        )
        store.upsert_review_item(
            ManualReviewItem(
                product_id=product_id,
                methodology=Methodology.PROTEIN_TRACKER,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.LOW_CONFIDENCE,
                queued_at=now,
            )
        )
        # Submit decision=changed with the SAME pt_group as current.
        # Before this hotfix this raised IllegalTransitionError.
        result = submit_decision(
            store,
            product_id=product_id,
            methodology=Methodology.PROTEIN_TRACKER,
            decision="changed",
            reviewer_user_id=user_id,
            to_category="plant_based_core",
        )
        # Item is now accepted; review queue is cleared.
        # ``submit_decision`` returns a ReviewItemView; once the
        # outcome is ACCEPTED/CHANGED the review item is removed from
        # the queue (see orchestrator) so the freshest store lookup
        # returns None.
        assert store.get_review_item(
            product_id, Methodology.PROTEIN_TRACKER
        ) is None
        assert result.status is ManualReviewStatus.ACCEPTED
        cls = store.get_pt_classification(product_id)
        assert cls is not None
        assert cls.source is ClassificationSource.MANUAL_REVIEW
        assert cls.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_pt_change_different_category_still_persists(self) -> None:
        store = InMemoryStore()
        org_id, user_id = _seed_org_user(store)
        product = _make_pt_product_no_pct(uuid4())
        product_id = product.id
        store.add_product(product)
        now = datetime.now(UTC)
        store.upsert_pt_classification(
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.AI,
                confidence=Decimal("0.8"),
                ai_prompt_version="test",
                ai_model="test",
                updated_at=now,
            )
        )
        store.upsert_review_item(
            ManualReviewItem(
                product_id=product_id,
                methodology=Methodology.PROTEIN_TRACKER,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.LOW_CONFIDENCE,
                queued_at=now,
            )
        )
        submit_decision(
            store,
            product_id=product_id,
            methodology=Methodology.PROTEIN_TRACKER,
            decision="changed",
            reviewer_user_id=user_id,
            to_category="composite_products",
        )
        # Genuine change → classification updated + review queue cleared.
        cls = store.get_pt_classification(product_id)
        assert cls is not None
        assert cls.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        assert cls.source is ClassificationSource.MANUAL_REVIEW
        assert (
            store.get_review_item(
                product_id, Methodology.PROTEIN_TRACKER
            )
            is None
        )


# ---------------------------------------------------------------------------
# B. Nutrition row picks manual override over NEVO
# ---------------------------------------------------------------------------


def _make_pt_product_no_pct(product_id) -> NormalizedProduct:
    return NormalizedProduct(
        id=product_id,
        project_id=uuid4(),
        upload_id=uuid4(),
        organisation_id=uuid4(),
        row_number=1,
        external_product_id="ext-x",
        product_name="Tofu Nature",
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("100")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


class TestNutritionManualOverridePriority:
    def test_manual_record_wins_over_earlier_nevo(self) -> None:
        product_id = uuid4()
        product = _make_pt_product_no_pct(product_id)
        cls = ProteinTrackerProductClassification(
            product_id=product_id,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            source=ClassificationSource.AI,
            confidence=Decimal("0.9"),
            ai_prompt_version="test",
            ai_model="test",
            updated_at=datetime.now(UTC),
        )
        # NEVO record stored FIRST (older timestamp).
        t0 = datetime.now(UTC)
        nevo = NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("12.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.NEVO,
            confidence=Decimal("0.9"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="nevo match",
            created_at=t0,
            created_by=uuid4(),
            match_method="ciqual_code",
        )
        manual = NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("18.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("1"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="manual override",
            created_at=t0 + timedelta(minutes=5),
            created_by=uuid4(),
            match_method="manual",
        )
        # The records list mirrors what the store returns (insertion
        # order — NEVO first, manual second).
        row = _nutrition_row_fields(product, cls, [nevo, manual])
        # Manual override wins.
        assert row["protein_pct"] == "18.0"
        assert row["source"] == "manual"

    def test_nevo_still_picked_when_no_manual(self) -> None:
        product_id = uuid4()
        product = _make_pt_product_no_pct(product_id)
        cls = ProteinTrackerProductClassification(
            product_id=product_id,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            source=ClassificationSource.AI,
            confidence=Decimal("0.9"),
            ai_prompt_version="test",
            ai_model="test",
            updated_at=datetime.now(UTC),
        )
        nevo = NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("12.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.NEVO,
            confidence=Decimal("0.9"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="nevo match",
            created_at=datetime.now(UTC),
            created_by=uuid4(),
            match_method="ciqual_code",
        )
        row = _nutrition_row_fields(product, cls, [nevo])
        assert row["protein_pct"] == "12.0"
        assert row["source"] == "nevo"

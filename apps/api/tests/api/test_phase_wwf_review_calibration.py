"""Phase WWF-Review-Calibration — end-to-end review-queue regression.

The demo bug
============

A project with both Protein Tracker and WWF enabled showed:

    PT  : 50/50 catégorisé ·  6 en revue
    WWF : 50/50 catégorisé · 50 en revue   <-- every WWF row in review

Every WWF row landed in manual review because the WWF auto-accept
threshold (Phase WWF-K = 0.80) sat *above* the confidence band the model
returns for clear WWF classifications (0.70-0.79). The operator saw WWF as
"broken" even though the classifications were fine.

After re-calibrating WWF to 0.70 (matching PT — see
``batch_classifier.WWF_REVIEW_THRESHOLD``) this file pins, end-to-end
through the real orchestrator + store, that:

  A. Clear WWF classifications (>= 0.70) auto-accept and leave **no**
     review item — the WWF review queue is empty (the demo fix).
  B. Genuinely low-confidence WWF rows (< 0.70) still create review items
     — we did not blanket-accept everything.
  C. An accepted re-classification removes the stale review item, so the
     aggregated ``needs_review`` reflects only real open items.
  D. WWF acceptance never touches the Protein Tracker review queue (the
     two methodologies stay strictly separate).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    advance_classification_job,
    create_classification_job,
)
from altera_api.api.orchestrator import _enqueue_review_item
from altera_api.api.state import InMemoryStore
from altera_api.domain.common import (
    AlteraRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.review import ManualReviewQueueReason
from altera_api.domain.upload import Upload, UploadStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wwf_product(
    name: str, *, project_id: UUID, organisation_id: UUID, upload_id: UUID
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=organisation_id,
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


def _make_pt_product(
    name: str, *, project_id: UUID, organisation_id: UUID, upload_id: UUID
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=organisation_id,
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


def _promote_org(store: InMemoryStore) -> tuple[UUID, UUID]:
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
    return org_id, user_id


def _seed_wwf_upload(
    store: InMemoryStore,
    *,
    org_id: UUID,
    user_id: UUID,
    project_id: UUID,
    names: list[str],
) -> tuple[UUID, list[UUID]]:
    """Add a WWF upload + products to an existing project."""
    upload_id = uuid4()
    products = [
        _make_wwf_product(
            n, project_id=project_id, organisation_id=org_id, upload_id=upload_id
        )
        for n in names
    ]
    for p in products:
        store.add_product(p)
    product_ids = [p.id for p in products]
    store.add_upload(
        Upload(
            id=upload_id,
            organisation_id=org_id,
            project_id=project_id,
            storage_path=f"wwf-calib/{upload_id}.csv",
            original_filename="wwf-calib.csv",
            status=UploadStatus.READY_FOR_CLASSIFICATION,
            row_count=len(products),
            uploaded_by=user_id,
            created_at=datetime.now(UTC),
        ),
        product_ids,
    )
    return upload_id, product_ids


class _FixedWWFProvider(ClassifierProvider):
    """Returns a valid FG1/legumes WWF verdict at a fixed confidence for
    every product — lets us drive the auto-accept-vs-review boundary
    deterministically through the real orchestrator."""

    def __init__(self, confidence: float) -> None:
        self._confidence = confidence

    @property
    def model(self) -> str:
        return "wwf-calib-fixed"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        rows: list[dict[str, Any]] = []
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
                    "wwf_food_group": "FG1",
                    "wwf_is_composite": False,
                    "wwf_fg1_subgroup": "legumes",
                    "confidence": self._confidence,
                    "rationale": "wwf-calib fixed",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}), model="wwf-calib-fixed"
        )


def _classify_wwf_to_terminal(
    store: InMemoryStore,
    *,
    org_id: UUID,
    project_id: UUID,
    upload_id: UUID,
    user_id: UUID,
    provider: ClassifierProvider,
    overwrite: bool = False,
) -> None:
    job = create_classification_job(
        store,
        organisation_id=org_id,
        project_id=project_id,
        upload_id=upload_id,
        methodology=Methodology.WWF,
        overwrite=overwrite,
        only_missing_or_failed=not overwrite,
        created_by=user_id,
    )
    for _ in range(20):
        job = advance_classification_job(store, job.id, ai_provider=provider)
        status = job.status.value
        if status.startswith("completed") or status == "failed":
            return
    raise AssertionError("classification job did not reach a terminal state")


def _wwf_review_count(store: InMemoryStore, project_id: UUID) -> int:
    return len(
        store.list_review_items_for_project(
            project_id, methodology=Methodology.WWF
        )
    )


def _pt_review_count(store: InMemoryStore, project_id: UUID) -> int:
    return len(
        store.list_review_items_for_project(
            project_id, methodology=Methodology.PROTEIN_TRACKER
        )
    )


# Neutral names carry no food tokens, so no deterministic WWF guard fires —
# the verdict keeps the provider's confidence and the threshold decides.
_NEUTRAL_NAMES = [f"Mystery Item {i}" for i in range(1, 9)]


# ---------------------------------------------------------------------------
# A. The headline demo fix — clear WWF rows accept with NO review item
# ---------------------------------------------------------------------------


class TestWWFClearRowsLeaveNoReviewItem:
    def test_clear_confidence_classifies_all_with_empty_review_queue(
        self,
    ) -> None:
        """The exact demo regression: 8 clear WWF rows at 0.78 must classify
        AND leave the WWF review queue EMPTY (was 8/8 in review at 0.80)."""
        store = InMemoryStore()
        org_id, user_id = _promote_org(store)
        project = store.create_project(
            name="wwf-calib-A",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="FY 2024",
            organisation_id=org_id,
            created_by=user_id,
        )
        upload_id, product_ids = _seed_wwf_upload(
            store,
            org_id=org_id,
            user_id=user_id,
            project_id=project.id,
            names=_NEUTRAL_NAMES,
        )

        _classify_wwf_to_terminal(
            store,
            org_id=org_id,
            project_id=project.id,
            upload_id=upload_id,
            user_id=user_id,
            provider=_FixedWWFProvider(0.78),
        )

        # Every product is classified...
        classified = [
            pid for pid in product_ids if store.get_wwf_classification(pid)
        ]
        assert len(classified) == len(product_ids)
        # ...and NONE of them is parked in review.
        assert _wwf_review_count(store, project.id) == 0


# ---------------------------------------------------------------------------
# B. Genuinely low-confidence rows still route to review
# ---------------------------------------------------------------------------


class TestWWFLowConfidenceStillReviews:
    def test_low_confidence_rows_populate_review_queue(self) -> None:
        """A clean fix must NOT blanket-accept: WWF rows at 0.55 (< 0.70)
        still create review items so ambiguous rows stay auditable."""
        store = InMemoryStore()
        org_id, user_id = _promote_org(store)
        project = store.create_project(
            name="wwf-calib-B",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="FY 2024",
            organisation_id=org_id,
            created_by=user_id,
        )
        upload_id, product_ids = _seed_wwf_upload(
            store,
            org_id=org_id,
            user_id=user_id,
            project_id=project.id,
            names=_NEUTRAL_NAMES,
        )

        _classify_wwf_to_terminal(
            store,
            org_id=org_id,
            project_id=project.id,
            upload_id=upload_id,
            user_id=user_id,
            provider=_FixedWWFProvider(0.55),
        )

        assert _wwf_review_count(store, project.id) == len(product_ids)


# ---------------------------------------------------------------------------
# C. Accepted re-classification clears the stale review item
# ---------------------------------------------------------------------------


class TestWWFStaleReviewClearedOnReaccept:
    def test_reaccept_removes_stale_low_confidence_review_item(self) -> None:
        """Run 1 at 0.55 parks the rows in review; run 2 (overwrite) at 0.78
        accepts them and the orchestrator removes the stale review items, so
        the aggregated needs_review drops back to 0."""
        store = InMemoryStore()
        org_id, user_id = _promote_org(store)
        project = store.create_project(
            name="wwf-calib-C",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="FY 2024",
            organisation_id=org_id,
            created_by=user_id,
        )
        upload_id, product_ids = _seed_wwf_upload(
            store,
            org_id=org_id,
            user_id=user_id,
            project_id=project.id,
            names=_NEUTRAL_NAMES,
        )

        # Run 1 — low confidence parks every row in review.
        _classify_wwf_to_terminal(
            store,
            org_id=org_id,
            project_id=project.id,
            upload_id=upload_id,
            user_id=user_id,
            provider=_FixedWWFProvider(0.55),
        )
        assert _wwf_review_count(store, project.id) == len(product_ids)

        # Run 2 — re-classify (overwrite) at clear confidence -> accepted ->
        # stale review items removed.
        _classify_wwf_to_terminal(
            store,
            org_id=org_id,
            project_id=project.id,
            upload_id=upload_id,
            user_id=user_id,
            provider=_FixedWWFProvider(0.78),
            overwrite=True,
        )
        assert _wwf_review_count(store, project.id) == 0


# ---------------------------------------------------------------------------
# D. WWF acceptance never touches the Protein Tracker review queue
# ---------------------------------------------------------------------------


class TestWWFAcceptDoesNotTouchPTReviewQueue:
    def test_pt_review_item_survives_wwf_acceptance(self) -> None:
        """The two methodologies keep separate review queues:
        ``remove_review_item`` on a WWF accept is scoped to WWF, so a
        pre-existing PT review item is left untouched."""
        store = InMemoryStore()
        org_id, user_id = _promote_org(store)
        project = store.create_project(
            name="wwf-calib-D",
            methodologies_enabled=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
            reporting_period_label="FY 2024",
            organisation_id=org_id,
            created_by=user_id,
        )
        # A PT product with a standing PT review item (separate queue).
        pt_upload_id = uuid4()
        pt_product = _make_pt_product(
            "Lentilles PT",
            project_id=project.id,
            organisation_id=org_id,
            upload_id=pt_upload_id,
        )
        store.add_product(pt_product)
        store.add_upload(
            Upload(
                id=pt_upload_id,
                organisation_id=org_id,
                project_id=project.id,
                storage_path=f"wwf-calib/{pt_upload_id}.csv",
                original_filename="pt.csv",
                status=UploadStatus.READY_FOR_CLASSIFICATION,
                row_count=1,
                uploaded_by=user_id,
                created_at=datetime.now(UTC),
            ),
            [pt_product.id],
        )
        _enqueue_review_item(
            store,
            pt_product.id,
            Methodology.PROTEIN_TRACKER,
            ManualReviewQueueReason.LOW_CONFIDENCE,
            datetime.now(UTC),
        )
        assert _pt_review_count(store, project.id) == 1

        # Now classify WWF rows at clear confidence (all accepted).
        wwf_upload_id, _ = _seed_wwf_upload(
            store,
            org_id=org_id,
            user_id=user_id,
            project_id=project.id,
            names=_NEUTRAL_NAMES[:4],
        )
        _classify_wwf_to_terminal(
            store,
            org_id=org_id,
            project_id=project.id,
            upload_id=wwf_upload_id,
            user_id=user_id,
            provider=_FixedWWFProvider(0.78),
        )

        # WWF queue empty (all accepted) BUT the PT review item survives.
        assert _wwf_review_count(store, project.id) == 0
        assert _pt_review_count(store, project.id) == 1

"""Demo golden classification — flag-gated deterministic path.

Covers (per the demo brief):

* flag OFF → demo catalogue follows the normal AI path; golden NOT applied;
* flag ON + recognised catalogue → 50/50 PT, 50/50 WWF, exactly 2 products
  in validation (PTWWF048 + PTWWF049), review items methodology-scoped (no
  leak), stale review items cleared, AI provider NEVER called;
* flag ON + NON-matching upload → golden NOT applied (AI path runs);
* provenance is honest (source=deterministic, rule_id=demo.golden.*);
* workflow aggregation reports the right classified / needs_review counts and
  the validation queue shows exactly the two intended products.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    advance_classification_job,
    create_classification_job,
)
from altera_api.api.orchestrator import classify_upload, list_review
from altera_api.api.state import InMemoryStore
from altera_api.api.workflow import compute_workflow_status
from altera_api.demo import golden_classification as golden
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
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
from altera_api.domain.review import ManualReviewQueueReason, ManualReviewStatus
from altera_api.domain.upload import Upload, UploadStatus

FLAG = "ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _demo_rows() -> list[tuple[str, str]]:
    """The (external_product_id, product_name) pairs of the demo catalogue,
    taken from the golden fixture itself."""
    return [(ext_id, entry.name) for ext_id, entry in golden._GOLDEN.items()]


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


def _make_product(
    ext_id: str,
    name: str,
    *,
    org_id: UUID,
    project_id: UUID,
    upload_id: UUID,
) -> NormalizedProduct:
    """A demo product with BOTH PT and WWF inputs populated (the demo CSV
    carries both methodologies' columns)."""
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=ext_id,
        product_name=name,
        brand=None,
        is_own_brand=False,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset(
            {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        ),
        pt_fields=PTProductFields(items_purchased=Decimal("100")),
        wwf_fields=WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.GROCERY_AMBIENT,
            is_own_brand=False,
        ),
        created_at=datetime.now(UTC),
    )


def _seed(
    store: InMemoryStore, rows: list[tuple[str, str]]
) -> tuple[UUID, UUID, UUID, list[UUID]]:
    org_id, user_id = _promote_org(store)
    project = store.create_project(
        name="demo-golden",
        methodologies_enabled=frozenset(
            {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        ),
        reporting_period_label="FY 2024",
        organisation_id=org_id,
        created_by=user_id,
    )
    upload_id = uuid4()
    products = [
        _make_product(
            ext_id, name, org_id=org_id, project_id=project.id, upload_id=upload_id
        )
        for ext_id, name in rows
    ]
    for p in products:
        store.add_product(p)
    product_ids = [p.id for p in products]
    store.add_upload(
        Upload(
            id=upload_id,
            organisation_id=org_id,
            project_id=project.id,
            storage_path=f"demo/{upload_id}.csv",
            original_filename="DEMO-50produits.csv",
            status=UploadStatus.READY_FOR_CLASSIFICATION,
            row_count=len(products),
            uploaded_by=user_id,
            created_at=datetime.now(UTC),
        ),
        product_ids,
    )
    return org_id, project.id, upload_id, product_ids


class _RecordingProvider(ClassifierProvider):
    """Records every batch call and returns a valid verdict for the
    methodology. Used to (a) assert the golden path NEVER calls AI, and
    (b) drive the normal (flag-off / non-demo) path so it completes."""

    def __init__(self, methodology: Methodology) -> None:
        self.methodology = methodology
        self.calls: list[Any] = []

    @property
    def model(self) -> str:
        return "demo-recording-fake"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
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
            if self.methodology is Methodology.PROTEIN_TRACKER:
                rows.append(
                    {
                        "id": row["id"],
                        "pt_group": "plant_based_core",
                        "confidence": 0.95,
                        "rationale": "recording fake",
                    }
                )
            else:
                rows.append(
                    {
                        "id": row["id"],
                        "wwf_food_group": "FG1",
                        "wwf_is_composite": False,
                        "wwf_fg1_subgroup": "legumes",
                        "confidence": 0.95,
                        "rationale": "recording fake",
                    }
                )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}), model=self.model
        )


def _run_job_to_terminal(
    store: InMemoryStore,
    *,
    org_id: UUID,
    project_id: UUID,
    upload_id: UUID,
    user_id: UUID | None,
    methodology: Methodology,
    provider: ClassifierProvider | None,
) -> None:
    job = create_classification_job(
        store,
        organisation_id=org_id,
        project_id=project_id,
        upload_id=upload_id,
        methodology=methodology,
        created_by=user_id,
    )
    for _ in range(20):
        job = advance_classification_job(store, job.id, ai_provider=provider)
        status = job.status.value
        if status.startswith("completed") or status == "failed":
            return job
    raise AssertionError("job did not reach terminal state")


def _pt_reviews(store: InMemoryStore, project_id: UUID) -> list:
    return store.list_review_items_for_project(
        project_id, methodology=Methodology.PROTEIN_TRACKER
    )


def _wwf_reviews(store: InMemoryStore, project_id: UUID) -> list:
    return store.list_review_items_for_project(
        project_id, methodology=Methodology.WWF
    )


# ---------------------------------------------------------------------------
# A. Flag OFF → normal AI path, golden NOT applied
# ---------------------------------------------------------------------------


class TestFlagDisabled:
    def test_demo_catalogue_follows_normal_ai_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(FLAG, raising=False)
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())
        provider = _RecordingProvider(Methodology.PROTEIN_TRACKER)

        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.PROTEIN_TRACKER,
            provider=provider,
        )

        # AI WAS called (normal path) ...
        assert provider.calls, "flag off must use the AI provider"
        # ... and NO classification carries the golden provenance.
        cls_map = store.get_pt_classifications_bulk(product_ids)
        assert cls_map, "products should be classified via the normal path"
        assert all(c.rule_id != golden.PT_RULE_ID for c in cls_map.values())


# ---------------------------------------------------------------------------
# B. Flag ON + recognised demo catalogue → golden path
# ---------------------------------------------------------------------------


class TestGoldenApplied:
    @pytest.fixture
    def applied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())
        pt_provider = _RecordingProvider(Methodology.PROTEIN_TRACKER)
        wwf_provider = _RecordingProvider(Methodology.WWF)
        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.PROTEIN_TRACKER,
            provider=pt_provider,
        )
        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.WWF,
            provider=wwf_provider,
        )
        return {
            "store": store,
            "project_id": project_id,
            "product_ids": product_ids,
            "pt_provider": pt_provider,
            "wwf_provider": wwf_provider,
        }

    def test_pt_50_of_50_categorised(self, applied) -> None:
        cls = applied["store"].get_pt_classifications_bulk(applied["product_ids"])
        assert len(cls) == 50

    def test_wwf_50_of_50_categorised(self, applied) -> None:
        cls = applied["store"].get_wwf_classifications_bulk(applied["product_ids"])
        assert len(cls) == 50

    def test_exactly_two_products_in_validation(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        pt = _pt_reviews(store, project_id)
        wwf = _wwf_reviews(store, project_id)
        # No PT review leak; exactly two WWF review items; two distinct
        # products visible in validation overall.
        assert len(pt) == 0
        assert len(wwf) == 2
        distinct_products = {i.product_id for i in (*pt, *wwf)}
        assert len(distinct_products) == 2

    def test_review_products_are_the_intended_two(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        ext_ids = {
            store.get_product(i.product_id).external_product_id
            for i in _wwf_reviews(store, project_id)
        }
        assert ext_ids == {"PTWWF048", "PTWWF049"}

    def test_review_items_are_methodology_scoped(self, applied) -> None:
        # The two review items are WWF-only; PT queue stays empty.
        store, project_id = applied["store"], applied["project_id"]
        assert all(
            i.methodology is Methodology.WWF
            for i in _wwf_reviews(store, project_id)
        )
        assert _pt_reviews(store, project_id) == []

    def test_provider_never_called_for_demo_catalogue(self, applied) -> None:
        # Privacy + determinism: no AI call for the recognised catalogue.
        assert applied["pt_provider"].calls == []
        assert applied["wwf_provider"].calls == []

    def test_provenance_is_honest_deterministic(self, applied) -> None:
        store, product_ids = applied["store"], applied["product_ids"]
        pt = store.get_pt_classifications_bulk(product_ids)
        wwf = store.get_wwf_classifications_bulk(product_ids)
        for c in pt.values():
            assert c.source is ClassificationSource.DETERMINISTIC
            assert c.rule_id == golden.PT_RULE_ID
            assert c.confidence == Decimal("1")
            assert c.ai_model is None and c.ai_prompt_version is None
        for c in wwf.values():
            assert c.source is ClassificationSource.DETERMINISTIC
            assert c.rule_id == golden.WWF_RULE_ID
            assert c.confidence == Decimal("1")

    def test_review_reason_and_notes_are_explicit(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        for item in _wwf_reviews(store, project_id):
            assert item.reason is ManualReviewQueueReason.REQUESTED
            assert item.status is ManualReviewStatus.IN_QUEUE
            assert item.rationale_notes  # non-empty, explains the demo intent

    def test_two_composite_products_are_composite(self, applied) -> None:
        store, product_ids = applied["store"], applied["product_ids"]
        wwf = store.get_wwf_classifications_bulk(product_ids)
        by_ext = {
            store.get_product(pid).external_product_id: wwf[pid]
            for pid in product_ids
            if pid in wwf
        }
        assert by_ext["PTWWF048"].wwf_is_composite is True
        assert by_ext["PTWWF049"].wwf_is_composite is True


# ---------------------------------------------------------------------------
# C. Workflow aggregation + validation queue
# ---------------------------------------------------------------------------


class TestWorkflowAggregation:
    def test_counts_and_validation_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())
        for methodology in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
            _run_job_to_terminal(
                store,
                org_id=org_id,
                project_id=project_id,
                upload_id=upload_id,
                user_id=store.default_user_id,
                methodology=methodology,
                provider=None,  # golden path needs no provider
            )

        project = store.get_project(project_id)
        status = compute_workflow_status(store, project)
        by_meth = status.classification_by_methodology
        assert by_meth["protein_tracker"].classified == 50
        assert by_meth["protein_tracker"].needs_review == 0
        assert by_meth["wwf"].classified == 50
        assert by_meth["wwf"].needs_review == 2

        # The validation experience shows exactly two product rows.
        reviews = list_review(store, project=project)
        assert len({r.product_id for r in reviews}) == 2
        assert {r.external_product_id for r in reviews} == {
            "PTWWF048",
            "PTWWF049",
        }

    def test_golden_path_needs_no_ai_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The job must complete cleanly even with ai_provider=None, because
        # the recognised demo catalogue never calls the provider.
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())
        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.WWF,
            provider=None,
        )
        assert len(store.get_wwf_classifications_bulk(product_ids)) == 50


# ---------------------------------------------------------------------------
# D. Stale review items cleared on (re-)classification
# ---------------------------------------------------------------------------


class TestStaleReviewCleared:
    def test_reclassification_clears_stale_review_items(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())

        # Pre-seed stale WWF review items on a handful of NON-review demo
        # products (simulating a prior AI run that parked them in review).
        from altera_api.api.orchestrator import _enqueue_review_item

        now = datetime.now(UTC)
        stale_targets = product_ids[:5]  # PTWWF001..005 (not 048/049)
        for pid in stale_targets:
            _enqueue_review_item(
                store,
                pid,
                Methodology.WWF,
                ManualReviewQueueReason.LOW_CONFIDENCE,
                now,
            )
        assert len(_wwf_reviews(store, project_id)) == 5

        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.WWF,
            provider=None,
        )

        # After the golden WWF run only the two intended items remain.
        remaining = _wwf_reviews(store, project_id)
        ext_ids = {
            store.get_product(i.product_id).external_product_id for i in remaining
        }
        assert ext_ids == {"PTWWF048", "PTWWF049"}


# ---------------------------------------------------------------------------
# E. Flag ON but NON-matching upload → golden NOT applied
# ---------------------------------------------------------------------------


class TestNonMatchingUpload:
    def test_non_demo_upload_uses_normal_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        # A real-looking catalogue: same id scheme is NOT enough — names
        # differ, so the fingerprint must reject it.
        rows = [(f"PTWWF{n:03d}", f"Mystery product {n}") for n in range(1, 51)]
        org_id, project_id, upload_id, product_ids = _seed(store, rows)
        provider = _RecordingProvider(Methodology.PROTEIN_TRACKER)

        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.PROTEIN_TRACKER,
            provider=provider,
        )

        # Golden did NOT fire: provider was called and no golden provenance.
        assert provider.calls
        cls = store.get_pt_classifications_bulk(product_ids)
        assert all(c.rule_id != golden.PT_RULE_ID for c in cls.values())


# ---------------------------------------------------------------------------
# F. Direct (synchronous) classify path parity
# ---------------------------------------------------------------------------


class TestDirectClassifyPath:
    def test_classify_upload_applies_golden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _demo_rows())
        project = store.get_project(project_id)
        provider = _RecordingProvider(Methodology.WWF)

        summary = classify_upload(
            store,
            project=project,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            ai_provider=provider,
            skip_deterministic=True,
        )

        assert summary.matched == 50
        assert summary.review_required_total == 2
        assert provider.calls == []  # no AI call for the demo catalogue
        assert len(store.get_wwf_classifications_bulk(product_ids)) == 50
        assert len(_wwf_reviews(store, project_id)) == 2


# ---------------------------------------------------------------------------
# G. Recognition unit tests
# ---------------------------------------------------------------------------


class TestRecognition:
    def test_recognises_exact_catalogue(self) -> None:
        store = InMemoryStore()
        _org, _proj, _upl, _pids = _seed(store, _demo_rows())
        products = store.list_products_by_ids(_pids)
        assert golden.is_demo_golden_upload(products) is True

    def test_rejects_missing_product(self) -> None:
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, _demo_rows())
        products = store.list_products_by_ids(pids[:49])
        assert golden.is_demo_golden_upload(products) is False

    def test_rejects_renamed_product(self) -> None:
        rows = _demo_rows()
        rows[0] = (rows[0][0], "Totally different product")
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        products = store.list_products_by_ids(pids)
        assert golden.is_demo_golden_upload(products) is False

    def test_accent_and_case_insensitive(self) -> None:
        # Same catalogue with stripped accents / different casing still
        # recognised (robust to CSV encoding differences).
        rows = [
            (ext, name.upper().replace("É", "E").replace("È", "E"))
            for ext, name in _demo_rows()
        ]
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        products = store.list_products_by_ids(pids)
        assert golden.is_demo_golden_upload(products) is True

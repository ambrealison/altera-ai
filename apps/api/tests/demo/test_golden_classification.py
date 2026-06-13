"""Demo golden classification — flag-gated deterministic path.

Primary coverage is the **current live demo file** ``DEMO.csv`` (the
``demo25`` catalogue, 25 products). It asserts:

* flag OFF → demo catalogue follows the normal AI path; golden NOT applied;
* flag ON + recognised catalogue → 25/25 PT, 25/25 WWF, PT needs_review == 2
  AND WWF needs_review == 2 for the SAME two product ids
  (``PTWWF019`` + ``PTWWF025``), AI provider NEVER called, stale review items
  cleared on BOTH methodologies, honest deterministic provenance;
* flag ON + NON-matching upload → golden NOT applied (AI path runs);
* workflow aggregation reports the right per-methodology counts.

A regression block keeps the earlier ``demo50`` catalogue working
(WWF-only review on ``PTWWF048`` / ``PTWWF049``).
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
from altera_api.demo.golden_classification import DEMO25, DEMO50, DemoCatalogue
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

# The current live demo catalogue and its two intended review products.
DEMO25_REVIEW_IDS = {"PTWWF019", "PTWWF025"}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _rows(catalogue: DemoCatalogue) -> list[tuple[str, str]]:
    return [(ext_id, entry.name) for ext_id, entry in catalogue.entries.items()]


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
    ext_id: str, name: str, *, org_id: UUID, project_id: UUID, upload_id: UUID
) -> NormalizedProduct:
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
            original_filename="DEMO.csv",
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
):
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


def _reviews(store: InMemoryStore, project_id: UUID, methodology: Methodology):
    return store.list_review_items_for_project(project_id, methodology=methodology)


def _review_ext_ids(
    store: InMemoryStore, project_id: UUID, methodology: Methodology
) -> set[str]:
    return {
        store.get_product(i.product_id).external_product_id
        for i in _reviews(store, project_id, methodology)
    }


# ---------------------------------------------------------------------------
# A. Gating — default ON (recognition-only); kill switch forces OFF
# ---------------------------------------------------------------------------


class TestGating:
    def test_default_no_env_applies_golden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The demo path now defaults ON (gated by strict recognition alone),
        # so the recognised demo catalogue is golden-classified with NO env
        # var set and the AI provider is never called.
        monkeypatch.delenv(FLAG, raising=False)
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
        provider = _RecordingProvider(Methodology.WWF)

        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.WWF,
            provider=provider,
        )

        assert provider.calls == []
        cls = store.get_wwf_classifications_bulk(product_ids)
        assert len(cls) == 25
        assert all(c.rule_id == golden.WWF_RULE_ID for c in cls.values())

    def test_kill_switch_false_uses_normal_ai_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Setting the env var to a falsy value forces the demo path OFF even
        # for the recognised catalogue → normal AI path runs.
        monkeypatch.setenv(FLAG, "false")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
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

        assert provider.calls, "kill switch off must use the AI provider"
        cls_map = store.get_pt_classifications_bulk(product_ids)
        assert cls_map
        assert all(c.rule_id != golden.PT_RULE_ID for c in cls_map.values())


# ---------------------------------------------------------------------------
# B. Flag ON + current 25-product demo catalogue → golden path
# ---------------------------------------------------------------------------


class TestDemo25GoldenApplied:
    @pytest.fixture
    def applied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
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

    def test_pt_25_of_25_categorised(self, applied) -> None:
        assert len(applied["store"].get_pt_classifications_bulk(applied["product_ids"])) == 25

    def test_wwf_25_of_25_categorised(self, applied) -> None:
        assert len(applied["store"].get_wwf_classifications_bulk(applied["product_ids"])) == 25

    def test_pt_has_exactly_two_in_review(self, applied) -> None:
        assert len(_reviews(applied["store"], applied["project_id"], Methodology.PROTEIN_TRACKER)) == 2

    def test_wwf_has_exactly_two_in_review(self, applied) -> None:
        assert len(_reviews(applied["store"], applied["project_id"], Methodology.WWF)) == 2

    def test_pt_and_wwf_review_products_are_the_same(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        pt_ids = _review_ext_ids(store, project_id, Methodology.PROTEIN_TRACKER)
        wwf_ids = _review_ext_ids(store, project_id, Methodology.WWF)
        assert pt_ids == wwf_ids == DEMO25_REVIEW_IDS

    def test_review_items_methodology_scoped(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        assert all(
            i.methodology is Methodology.PROTEIN_TRACKER
            for i in _reviews(store, project_id, Methodology.PROTEIN_TRACKER)
        )
        assert all(
            i.methodology is Methodology.WWF
            for i in _reviews(store, project_id, Methodology.WWF)
        )

    def test_provider_never_called(self, applied) -> None:
        assert applied["pt_provider"].calls == []
        assert applied["wwf_provider"].calls == []

    def test_provenance_is_honest_deterministic(self, applied) -> None:
        store, product_ids = applied["store"], applied["product_ids"]
        for c in store.get_pt_classifications_bulk(product_ids).values():
            assert c.source is ClassificationSource.DETERMINISTIC
            assert c.rule_id == golden.PT_RULE_ID
            assert c.confidence == Decimal("1")
            assert c.ai_model is None and c.ai_prompt_version is None
        for c in store.get_wwf_classifications_bulk(product_ids).values():
            assert c.source is ClassificationSource.DETERMINISTIC
            assert c.rule_id == golden.WWF_RULE_ID
            assert c.confidence == Decimal("1")

    def test_review_reason_and_notes_explicit(self, applied) -> None:
        store, project_id = applied["store"], applied["project_id"]
        for methodology in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
            for item in _reviews(store, project_id, methodology):
                assert item.reason is ManualReviewQueueReason.REQUESTED
                assert item.status is ManualReviewStatus.IN_QUEUE
                assert item.rationale_notes

    def test_pizza_is_composite_vegetarian(self, applied) -> None:
        # PTWWF025 "Pizza fromage tomate" must be a WWF composite with the
        # VEGETARIAN Step-1 bucket — NOT a plain FG2 dairy product. (FG2 is
        # only the schema filler the domain model requires; the bucket is
        # what the calculation and the UI use.)
        from altera_api.domain.wwf import WWFCompositeStep1Bucket

        store, product_ids = applied["store"], applied["product_ids"]
        wwf = store.get_wwf_classifications_bulk(product_ids)
        by_ext = {
            store.get_product(pid).external_product_id: wwf[pid]
            for pid in product_ids
            if pid in wwf
        }
        pizza = by_ext["PTWWF025"]
        assert pizza.wwf_is_composite is True
        assert pizza.composite_step1_bucket is WWFCompositeStep1Bucket.VEGETARIAN


# ---------------------------------------------------------------------------
# C. Workflow aggregation + validation queue (the demo cards)
# ---------------------------------------------------------------------------


class TestDemo25WorkflowAggregation:
    def test_cards_and_validation_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
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
        by_meth = compute_workflow_status(store, project).classification_by_methodology
        # The exact card state the demo must show.
        assert by_meth["protein_tracker"].classified == 25
        assert by_meth["protein_tracker"].total == 25
        assert by_meth["protein_tracker"].needs_review == 2
        assert by_meth["wwf"].classified == 25
        assert by_meth["wwf"].total == 25
        assert by_meth["wwf"].needs_review == 2

        # The validation queue resolves to the same two products.
        reviews = list_review(store, project=project)
        assert {r.external_product_id for r in reviews} == DEMO25_REVIEW_IDS


# ---------------------------------------------------------------------------
# D. Stale review items cleared on BOTH methodologies
# ---------------------------------------------------------------------------


class TestDemo25StaleReviewCleared:
    def test_reclassification_clears_stale_review_items_both_methodologies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))

        from altera_api.api.orchestrator import _enqueue_review_item

        now = datetime.now(UTC)
        # Pre-seed stale review items on the FIRST five products (none of
        # which is a designated review product) for BOTH methodologies.
        for pid in product_ids[:5]:
            for methodology in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
                _enqueue_review_item(
                    store, pid, methodology, ManualReviewQueueReason.LOW_CONFIDENCE, now
                )
        assert len(_reviews(store, project_id, Methodology.PROTEIN_TRACKER)) == 5
        assert len(_reviews(store, project_id, Methodology.WWF)) == 5

        for methodology in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
            _run_job_to_terminal(
                store,
                org_id=org_id,
                project_id=project_id,
                upload_id=upload_id,
                user_id=store.default_user_id,
                methodology=methodology,
                provider=None,
            )

        assert _review_ext_ids(store, project_id, Methodology.PROTEIN_TRACKER) == DEMO25_REVIEW_IDS
        assert _review_ext_ids(store, project_id, Methodology.WWF) == DEMO25_REVIEW_IDS


# ---------------------------------------------------------------------------
# D2. Re-run after a prior AI run replaces everything with golden
#
# Reproduces the reported bug: the catalogue was first classified by live AI
# (flag off) — WWF landed every row in review — then the flag was enabled and
# classification re-run. The re-run MUST replace the stale AI state with the
# golden fixture (PT 2 + WWF 2), not skip already-classified products.
# ---------------------------------------------------------------------------


class TestDemo25ReRunReplacesPriorAiState:
    def test_rerun_with_flag_on_overrides_prior_ai_reviews(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.api.orchestrator import _enqueue_review_item
        from altera_api.domain.wwf import (
            WWFFG1Subgroup,
            WWFFoodGroup,
            WWFProductClassification,
        )

        # --- 1. Prior live-AI run (flag OFF): every WWF row low-confidence
        #        → all 25 stored as source=ai AND parked in review. ---
        monkeypatch.delenv(FLAG, raising=False)
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
        now = datetime.now(UTC)
        for pid in product_ids:
            store.upsert_wwf_classification(
                WWFProductClassification(
                    product_id=pid,
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_is_composite=False,
                    fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                    source=ClassificationSource.AI,
                    confidence=Decimal("0.5"),
                    ai_prompt_version="demo-test-v1",
                    ai_model="demo-test-model",
                    updated_at=now,
                )
            )
            _enqueue_review_item(
                store, pid, Methodology.WWF, ManualReviewQueueReason.LOW_CONFIDENCE, now
            )
        assert len(_reviews(store, project_id, Methodology.WWF)) == 25  # the bug

        # --- 2. Enable the flag and RE-RUN classification. ---
        monkeypatch.setenv(FLAG, "true")
        provider = _RecordingProvider(Methodology.WWF)
        _run_job_to_terminal(
            store,
            org_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            user_id=store.default_user_id,
            methodology=Methodology.WWF,
            provider=provider,
        )

        # --- 3. Golden fully replaced the AI state: 2 review (not 25), no AI
        #        call, deterministic provenance. ---
        assert _review_ext_ids(store, project_id, Methodology.WWF) == DEMO25_REVIEW_IDS
        assert provider.calls == []
        wwf = store.get_wwf_classifications_bulk(product_ids)
        assert len(wwf) == 25
        assert all(c.rule_id == golden.WWF_RULE_ID for c in wwf.values())


# ---------------------------------------------------------------------------
# E. Flag ON but NON-matching upload → golden NOT applied
# ---------------------------------------------------------------------------


class TestNonMatchingUpload:
    def test_non_demo_upload_uses_normal_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        # A real retailer catalogue: real SKU ids (NOT the demo PTWWF ids),
        # so neither the fingerprint nor the id-set match → golden not applied.
        rows = [(f"SKU{n:05d}", f"Mystery product {n}") for n in range(1, 26)]
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

        assert provider.calls
        cls = store.get_pt_classifications_bulk(product_ids)
        assert all(c.rule_id != golden.PT_RULE_ID for c in cls.values())


# ---------------------------------------------------------------------------
# F. Direct (synchronous) classify path parity
# ---------------------------------------------------------------------------


class TestDirectClassifyPath:
    @pytest.mark.parametrize(
        "methodology", [Methodology.PROTEIN_TRACKER, Methodology.WWF]
    )
    def test_classify_upload_applies_golden(
        self, monkeypatch: pytest.MonkeyPatch, methodology: Methodology
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO25))
        project = store.get_project(project_id)
        provider = _RecordingProvider(methodology)

        summary = classify_upload(
            store,
            project=project,
            upload_id=upload_id,
            methodology=methodology,
            ai_provider=provider,
            skip_deterministic=True,
        )

        assert summary.matched == 25
        assert summary.review_required_total == 2  # both PT and WWF review 2
        assert provider.calls == []
        assert len(_reviews(store, project_id, methodology)) == 2


# ---------------------------------------------------------------------------
# G. Recognition unit tests
# ---------------------------------------------------------------------------


class TestRecognition:
    @pytest.mark.parametrize("catalogue", [DEMO25, DEMO50])
    def test_recognises_exact_catalogue(self, catalogue: DemoCatalogue) -> None:
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, _rows(catalogue))
        products = store.list_products_by_ids(pids)
        recognised = golden.recognise_demo_catalogue(products)
        assert recognised is not None and recognised.key == catalogue.key

    def test_rejects_missing_product(self) -> None:
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, _rows(DEMO25))
        assert golden.recognise_demo_catalogue(store.list_products_by_ids(pids[:24])) is None

    def test_rejects_changed_external_id(self) -> None:
        # Changing an external id breaks the id set → not a demo catalogue.
        rows = _rows(DEMO25)
        rows[0] = ("NOT-A-DEMO-ID", rows[0][1])
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        assert golden.recognise_demo_catalogue(store.list_products_by_ids(pids)) is None

    def test_accent_and_case_insensitive(self) -> None:
        rows = [
            (ext, name.upper().replace("É", "E").replace("È", "E"))
            for ext, name in _rows(DEMO25)
        ]
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        recognised = golden.recognise_demo_catalogue(store.list_products_by_ids(pids))
        assert recognised is not None and recognised.key == "demo25"

    def test_catalogues_have_distinct_fingerprints(self) -> None:
        fps = golden.demo_catalogue_fingerprints()
        assert fps["demo25"] != fps["demo50"]

    def test_recognises_by_exact_id_set_despite_name_differences(self) -> None:
        # Robustness: the same 25 demo external ids but with DIFFERENT product
        # names (simulating an encoding / whitespace / edit difference in the
        # stored data) are still recognised via the exact id-set match, so the
        # demo never silently falls back to live AI over a name mismatch.
        rows = [(ext, f"renamed {ext}") for ext, _name in _rows(DEMO25)]
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        recognised = golden.recognise_demo_catalogue(store.list_products_by_ids(pids))
        assert recognised is not None and recognised.key == "demo25"

    def test_id_set_match_does_not_cross_catalogues(self) -> None:
        # A 25-id upload must never be mistaken for the 50-product catalogue.
        rows = [(ext, f"x {ext}") for ext, _ in _rows(DEMO25)]
        store = InMemoryStore()
        _org, _proj, _upl, pids = _seed(store, rows)
        assert golden.recognise_demo_catalogue(
            store.list_products_by_ids(pids)
        ).key == "demo25"


# ---------------------------------------------------------------------------
# H. demo50 regression (earlier catalogue, WWF-only review)
# ---------------------------------------------------------------------------


class TestDemo50Regression:
    def test_demo50_still_golden_wwf_only_review(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(FLAG, "true")
        store = InMemoryStore()
        org_id, project_id, upload_id, product_ids = _seed(store, _rows(DEMO50))
        for methodology in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
            _run_job_to_terminal(
                store,
                org_id=org_id,
                project_id=project_id,
                upload_id=upload_id,
                user_id=store.default_user_id,
                methodology=methodology,
                provider=None,
            )

        assert len(store.get_pt_classifications_bulk(product_ids)) == 50
        assert len(store.get_wwf_classifications_bulk(product_ids)) == 50
        # demo50 keeps WWF-only review on the two composite products.
        assert _review_ext_ids(store, project_id, Methodology.PROTEIN_TRACKER) == set()
        assert _review_ext_ids(store, project_id, Methodology.WWF) == {
            "PTWWF048",
            "PTWWF049",
        }

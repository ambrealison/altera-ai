"""Phase WWF-S — provider-exception batch resilience.

Operator ran PT+WWF classification on a 100-product dataset and got
"50 réussies / 50 à résoudre" for WWF. Diagnosis: the orchestrator's
provider-exception branch hard-failed every product in the second
batch with ``_queue_unknown_wwf`` even though the WWF readable
fallback would have recovered most rows. The in-batch parse-failure
path (``_emit_failed_or_fallback`` in ``batch_classifier.py``) already
has the fallback wired up; the orchestrator branch did not.

Covered:
  A. Methodology-aware batch size — WWF defaults are capped at 25
     even when the global env raises PT to 50.
  B. Provider-exception during a WWF batch recovers readable rows via
     ``classify_wwf_readable_fallback`` instead of hard-failing all
     50.
  C. Retry of a batch-level failure halves the batch size (with a
     floor of 10) so a flaky provider response can't keep eating the
     same batch on every retry.
  D. End-to-end 20-product two-batch regression — batch 2 provider
     crash recovers readable rows.
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
    ProviderError,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    MAX_BATCH_SIZE,
    _default_batch_size,
    _readable_fallback_for_product,
    advance_classification_job,
    create_classification_job,
    retry_failed_in_classification_job,
)
from altera_api.api.state import InMemoryStore
from altera_api.domain.classification_job import ClassificationJobStatus
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
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
)

# ---------------------------------------------------------------------------
# A. Methodology-aware batch size — pure unit tests
# ---------------------------------------------------------------------------


class TestMethodologyAwareBatchSize:
    def test_pt_default_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(
            "ALTERA_AI_CLASSIFICATION_BATCH_SIZE", raising=False
        )
        monkeypatch.delenv(
            "ALTERA_WWF_CLASSIFICATION_BATCH_SIZE", raising=False
        )
        assert _default_batch_size(Methodology.PROTEIN_TRACKER) == 25

    def test_wwf_default_capped_at_25(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "50")
        monkeypatch.delenv(
            "ALTERA_WWF_CLASSIFICATION_BATCH_SIZE", raising=False
        )
        assert _default_batch_size(Methodology.PROTEIN_TRACKER) == 50
        assert _default_batch_size(Methodology.WWF) == 25

    def test_wwf_env_override_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "50")
        monkeypatch.setenv("ALTERA_WWF_CLASSIFICATION_BATCH_SIZE", "40")
        assert _default_batch_size(Methodology.WWF) == 40

    def test_wwf_cap_clamped_to_max_batch_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_WWF_CLASSIFICATION_BATCH_SIZE", "999")
        assert _default_batch_size(Methodology.WWF) <= MAX_BATCH_SIZE


# ---------------------------------------------------------------------------
# Direct-orchestrator helpers (no HTTP)
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
        external_product_id=f"ext-{name[:10]}-{uuid4().hex[:6]}",
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
        external_product_id=f"ext-{name[:10]}-{uuid4().hex[:6]}",
        product_name=name,
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("100")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


def _seed_store(
    store: InMemoryStore,
    methodology: Methodology,
    product_names: list[str],
) -> tuple[UUID, UUID, UUID, list[UUID]]:
    """Promote the default org, create a project + upload + N products."""
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
    project = store.create_project(
        name="phase-wwf-s",
        methodologies_enabled=frozenset({methodology}),
        reporting_period_label="FY 2024",
        organisation_id=org_id,
        created_by=user_id,
    )
    upload_id = uuid4()
    products = [
        (
            _make_wwf_product(
                n,
                project_id=project.id,
                organisation_id=org_id,
                upload_id=upload_id,
            )
            if methodology is Methodology.WWF
            else _make_pt_product(
                n,
                project_id=project.id,
                organisation_id=org_id,
                upload_id=upload_id,
            )
        )
        for n in product_names
    ]
    for p in products:
        store.add_product(p)
    product_ids = [p.id for p in products]
    upload = Upload(
        id=upload_id,
        organisation_id=org_id,
        project_id=project.id,
        storage_path=f"phase-wwf-s/{upload_id}.csv",
        original_filename="phase-wwf-s.csv",
        status=UploadStatus.READY_FOR_CLASSIFICATION,
        row_count=len(products),
        uploaded_by=user_id,
        created_at=datetime.now(UTC),
    )
    store.add_upload(upload, product_ids)
    return org_id, project.id, upload_id, product_ids


class _AlwaysRaisingProvider(ClassifierProvider):
    model_name = "phase-wwf-s-fake-raise"

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        raise ProviderError("simulated provider crash")


class _Batch2RaisingProvider(ClassifierProvider):
    """First batch succeeds with valid WWF JSON; subsequent batches
    raise — exactly the failure mode the operator hit on the
    100-product run (batch 1 succeeds, batch 2 crashes).
    """

    model_name = "phase-wwf-s-batch2-fake"

    def __init__(self, methodology: Methodology) -> None:
        self.calls: list[Any] = []
        self.methodology = methodology

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
        if len(self.calls) >= 2:
            raise ProviderError("batch 2 crash")
        rows = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row:
                continue
            if self.methodology is Methodology.WWF:
                rows.append(
                    {
                        "id": row["id"],
                        "wwf_food_group": "FG1",
                        "wwf_is_composite": False,
                        "wwf_fg1_subgroup": "legumes",
                        "confidence": 0.92,
                        "rationale": "fake legumes",
                    }
                )
            else:
                rows.append(
                    {
                        "id": row["id"],
                        "pt_group": "plant_based_core",
                        "confidence": 0.95,
                        "rationale": "fake core",
                    }
                )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}), model=self.model_name
        )


def _run_to_terminal(
    store: InMemoryStore,
    job_id: UUID,
    provider: ClassifierProvider,
    max_advances: int = 10,
):
    job = None
    for _ in range(max_advances):
        job = advance_classification_job(store, job_id, ai_provider=provider)
        if job.status.value.startswith("completed") or job.status.value == "failed":
            return job
    return job


# ---------------------------------------------------------------------------
# B. Provider-exception recovery via readable fallback
# ---------------------------------------------------------------------------


class TestProviderExceptionRecovery:
    def test_wwf_provider_error_recovers_readable_rows(self) -> None:
        store = InMemoryStore()
        # Names that match the WWF readable fallback. The fallback does
        # not currently cover plain fresh fruit/veg (FG4) so we pick
        # families that do fire: legumes, composite, alt-protein, dairy,
        # plant-fat.
        names = [
            "Lentilles vertes",          # FG1 legumes
            "Cassoulet maison",          # composite meat-based
            "Tofu nature bio",           # FG1 alt-protein
            "Yaourt nature",             # FG2 dairy
            "Huile d'olive vierge",      # FG3 plant fat
        ]
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=5,
        )
        terminal = _run_to_terminal(store, job.id, _AlwaysRaisingProvider())
        assert terminal is not None
        # All five are food-recognizable so the WWF readable fallback
        # should recover them — failed_product_ids must NOT include
        # readable rows.
        assert terminal.recovered_rows >= 4, (
            f"expected >=4 recovered, got {terminal.recovered_rows}"
        )
        # WWF classifications must exist with a methodology-group
        # food_group for the recovered products.
        wwf_map = store.get_wwf_classifications_bulk(product_ids)
        recovered_count = sum(
            1
            for cls in wwf_map.values()
            if cls.wwf_food_group.is_methodology_group
        )
        assert recovered_count >= 4

    def test_wwf_provider_error_unusable_names_still_fail(self) -> None:
        store = InMemoryStore()
        # Names with no WWF-guard match → readable fallback returns
        # None → rows must end in ``failed_product_ids``.
        names = ["xqz one", "xqz two", "xqz three"]
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=5,
        )
        terminal = _run_to_terminal(store, job.id, _AlwaysRaisingProvider())
        assert terminal is not None
        # No readable fallback match → all 3 stay as failed.
        assert len(terminal.failed_product_ids) == 3
        assert terminal.recovered_rows == 0

    def test_pt_provider_error_recovers_readable_rows(self) -> None:
        store = InMemoryStore()
        names = ["Tofu nature", "Lentilles vertes", "Steak boeuf 5%"]
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.PROTEIN_TRACKER, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.PROTEIN_TRACKER,
            batch_size=5,
        )
        terminal = _run_to_terminal(store, job.id, _AlwaysRaisingProvider())
        assert terminal is not None
        # PT readable fallback recovers at least 2/3 readable names.
        assert terminal.recovered_rows >= 2

    def test_helper_returns_none_for_unusable_name(self) -> None:
        product = _make_wwf_product(
            "??",
            project_id=uuid4(),
            organisation_id=uuid4(),
            upload_id=uuid4(),
        )
        assert (
            _readable_fallback_for_product(
                product, Methodology.WWF, datetime.now(UTC)
            )
            is None
        )

    def test_helper_returns_wwf_classification_for_readable_name(
        self,
    ) -> None:
        product = _make_wwf_product(
            "Lentilles corail bio",
            project_id=uuid4(),
            organisation_id=uuid4(),
            upload_id=uuid4(),
        )
        out = _readable_fallback_for_product(
            product, Methodology.WWF, datetime.now(UTC)
        )
        assert out is not None
        assert out.wwf_food_group is WWFFoodGroup.FG1
        assert out.fg1_subgroup is WWFFG1Subgroup.LEGUMES


# ---------------------------------------------------------------------------
# C. Retry behaviour halves batch size on batch-level failure
# ---------------------------------------------------------------------------


class TestRetryBatchSize:
    def test_retry_halves_batch_size_when_prior_batch_failed_wholesale(
        self,
    ) -> None:
        store = InMemoryStore()
        names = ["xqz one"] * 30  # unrecognizable so they fail
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=20,
        )
        # Simulate prior run finishing with a 20-row batch failure.
        terminal = job.with_progress(
            status=ClassificationJobStatus.COMPLETED_WITH_ERRORS,
            pending_product_ids=(),
            processed_products=len(product_ids),
            failed_product_ids=tuple(product_ids[:20]),
            completed_at=datetime.now(UTC),
        )
        store.update_classification_job(terminal)
        retried = retry_failed_in_classification_job(store, job.id)
        # 20 >= batch_size (20) → retry halves to max(10, 20 // 2) = 10.
        assert retried.batch_size == 10
        assert retried.total_products == 20

    def test_retry_keeps_batch_size_when_only_a_few_failed(self) -> None:
        store = InMemoryStore()
        names = ["xqz one"] * 30
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=20,
        )
        terminal = job.with_progress(
            status=ClassificationJobStatus.COMPLETED_WITH_ERRORS,
            pending_product_ids=(),
            processed_products=len(product_ids),
            failed_product_ids=tuple(product_ids[:3]),
            completed_at=datetime.now(UTC),
        )
        store.update_classification_job(terminal)
        retried = retry_failed_in_classification_job(store, job.id)
        # 3 < batch_size → keep 20.
        assert retried.batch_size == 20

    def test_retry_floors_at_10(self) -> None:
        store = InMemoryStore()
        names = ["xqz one"] * 30
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=12,
        )
        terminal = job.with_progress(
            status=ClassificationJobStatus.COMPLETED_WITH_ERRORS,
            pending_product_ids=(),
            processed_products=len(product_ids),
            failed_product_ids=tuple(product_ids[:12]),
            completed_at=datetime.now(UTC),
        )
        store.update_classification_job(terminal)
        retried = retry_failed_in_classification_job(store, job.id)
        # 12 // 2 = 6 but floor is 10.
        assert retried.batch_size == 10


# ---------------------------------------------------------------------------
# D. Two-batch regression — batch 2 provider crash
# ---------------------------------------------------------------------------


class TestTwoBatchRegression:
    def test_wwf_batch_2_crash_does_not_lose_readable_rows(self) -> None:
        store = InMemoryStore()
        # 20 readable WWF-recognizable names — batch 1 (10) succeeds via
        # provider, batch 2 (10) hits the simulated crash. Before
        # Phase WWF-S, batch 2 lost all 10 to ``unknown +
        # AI_PROVIDER_ERROR``. After WWF-S the readable fallback
        # recovers them.
        names = [f"Lentilles vertes lot {i}" for i in range(10)] + [
            f"Cassoulet maison lot {i}" for i in range(10)
        ]
        org_id, project_id, upload_id, product_ids = _seed_store(
            store, Methodology.WWF, names
        )
        job = create_classification_job(
            store,
            organisation_id=org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.WWF,
            batch_size=10,
        )
        provider = _Batch2RaisingProvider(Methodology.WWF)
        terminal = _run_to_terminal(store, job.id, provider)
        assert terminal is not None
        wwf_map = store.get_wwf_classifications_bulk(product_ids)
        recovered_count = sum(
            1
            for cls in wwf_map.values()
            if cls.wwf_food_group.is_methodology_group
        )
        # Critical assertion: ≥18 of the 20 are classified. Before
        # WWF-S only 10 (batch 1) were classified.
        assert recovered_count >= 18, (
            f"expected >=18 recovered, got {recovered_count}"
        )
        assert len(terminal.failed_product_ids) <= 2

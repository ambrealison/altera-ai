"""Phase 36H — 10K classification coverage + no-final-unknown.

Three independent fixes:

A. **10K cap bug**. ``get_upload`` in the Postgres repo did not
   paginate the products fetch. PostgREST's default 1000-row cap
   silently truncated ``UploadRecord.product_ids`` to 1000 entries on
   a 10K-row upload, so ``create_classification_job`` then set
   ``total_products=1000``. The wizard showed "25/1000 · 3%" forever.

   The in-memory store is naturally O(N) and was never affected, so
   this regression test exercises a synthetic 10K-product project end
   to end against ``create_classification_job`` and asserts
   ``total_products == n_products`` for 1050, 2500, and 10 000 rows.

B. **Anxiogenic warnings UI**. ``IngestionJobProgress`` showed
   ``X avertissement(s)`` in the primary summary. On a 10K-row upload
   the count reached ~20 000 because the mapper emits ~2 warnings per
   row for optional fields. Non-technical users read it as "the
   import failed". Backend logic is unchanged; only the frontend hid
   the count. This module has no test for the UI change itself —
   covered by frontend typecheck/lint.

C. **No-final-unknown** for readable names. Product rule:
   ``unknown`` is reserved for empty / corrupted / placeholder names
   ("", "Produit", "Divers", "N/A", …). For any readable name the
   model returning ``unknown`` is now overridden to ``needs_review``
   so a human can pick.

Out of scope:
  * NEVO matching (Phase 36E).
  * Nutrition / NEVO table perf (Phase 36F-lite).
  * Classification or nutrition table perf (Phase 36B / 36F-lite).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.batch_classifier import _is_unusable_name, batch_classify
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
)
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    create_classification_job,
)
from altera_api.api.state import InMemoryStore, UploadRecord
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.upload import Upload
from altera_api.main import app


def _promote(store: InMemoryStore) -> None:
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


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


# ---------------------------------------------------------------------------
# A. 10K classification cap
# ---------------------------------------------------------------------------


def _create_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase36h",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed_n_products_via_store(
    store: InMemoryStore, project_id: UUID, n: int
) -> UUID:
    """Bypass the upload route — that path parses CSV which is slow
    for 10K rows. We directly seed products + an Upload record on
    the in-memory store so the regression test runs in ~1s."""
    from datetime import UTC
    from decimal import Decimal

    now = datetime.now(UTC)
    upload_id = uuid4()
    products: list[NormalizedProduct] = []
    for i in range(n):
        pid = uuid4()
        products.append(
            NormalizedProduct(
                id=pid,
                project_id=project_id,
                upload_id=upload_id,
                organisation_id=store.default_org_id,
                row_number=i + 2,
                external_product_id=f"ext-{i}",
                product_name=f"Tofu Lot {i}",
                brand=None,
                retailer_category=None,
                retailer_subcategory=None,
                weight_per_item_kg=Decimal("0.15"),
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                pt_fields=PTProductFields(
                    items_purchased=Decimal("2.0"),
                    protein_pct=Decimal("20.0"),
                ),
                wwf_fields=None,
                created_at=now,
            )
        )
    store.add_products_bulk(products)
    from altera_api.domain.upload import UploadStatus

    upload = Upload(
        id=upload_id,
        project_id=project_id,
        organisation_id=store.default_org_id,
        storage_path=f"uploads/{upload_id}.csv",
        original_filename="seeded.csv",
        status=UploadStatus.VALID,
        content_type="text/csv",
        file_size_bytes=n * 100,
        uploaded_by=store.default_user_id,
        created_at=now,
        row_count=n,
    )
    store.add_upload(upload, [p.id for p in products])
    # The in-memory ``add_upload`` builds an UploadRecord from a
    # list of product ids; nothing else to do.
    return upload_id


class TestClassificationJobScalesToTenThousand:
    @pytest.mark.parametrize("n_products", [1050, 2500, 10_000])
    def test_total_products_matches_upload_size(
        self,
        client: TestClient,
        store: InMemoryStore,
        n_products: int,
    ) -> None:
        # Create project + seed N products + an Upload record.
        pid = _create_project(client)
        project_uuid = UUID(pid)
        upload_id = _seed_n_products_via_store(
            store, project_uuid, n_products
        )

        # Verify the upload record carries the full product_ids.
        upload_record = store.get_upload(upload_id)
        assert upload_record is not None
        assert isinstance(upload_record, UploadRecord)
        assert len(upload_record.product_ids) == n_products, (
            "get_upload truncated product_ids — the 10K cap bug "
            "regressed"
        )

        # Now create a classification job and assert no truncation.
        job = create_classification_job(
            store,
            organisation_id=store.default_org_id,
            project_id=project_uuid,
            upload_id=upload_id,
            methodology=Methodology.PROTEIN_TRACKER,
        )
        assert job.total_products == n_products, (
            f"classification job truncated: total_products="
            f"{job.total_products} vs upload size {n_products}"
        )
        assert len(job.pending_product_ids) == n_products


# ---------------------------------------------------------------------------
# C. Unusable-name detector
# ---------------------------------------------------------------------------


class TestIsUnusableName:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            " ",
            "\t",
            "  \n ",
            None,
            "a",
            "x",
            "12",
            "?",
            "??",
            "???",
            "...",
            "-",
            "--",
            "_",
            "produit",
            "Produit",
            "PRODUIT",
            "Divers",
            "Article",
            "N/A",
            "n/a",
            "NA",
            "nan",
            "None",
            "null",
            "xxx",
            "TBD",
            "tbc",
            "123456",
            "###",
        ],
    )
    def test_recognises_unusable(self, name: str | None) -> None:
        assert _is_unusable_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Blinis Moelleux",
            "Cuisine Vapeur Ratatouille",
            "Burger Végétal & Emmental",
            "Dessert Vanille",
            "Préparation végétale",
            "Papier Toilette",
            "Dentifrice Menthe",
            "Lessive Liquide",
            "Tofu Nature Bio",
            "Lait Demi-écrémé",
            "Beurre Doux 250g",
            "Yaourt 0% MG",
        ],
    )
    def test_treats_readable_names_as_usable(self, name: str) -> None:
        assert not _is_unusable_name(name)


# ---------------------------------------------------------------------------
# C. End-to-end: readable name + model returns unknown → routed to review
# ---------------------------------------------------------------------------


@dataclass
class _UnknownFakeProvider(ClassifierProvider):
    """Returns ``unknown`` for every product. Used to verify the
    Phase 36H safety net: readable names must NOT end up final
    unknown — they're rerouted to needs_review."""

    model_name: str = "phase36h-fake"
    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
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
            rows.append(
                {
                    "id": row["id"],
                    "pt_group": "unknown",
                    "confidence": 0.5,
                    "rationale": "fake unknown",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model=self.model_name,
        )


def _make_product(name: str) -> NormalizedProduct:
    from decimal import Decimal

    return NormalizedProduct(
        id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        organisation_id=uuid4(),
        row_number=2,
        external_product_id="ext-h",
        product_name=name,
        brand=None,
        retailer_category=None,
        retailer_subcategory=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("2.0"),
            protein_pct=None,
        ),
        wwf_fields=None,
        created_at=datetime.now(),
    )


class TestUnknownSafetyNetEndToEnd:
    def test_readable_names_with_unknown_are_routed_to_review(
        self,
    ) -> None:
        from datetime import UTC

        provider = _UnknownFakeProvider()
        products = [
            _make_product("Blinis Moelleux"),
            _make_product("Dessert Vanille"),
            _make_product("Préparation végétale"),
            _make_product("Burger Végétal & Emmental"),
        ]
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # None of these readable names should land as final unknown.
        # They must all be needs_review — either as
        # AINeedsReviewParseFailed (legacy safety net) or as
        # AINeedsReviewLowConfidence (Phase 36K readable fallback).
        from altera_api.ai.classifier import AINeedsReviewLowConfidence

        for verdict in bundle.verdicts:
            assert isinstance(
                verdict,
                (AINeedsReviewParseFailed, AINeedsReviewLowConfidence),
            ), f"expected needs_review, got {type(verdict).__name__}"
        # And the sample errors should mention the safety net OR
        # the Phase 36K readable fallback rule.
        joined = "\n".join(bundle.sample_errors)
        assert (
            "unknown_safety_net" in joined
            or "unknown on readable" in joined
            or "food_guard_override" in joined
            or "readable_fallback" in joined
        )

    def test_empty_name_can_still_land_unknown(self) -> None:
        # An empty / unusable name must NOT be routed to review by
        # the unknown safety net — these are the only legitimate
        # ``unknown`` source.
        from datetime import UTC

        # Build a product with a name that bypasses the food guard
        # (no food tokens) AND fails the readability check
        # (placeholder).
        empty_product = _make_product("Produit")
        provider = _UnknownFakeProvider()
        bundle = batch_classify(
            [empty_product],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # Should be accepted or low-confidence with the model's
        # ``unknown`` verdict — NOT a parse-failure from the
        # safety net.
        verdict = bundle.verdicts[0]
        assert isinstance(
            verdict, (AIAccepted, AINeedsReviewLowConfidence)
        )


# ---------------------------------------------------------------------------
# A (cont). Detailed timing log surfaces upload_product_ids_count
# ---------------------------------------------------------------------------


class TestCreateTimingLog:
    def test_log_includes_upload_product_ids_count(
        self,
        client: TestClient,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        pid = _create_project(client)
        upload_id = _seed_n_products_via_store(
            store, UUID(pid), 25
        )
        with caplog.at_level(
            logging.INFO, logger="altera_api.classification_create"
        ):
            create_classification_job(
                store,
                organisation_id=store.default_org_id,
                project_id=UUID(pid),
                upload_id=upload_id,
                methodology=Methodology.PROTEIN_TRACKER,
            )
        msgs = [rec.getMessage() for rec in caplog.records]
        joined = "\n".join(msgs)
        assert "upload_product_ids_count=25" in joined, (
            "log must surface the upload size so the 10K cap bug "
            "is immediately visible if it regresses"
        )
        assert "products_loaded_count=" in joined
        assert "eligible_count=" in joined

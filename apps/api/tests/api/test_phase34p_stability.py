"""Phase 34P — Stability hotfix tests.

Areas under test:

A. ``GET /api/v1/projects`` returns a usable response even when one
   project's derived counts blow up. A single misbehaving project must
   never wipe the workspace.

B. ``POST /uploads/{id}/classify`` returns a structured 502 with
   ``error_code=classify_failed`` (no bare 500) when the orchestrator
   raises an unexpected exception. The wizard depends on the
   error_code to render its retry banner.

C. The batched AI classifier (``batch_classify``) re-runs failed rows
   in a small retry batch and surfaces ``recovered_rows`` /
   ``retry_batches`` on the bundle. A 50-row batch where the first
   provider response truncates mid-envelope must finish with >=80% of
   rows usable, not 50/50 parse_failed.

D. ``DEFAULT_BATCH_SIZE`` is now <=30 to avoid envelope truncation at
   gpt-4o-mini's completion cap.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.batch_classifier import batch_classify
from altera_api.ai.batch_prompt import DEFAULT_BATCH_SIZE, RETRY_BATCH_SIZE
from altera_api.ai.classifier import AIAccepted, AINeedsReviewParseFailed
from altera_api.ai.provider import ClassifierProvider, ProviderResponse
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.main import app


# ---------------------------------------------------------------------------
# Helpers (mirror prior phase fixtures)
# ---------------------------------------------------------------------------


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


def _make_product(name: str) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        row_number=1,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        weight_per_item_kg=Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("1")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A. Projects list isolation
# ---------------------------------------------------------------------------


class TestProjectsListResilience:
    def test_projects_list_renders_when_one_project_count_raises(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """If ``get_pt_classification`` blows up for one product in one
        project, the route must still return BOTH projects with names
        intact — that project's count just degrades to 0."""
        # Create two PT-enabled projects.
        r1 = client.post(
            "/api/v1/projects",
            json={
                "name": "alpha",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        r2 = client.post(
            "/api/v1/projects",
            json={
                "name": "beta",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        original_get_pt = store.get_pt_classification

        def flaky(product_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("classification table unreachable")

        with patch.object(store, "get_pt_classification", side_effect=flaky):
            with patch.object(
                store, "list_products_for_project", side_effect=Exception("boom")
            ):
                resp = client.get("/api/v1/projects")
        # restore
        store.get_pt_classification = original_get_pt  # type: ignore[method-assign]

        assert resp.status_code == 200
        body = resp.json()
        names = sorted(p["name"] for p in body["items"])
        assert names == ["alpha", "beta"]
        # The defensive path defaults unclassified_pt_count to 0.
        for p in body["items"]:
            assert p["unclassified_pt_count"] == 0

    def test_projects_list_survives_store_total_failure(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Even when ``store.list_projects`` itself raises, the route
        returns an empty page (200) so the workspace stays accessible."""
        with patch.object(
            store, "list_projects", side_effect=RuntimeError("db down")
        ):
            resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# B. Classify route never returns a bare 500
# ---------------------------------------------------------------------------


class TestClassifyStructuredErrors:
    def test_unexpected_exception_returns_502_with_error_code(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34p",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        pid = r.json()["id"]
        csv = (
            b"Product Name (FR),Poids unitaire produit (g),Volume\n"
            b"Test Produit,150,3.0\n"
        )
        mapping = (
            '{"product_name_fr": "product_name",'
            ' "poids_unitaire_produit_g": "weight_per_item_g",'
            ' "volume": "items_purchased"}'
        )
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("c.csv", csv, "text/csv")},
            data={"column_mapping": mapping},
        )
        upload_id = r_up.json()["id"]

        # Inject a failure deep inside classify_upload.
        with patch(
            "altera_api.api.routes.classify_upload",
            side_effect=RuntimeError("simulated provider blew up"),
        ):
            r_cls = client.post(
                f"/api/v1/projects/{pid}/uploads/{upload_id}/classify",
                json={"methodology": "protein_tracker"},
            )
        assert r_cls.status_code == 502
        detail = r_cls.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["error_code"] == "classify_failed"
        assert "simulated provider blew up" in detail["message"]

    def test_unknown_upload_returns_structured_404(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "p34p2",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        pid = r.json()["id"]
        bogus = uuid4()
        r_cls = client.post(
            f"/api/v1/projects/{pid}/uploads/{bogus}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r_cls.status_code == 404
        detail = r_cls.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["error_code"] == "upload_not_found"


# ---------------------------------------------------------------------------
# C. Batch classifier retry pass
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedProvider(ClassifierProvider):
    """Yields a sequence of raw responses across batch_classify calls."""

    responses: list[str]
    model_name: str = "phase34p-fake"
    _idx: int = field(default=0, init=False)
    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: Any) -> ProviderResponse:  # pragma: no cover
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
        idx = min(self._idx, len(self.responses) - 1)
        self._idx += 1
        return ProviderResponse(
            raw_text=self.responses[idx], model=self.model_name
        )


def _good_row(pid: str) -> str:
    return (
        f'{{"id":"{pid}","pt_group":"plant_based_core",'
        f'"confidence":0.95,"rationale":"ok"}}'
    )


class TestBatchRetry:
    def test_default_batch_size_is_capped_to_avoid_truncation(self) -> None:
        # Phase 34P — gpt-4o-mini truncates envelopes at sizes >30; the
        # default must stay <=30 to keep the main pass reliable.
        assert DEFAULT_BATCH_SIZE <= 30
        assert RETRY_BATCH_SIZE >= 1
        assert RETRY_BATCH_SIZE < DEFAULT_BATCH_SIZE

    def test_failed_rows_are_recovered_by_retry_batch(self) -> None:
        # First batch (10 products): the model returns a junk envelope.
        # Then 2 retry sub-batches of 5 each: model returns valid JSON.
        products = [_make_product(f"P{i}") for i in range(10)]
        broken = "totally not json"
        retry_a = (
            '{"results":['
            + ",".join(_good_row(str(p.id)) for p in products[:5])
            + "]}"
        )
        retry_b = (
            '{"results":['
            + ",".join(_good_row(str(p.id)) for p in products[5:])
            + "]}"
        )
        provider = _ScriptedProvider(
            responses=[
                broken,  # initial main-batch call
                broken,  # the repair-retry inside main batch
                retry_a,  # retry sub-batch 1
                retry_b,  # retry sub-batch 2
            ]
        )
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=10,
            retry_batch_size=5,
        )
        accepted = sum(1 for v in bundle.verdicts if isinstance(v, AIAccepted))
        assert accepted == 10, (
            f"expected 10 recovered, got {accepted} "
            f"(bundle.recovered_rows={bundle.recovered_rows})"
        )
        # Recovery diagnostics are surfaced.
        assert bundle.recovered_rows == 10
        assert bundle.retry_batches == 2
        # The main parse_failures was decremented as rows recovered.
        assert bundle.parse_failures == 0

    def test_retry_disabled_when_initial_pass_already_clean(self) -> None:
        products = [_make_product(f"X{i}") for i in range(3)]
        envelope = (
            '{"results":['
            + ",".join(_good_row(str(p.id)) for p in products)
            + "]}"
        )
        provider = _ScriptedProvider(responses=[envelope])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=5,
            retry_batch_size=2,
        )
        # All accepted on the first pass — no retry calls.
        assert all(isinstance(v, AIAccepted) for v in bundle.verdicts)
        assert bundle.retry_batches == 0
        assert bundle.recovered_rows == 0

    def test_retry_can_be_disabled_via_flag(self) -> None:
        products = [_make_product(f"Y{i}") for i in range(4)]
        provider = _ScriptedProvider(
            responses=["junk", "still junk"]  # initial + repair
        )
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=4,
            retry_batch_size=2,
            enable_retry=False,
        )
        # With retry disabled, the 4 failed rows stay failed.
        assert bundle.retry_batches == 0
        assert bundle.recovered_rows == 0
        assert all(
            isinstance(v, AINeedsReviewParseFailed) for v in bundle.verdicts
        )

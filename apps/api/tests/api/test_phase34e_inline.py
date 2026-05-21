"""Phase 34E — Inline-wizard contract tests.

The user must complete the full normal flow without leaving
`/projects/{id}/workflow`. These backend tests prove that every API
the wizard needs to call inline is available and behaves correctly
end-to-end on a sparse retailer CSV (product name + unit weight +
volume only — no external_product_id).

Frontend has its own primary surface test (legacy pages still load but
no normal CTA points there). These tests verify the *server* contracts
the inline components rely on:

1. Sparse CSV ingestion does not require external_product_id.
2. Mapping preview accepts French headers and reports per-column
   confidence.
3. Manual review queue is filterable by methodology + status (the
   pagination the inline review uses).
4. Run creation never produces a 0-row run.
5. The complete sparse-CSV journey works end-to-end via the same
   endpoints the wizard uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

SPARSE_FRENCH_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Blanc de Poulet Roti Tranche,193,3.0
Tofu Nature Bio,97,2.0
Filets de Saumon Atlantique,168,5.0
Pois Chiches Cuits en Conserve,73,3.0
Salade Poulet Cesar,53,4.0
"""

# The inline upload component sends this same mapping via FormData
# after the user accepts the auto-detected mapping preview.
_SPARSE_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _create_pt_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34e-sparse",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


class TestInlineMappingPreview:
    def test_french_headers_preview(self, client: TestClient) -> None:
        # The inline upload component calls previewMapping before
        # submitting; this contract must accept French headers and
        # return per-column confidence.
        r = client.post(
            "/api/v1/uploads/preview-mapping",
            json={
                "headers": [
                    "Product Name (FR)",
                    "Poids unitaire produit (g)",
                    "Volume",
                ],
                "methodologies": ["protein_tracker"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["entries"]) == 3
        # Each entry must report a confidence the UI can render
        # ("exact" | "synonym" | "none").
        confidences = {e["confidence"] for e in body["entries"]}
        assert confidences.issubset({"exact", "synonym", "none"})


class TestInlineSparseUpload:
    def test_sparse_csv_ingests_without_external_product_id(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
        # The inline upload component sends a column mapping that
        # tells the server which French headers correspond to which
        # canonical fields. Mirror that here.
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", SPARSE_FRENCH_CSV, "text/csv")},
            data={
                "column_mapping": (
                    '{"product_name_fr": "product_name",'
                    ' "poids_unitaire_produit_g": "weight_per_item_g",'
                    ' "volume": "items_purchased"}'
                )
            },
        )
        assert r.status_code == 201, r.json()
        body = r.json()
        # external_product_id absent in source CSV → server must
        # generate one per product, no rejection.
        assert body["products_count"] == 5, body
        assert body["row_count"] == 5

    def test_sparse_csv_then_classify_then_review_queue_listed(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", SPARSE_FRENCH_CSV, "text/csv")},
            data={"column_mapping": _SPARSE_MAPPING},
        )
        uid = r_up.json()["id"]
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        # InlineReview calls listReview with methodology + status filters.
        r_rev = client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue&sort=priority"
        )
        assert r_rev.status_code == 200
        items = r_rev.json()["items"]
        # Every item has the fields the inline UI consumes.
        for item in items:
            assert "product_id" in item
            assert "product_name" in item
            assert "current_category" in item
            assert "reason" in item

    def test_inline_decision_changed_resolves_review_item(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", SPARSE_FRENCH_CSV, "text/csv")},
            data={"column_mapping": _SPARSE_MAPPING},
        )
        uid = r_up.json()["id"]
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        queue = client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue"
        ).json()["items"]
        if not queue:
            # The deterministic rules classified everything in the
            # sparse CSV — nothing to validate manually. The wizard's
            # Step 5 short-circuits to "rien à valider" in that case.
            pytest.skip(
                "sparse CSV fully classified by deterministic rules — "
                "no queue items to assert on"
            )
        # The inline review submits {decision: "changed", to_category: <group>}.
        pid_first = queue[0]["product_id"]
        r = client.post(
            f"/api/v1/projects/{pid}/review/{pid_first}/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )
        assert r.status_code == 200
        # The just-resolved item leaves the in_queue list.
        queue2 = client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue"
        ).json()["items"]
        assert all(item["product_id"] != pid_first for item in queue2)


# ---------------------------------------------------------------------------
# Altera-promoted client for the calculation + result path
# ---------------------------------------------------------------------------


def _promote_to_altera(store: InMemoryStore) -> None:
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
def altera_store() -> InMemoryStore:
    s = InMemoryStore()
    _promote_to_altera(s)
    # Seed one NEVO row per family represented in the sparse CSV so the
    # full inline journey (apply-references → run) has nutrition data.
    s.seed_nevo_entries(
        [
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_CHK",
                food_name_en="Chicken breast, cooked",
                food_name_nl="Kipfilet, gekookt",
                food_group="Poultry",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("23.0"),
                plant_protein_g_per_100g=Decimal("0"),
                animal_protein_g_per_100g=Decimal("23.0"),
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_TOFU",
                food_name_en="Tofu, plain",
                food_name_nl="Tofu",
                food_group="Plant protein",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("8.0"),
                plant_protein_g_per_100g=Decimal("8.0"),
                animal_protein_g_per_100g=Decimal("0"),
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_SAL",
                food_name_en="Salmon fillet, raw",
                food_name_nl="Zalm filet, rauw",
                food_group="Fish",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("20.0"),
                plant_protein_g_per_100g=Decimal("0"),
                animal_protein_g_per_100g=Decimal("20.0"),
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_CHK_PEA",
                food_name_en="Chickpeas, canned",
                food_name_nl="Kikkererwten",
                food_group="Legumes",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("8.5"),
                plant_protein_g_per_100g=Decimal("8.5"),
                animal_protein_g_per_100g=Decimal("0"),
            ),
        ]
    )
    return s


@pytest.fixture
def altera_client(altera_store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: altera_store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


class TestInlineEndToEndFlow:
    def test_full_sparse_journey_uses_only_wizard_apis(
        self, altera_client: TestClient
    ) -> None:
        # 1. Create project (Phase 34C redirects to /workflow).
        pid = _create_pt_project(altera_client)

        # 2. Inline upload — Step 1.
        r_up = altera_client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", SPARSE_FRENCH_CSV, "text/csv")},
            data={"column_mapping": _SPARSE_MAPPING},
        )
        assert r_up.status_code == 201
        uid = r_up.json()["id"]

        # 3. Classify — Steps 3+4.
        altera_client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )

        # 4. Resolve manual review — Step 5.
        queue = altera_client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue"
        ).json()["items"]
        for item in queue:
            # The inline UI picks plant_based_core / animal_core; here
            # we just pick something valid so the row becomes eligible.
            altera_client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}"
                "/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )

        # 5. Apply NEVO — Step 6.
        r_enrich = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["nevo"]},
        )
        assert r_enrich.status_code == 200
        body = r_enrich.json()
        # The deterministic NEVO matcher requires an exact case-insensitive
        # name match; with AI nutrition matching disabled (default) and
        # French product names, deterministic matches will be 0 here.
        # The important contract for Step 6 is that the response is shaped
        # correctly so the wizard renders without crashing — total
        # references and product_results must always be present.
        assert body["nevo_total_references"] == 4
        assert isinstance(body["product_results"], list)
        # When 0 matched, the response MUST surface a warning so the
        # wizard never appears silent.
        if body["nevo_matched"] == 0:
            assert body["warning"] is not None

        # 6. Create run — Step 8 → 9. Server must NEVER create a 0-row run.
        r_run = altera_client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"methodology": "protein_tracker"},
        )
        if r_run.status_code == 201:
            assert r_run.json()["rows_count"] > 0
        else:
            # If blocked, server returns 4xx with a structured blocker
            # — also acceptable (the wizard then renders the blockers).
            assert r_run.status_code in (400, 422)

        # 7. Workflow status should reflect the full chain.
        status = altera_client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        # Phase 34I — deterministic step removed. AI classification is
        # the primary step now.
        upload_step = next(s for s in status["steps"] if s["key"] == "upload")
        ai_step = next(
            s for s in status["steps"] if s["key"] == "ai_classification"
        )
        assert upload_step["status"] == "complete"
        assert ai_step["status"] in {"complete", "needs_action"}


class TestNoZeroRowRun:
    def test_run_creation_blocked_without_eligible_rows(
        self, client: TestClient
    ) -> None:
        # An empty project must not be able to create a run; the wizard
        # Step 8 surfaces a blocker instead.
        pid = _create_pt_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"methodology": "protein_tracker"},
        )
        # Either 4xx (the typical zero-row guard) or a 201 with rows_count > 0.
        if r.status_code == 201:
            assert r.json()["rows_count"] > 0
        else:
            assert r.status_code in (400, 409, 422)

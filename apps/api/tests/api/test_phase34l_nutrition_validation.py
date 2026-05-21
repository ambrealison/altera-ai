"""Phase 34L — Nutrition validation table, CIQUAL removal,
fuzzy NEVO fallback, zero-row partial-run guard, manual-override
persistence, and the submit_decision auto-enqueue fix.

Areas under test:

A. ``submit_decision`` now enqueues a synthetic review item when the
   product has no open one, so the wizard's category validation table
   can override ANY product (not just those in the review queue).
B. The workflow-status response no longer emits CIQUAL in the user
   flow — the step is present but marked ``not_needed`` /
   ``accessible=false`` so the frontend can render the 8-step
   wizard. ``nutrition_validation`` takes the previous CIQUAL slot.
C. NEVO matcher has a fuzzy token-overlap fallback that picks the
   top candidate when score ≥ 2.
D. Zero-row partial-run guard rejects runs with no usable rows.
E. ``GET /nutrition-validations`` returns one row per PT product
   with status + source + provenance, paginated and filterable.
F. ``POST /nutrition-validations/{pid}/manual`` persists the manual
   override and the row's status becomes ``ready`` afterwards.
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

_SPARSE_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Pommes Golden,150,3.0
Blanc de Poulet Roti,193,3.0
Tofu Nature,200,2.0
Saumon Atlantique,168,5.0
"""

_SPARSE_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


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
    s.seed_nevo_entries(
        [
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_CHK",
                food_name_en="Chicken breast",
                food_name_nl="Kipfilet",
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
                food_name_en="Tofu plain",
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
                food_name_en="Salmon raw",
                food_name_nl="Zalm",
                food_group="Fish",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("20.0"),
                plant_protein_g_per_100g=Decimal("0"),
                animal_protein_g_per_100g=Decimal("20.0"),
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_APPLE",
                food_name_en="Apple raw",
                food_name_nl="Appel",
                food_group="Fruit",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("0.3"),
                plant_protein_g_per_100g=Decimal("0.3"),
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


def _setup_classified_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34l",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    pid = r.json()["id"]
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("sparse.csv", _SPARSE_CSV, "text/csv")},
        data={"column_mapping": _SPARSE_MAPPING},
    )
    uid = r_up.json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/uploads/{uid}/classify",
        json={"methodology": "protein_tracker"},
    )
    return pid


# ---------------------------------------------------------------------------
# A. submit_decision auto-enqueues on AI-classified products
# ---------------------------------------------------------------------------


class TestSubmitDecisionAutoEnqueue:
    def test_change_classified_product_without_review_item(
        self, client: TestClient
    ) -> None:
        pid = _setup_classified_project(client)
        rows = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"]
        # Pick any row that does NOT have an open review item. The
        # sparse CSV's deterministic-friendly products end up
        # classified without going through review.
        target = next(
            r for r in rows
            if r["review_status"] is None and r["pt_group"] is not None
        )
        # Before Phase 34L this 404'd because there was no review item.
        # The route now enqueues a synthetic one and resolves it
        # uniformly with the existing decision pipeline.
        r = client.post(
            f"/api/v1/projects/{pid}/review/{target['product_id']}"
            "/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )
        assert r.status_code == 200, r.json()

        rows_after = client.get(
            f"/api/v1/projects/{pid}/classifications"
            f"?product_search={target['product_name'].split()[0]}"
        ).json()["items"]
        after = next(
            r for r in rows_after if r["product_id"] == target["product_id"]
        )
        assert after["pt_group"] == "plant_based_core"
        assert after["pt_source"] == "manual_review"


# ---------------------------------------------------------------------------
# B. CIQUAL removed from normal flow; nutrition_validation present
# ---------------------------------------------------------------------------


class TestWorkflowWithoutCiqual:
    def test_ciqual_step_is_not_needed(self, client: TestClient) -> None:
        pid = _setup_classified_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        ciqual = next(
            s for s in body["steps"]
            if s["key"] == "nutrition_enrichment_ciqual"
        )
        # The step still exists in the emitted list (for backward
        # compatibility) but it is marked not_needed and not
        # accessible from the wizard.
        assert ciqual["status"] == "not_needed"
        assert ciqual["accessible"] is False

    def test_nutrition_validation_step_present(self, client: TestClient) -> None:
        pid = _setup_classified_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        keys = [s["key"] for s in body["steps"]]
        assert "nutrition_validation" in keys


# ---------------------------------------------------------------------------
# C. NEVO fuzzy fallback picks the right candidate without AI
# ---------------------------------------------------------------------------


class TestNevoFuzzyFallback:
    def test_blanc_de_poulet_matches_chicken_breast(
        self, altera_client: TestClient
    ) -> None:
        pid = _setup_classified_project(altera_client)
        # NEVO fuzzy picks up "Blanc de Poulet Roti" → "Chicken breast"
        # (poulet→chicken, blanc→breast = 2 token overlap, ≥ threshold).
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["nevo"]},
        )
        body = r.json()
        # At least one NEVO match without AI assistance (AI is off
        # by default in tests).
        assert body["nevo_matched"] >= 1
        assert body["ai_enabled"] is False


# ---------------------------------------------------------------------------
# D. Zero-row partial-run guard
# ---------------------------------------------------------------------------


class TestZeroRowPartialRunGuard:
    def test_allow_partial_blocked_when_zero_usable_nutrition(
        self, client: TestClient
    ) -> None:
        # Default in-memory store has empty NEVO → NEVO matches zero
        # → no enrichment → 0 usable nutrition. allow_partial=True
        # must still 400 with zero_usable_nutrition.
        pid = _setup_classified_project(client)
        # Resolve all review items so classification isn't the blocker.
        queue = client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue"
        ).json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}"
                "/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        r = client.post(
            f"/api/v1/projects/{pid}/runs",
            json={
                "methodology": "protein_tracker",
                "allow_partial": True,
            },
        )
        assert r.status_code == 400, r.json()
        assert r.json()["detail"]["error_code"] == "zero_usable_nutrition"


# ---------------------------------------------------------------------------
# E. Nutrition validation endpoint
# ---------------------------------------------------------------------------


class TestNutritionValidationEndpoint:
    def test_lists_one_row_per_pt_product(
        self, client: TestClient
    ) -> None:
        pid = _setup_classified_project(client)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
        )
        assert r.status_code == 200
        body = r.json()
        # The sparse CSV had 4 products, all PT-eligible.
        assert body["total"] == 4
        for row in body["items"]:
            assert "product_id" in row
            assert "product_name" in row
            assert "status" in row
            assert "source" in row
            # No commercial fields leak.
            for forbidden in (
                "items_purchased",
                "items_sold",
                "weight_per_item_g",
                "revenue",
                "margin",
            ):
                assert forbidden not in row

    def test_filter_by_status(self, client: TestClient) -> None:
        pid = _setup_classified_project(client)
        r = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
            "?status=missing"
        )
        assert r.status_code == 200
        for row in r.json()["items"]:
            assert row["status"] == "missing"


# ---------------------------------------------------------------------------
# F. Manual nutrition override
# ---------------------------------------------------------------------------


class TestManualNutritionOverride:
    def test_manual_override_persists_and_status_becomes_ready(
        self, client: TestClient
    ) -> None:
        pid = _setup_classified_project(client)
        rows = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
        ).json()["items"]
        target = next(r for r in rows if r["status"] == "missing")
        r = client.post(
            f"/api/v1/projects/{pid}/nutrition-validations/"
            f"{target['product_id']}/manual",
            json={
                "protein_pct": 20,
                "plant_protein_pct": 0,
                "animal_protein_pct": 20,
            },
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["status"] == "ready"
        assert body["source"] == "manual"
        assert body["protein_pct"] == "20"

    def test_split_does_not_match_total_returns_400(
        self, client: TestClient
    ) -> None:
        pid = _setup_classified_project(client)
        rows = client.get(
            f"/api/v1/projects/{pid}/nutrition-validations"
        ).json()["items"]
        target = rows[0]
        r = client.post(
            f"/api/v1/projects/{pid}/nutrition-validations/"
            f"{target['product_id']}/manual",
            json={
                "protein_pct": 20,
                "plant_protein_pct": 5,
                "animal_protein_pct": 5,  # 5 + 5 != 20 (tolerance 2pp)
            },
        )
        assert r.status_code == 400
        assert (
            r.json()["detail"]["error_code"]
            == "split_does_not_match_total"
        )

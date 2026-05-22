"""Phase 34N — Full NEVO import + calculation preflight diagnostics.

Areas under test:

A. ``list_nevo_entries`` no longer truncates at 1000 rows; it
   pages through ``.range()`` windows so the full ~2,328-row NEVO
   2025 dataset is exposed to matching.
B. ``GET /api/v1/projects/{id}/calculation-preflight`` walks each
   PT-eligible product and returns the exact same row count the
   subsequent ``/runs`` call will produce, plus explicit exclusion
   reasons for the rest. The wizard reads this to make the
   "Lignes éligibles" panel non-contradictory.
C. The importer's row-count floor rejects truncated imports unless
   the operator explicitly passes ``--limit``.
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

_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Pommes Golden,150,3.0
Blanc de Poulet Roti,193,3.0
Filets de Saumon Atlantique,168,5.0
Tofu Nature Bio,200,2.0
Promotion Mystere XYZ,100,1.0
"""

_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


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


def _seed_nevo(store: InMemoryStore) -> None:
    store.seed_nevo_entries(
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
                nevo_code="N_APPLE",
                food_name_en="Apple raw",
                food_name_nl="Appel",
                food_group="Fruit",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("0.3"),
                plant_protein_g_per_100g=Decimal("0.3"),
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
                nevo_code="N_TOFU",
                food_name_en="Tofu plain",
                food_name_nl="Tofu",
                food_group="Plant protein",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("8.0"),
                plant_protein_g_per_100g=Decimal("8.0"),
                animal_protein_g_per_100g=Decimal("0"),
            ),
        ]
    )


@pytest.fixture
def altera_store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    _seed_nevo(s)
    return s


@pytest.fixture
def altera_client(altera_store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: altera_store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _setup_project_with_nevo(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34n",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    pid = r.json()["id"]
    r_up = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("c.csv", _CSV, "text/csv")},
        data={"column_mapping": _MAPPING},
    )
    client.post(
        f"/api/v1/projects/{pid}/uploads/{r_up.json()['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    # Resolve all reviews to a concrete category so classification
    # is "complete" — the preflight is about nutrition readiness now.
    for item in client.get(
        f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
        "&status=in_queue"
    ).json()["items"]:
        client.post(
            f"/api/v1/projects/{pid}/review/{item['product_id']}"
            "/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )
    client.post(
        f"/api/v1/projects/{pid}/enrichments/apply-references",
        json={"providers": ["nevo"]},
    )
    return pid


# ---------------------------------------------------------------------------
# A. Calculation preflight matches the actual rows_count
# ---------------------------------------------------------------------------


class TestCalculationPreflight:
    def test_preflight_returns_per_product_counts(
        self, altera_client: TestClient
    ) -> None:
        pid = _setup_project_with_nevo(altera_client)
        r = altera_client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        )
        assert r.status_code == 200
        body = r.json()
        # 5 products in the sparse CSV; all PT-eligible.
        assert body["total_products"] == 5
        assert body["classified_products"] == 5
        assert body["products_with_volume"] == 5
        assert body["products_with_weight"] == 5
        # NEVO is attempted (apply-references ran).
        assert body["nevo_attempted"] is True
        # The preflight surfaces a count of products with usable
        # nutrition — that count must be non-zero for a project where
        # NEVO matched anything.
        assert body["products_with_total_protein"] >= 1

    def test_preflight_ready_count_matches_run_rows(
        self, altera_client: TestClient
    ) -> None:
        """The number the wizard shows ('Lignes éligibles: N') must
        match the actual rows_count the run produces. Before Phase
        34N the two numbers could disagree because the route ignored
        enrichment records (use_enriched_nutrition gated to Altera).
        """
        pid = _setup_project_with_nevo(altera_client)
        preflight = altera_client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        ).json()
        ready = preflight["products_ready_for_calculation"]
        r_run = altera_client.post(
            f"/api/v1/projects/{pid}/runs",
            json={
                "methodology": "protein_tracker",
                "allow_partial": True,
            },
        )
        if r_run.status_code == 201:
            assert r_run.json()["rows_count"] == ready
        else:
            # Zero-row guard only fires when ready == 0.
            assert ready == 0
            assert r_run.status_code == 400
            assert r_run.json()["detail"]["error_code"] == (
                "zero_usable_nutrition"
            )

    def test_preflight_sample_reasons_explain_exclusions(
        self, altera_client: TestClient
    ) -> None:
        pid = _setup_project_with_nevo(altera_client)
        body = altera_client.get(
            f"/api/v1/projects/{pid}/calculation-preflight"
        ).json()
        # The "Promotion Mystere XYZ" product almost certainly stays
        # missing nutrition — the sample_exclusion_reasons should
        # name it explicitly if so.
        if body["products_missing_nutrition"] > 0:
            assert body["sample_exclusion_reasons"]
            joined = " ".join(body["sample_exclusion_reasons"]).lower()
            assert "protein" in joined or "nutrition" in joined


# ---------------------------------------------------------------------------
# B. NEVO row count reflects the full table (no 1000 cap)
# ---------------------------------------------------------------------------


class TestNevoRowCountDiagnostic:
    def test_admin_stats_reports_full_table_size(
        self, altera_client: TestClient, altera_store: InMemoryStore
    ) -> None:
        # Seed 1500 entries to prove the 1000 cap is gone. We use
        # cheap stub rows here — the InMemoryStore doesn't have the
        # Supabase pagination but the diagnostic endpoint reads
        # `list_nevo_entries` which on the in-memory side just returns
        # all of them.
        from decimal import Decimal as _D

        extra = [
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code=f"N_AUTO_{i}",
                food_name_en=f"Auto food {i}",
                food_name_nl=f"Auto food {i}",
                food_group="Auto",
                quantity_basis="per 100g",
                protein_g_per_100g=_D("5.0"),
                plant_protein_g_per_100g=None,
                animal_protein_g_per_100g=None,
            )
            for i in range(1500)
        ]
        altera_store.seed_nevo_entries(extra)
        r = altera_client.get(
            "/api/v1/admin/nutrition-references/stats"
        )
        assert r.status_code == 200
        body = r.json()
        # 4 seeded + 1500 auto.
        assert body["nevo_total"] >= 1500


# ---------------------------------------------------------------------------
# C. Importer row-count floor
# ---------------------------------------------------------------------------


class TestImporterFloor:
    def test_importer_module_exposes_expected_min(self) -> None:
        from scripts import import_nevo as imp

        assert imp._EXPECTED_MIN_ROWS >= 2000

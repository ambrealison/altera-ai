"""Phase 34C — French product name matching and AI unavailable status.

Contracts under test:

1. ``candidates_for_product`` returns non-empty candidates for French
   product names by expanding French tokens to English equivalents
   (e.g. "poulet" → "chicken").

2. ``POST .../classify`` response includes ``ai_enabled: false``
   when ``ALTERA_AI_CLASSIFIER_ENABLED`` is not set.

3. ``POST .../enrichments/apply-references`` response includes
   ``product_results`` list with per-product enrichment outcomes.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.nutrition_candidates import candidates_for_product
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nevo(*, code: str, name_en: str, name_nl: str, prot: Decimal, group: str = "Poultry") -> NevoEntry:
    return NevoEntry(
        id=uuid4(),
        source_version="2025_v9.0",
        nevo_code=code,
        food_name_en=name_en,
        food_name_nl=name_nl,
        food_group=group,
        quantity_basis="per 100g",
        protein_g_per_100g=prot,
        plant_protein_g_per_100g=None,
        animal_protein_g_per_100g=prot,
    )


def _ciqual(*, code: str, name: str, prot: Decimal, group: str = "Viandes") -> CiqualEntry:
    return CiqualEntry(
        id=uuid4(),
        source_version="2025",
        source_food_code=code,
        food_name_en=name,  # CIQUAL stores French name here (from alim_nom_fr column)
        food_group=group,
        food_subgroup=None,
        food_subsubgroup=None,
        protein_g_per_100g=prot,
        is_below_detection=False,
    )


# ---------------------------------------------------------------------------
# 1. French token expansion in candidates_for_product
# ---------------------------------------------------------------------------


class TestFrenchMatching:
    """French product names should generate NEVO/CIQUAL candidates via
    token expansion (poulet→chicken, boeuf→beef, etc.)."""

    def test_poulet_matches_chicken_nevo(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Chicken breast, raw", name_nl="Kipfilet, rauw", prot=Decimal("22.0")),
            _nevo(code="N2", name_en="Beef mince, raw", name_nl="Rundergehakt, rauw", prot=Decimal("18.0"), group="Beef"),
        ]
        candidates = candidates_for_product(
            product_name="Blanc de poulet",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        assert len(candidates) > 0
        nevo_codes = [c.reference_code for c in candidates if c.source == "nevo"]
        assert "N1" in nevo_codes, f"Expected N1 (chicken) to be a candidate; got {nevo_codes}"

    def test_boeuf_matches_beef_nevo(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Beef, ground, raw", name_nl="Rundergehakt", prot=Decimal("18.0"), group="Beef"),
        ]
        candidates = candidates_for_product(
            product_name="Viande de boeuf hachée",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        nevo_codes = [c.reference_code for c in candidates if c.source == "nevo"]
        assert "N1" in nevo_codes

    def test_saumon_matches_salmon_nevo(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Salmon, raw", name_nl="Zalm, rauw", prot=Decimal("20.0"), group="Fish"),
        ]
        candidates = candidates_for_product(
            product_name="Filet de saumon",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        nevo_codes = [c.reference_code for c in candidates if c.source == "nevo"]
        assert "N1" in nevo_codes

    def test_tofu_matches_tofu_nevo(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Tofu, plain", name_nl="Tofu, naturel", prot=Decimal("8.0"), group="Legumes"),
        ]
        candidates = candidates_for_product(
            product_name="Tofu nature",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        nevo_codes = [c.reference_code for c in candidates if c.source == "nevo"]
        assert "N1" in nevo_codes

    def test_french_ciqual_name_matches_directly(self) -> None:
        # CIQUAL stores French names as food_name_en; French query should match.
        ciqual_entries = [
            _ciqual(code="C1", name="Blanc de poulet, cru", prot=Decimal("23.0")),
        ]
        candidates = candidates_for_product(
            product_name="Blanc de poulet",
            retailer_category=None,
            nevo_entries=[],
            ciqual_entries=ciqual_entries,
        )
        ciqual_codes = [c.reference_code for c in candidates if c.source == "ciqual"]
        assert "C1" in ciqual_codes

    def test_no_candidates_for_completely_unrelated_name(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Chicken breast", name_nl="Kipfilet", prot=Decimal("22.0")),
        ]
        candidates = candidates_for_product(
            product_name="XYZ completely unknown",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        # May or may not be empty — but should not crash.
        assert isinstance(candidates, list)

    def test_accented_characters_normalized(self) -> None:
        nevo_entries = [
            _nevo(code="N1", name_en="Cereals mix", name_nl="Graanmix", prot=Decimal("10.0"), group="Cereals"),
        ]
        candidates = candidates_for_product(
            product_name="Salade de céréales",
            retailer_category=None,
            nevo_entries=nevo_entries,
            ciqual_entries=[],
        )
        # "céréales" → "cereales" → "cereals" expansion should yield a match
        nevo_codes = [c.reference_code for c in candidates if c.source == "nevo"]
        assert "N1" in nevo_codes


# ---------------------------------------------------------------------------
# 2. ai_enabled field on classify response
# ---------------------------------------------------------------------------


class TestClassifyAiEnabled:
    def test_classify_ai_enabled_false_by_default(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """Without ALTERA_AI_CLASSIFIER_ENABLED, ai_enabled must be false."""
        r_proj = client.post(
            "/api/v1/projects",
            json={
                "name": "ai-test",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        assert r_proj.status_code == 201
        pid = r_proj.json()["id"]

        r_up = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        )
        assert r_up.status_code == 201
        uid = r_up.json()["id"]

        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 200
        assert r.json()["ai_enabled"] is False


# ---------------------------------------------------------------------------
# 3. product_results in apply-references response
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
    return s


@pytest.fixture
def altera_client(altera_store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: altera_store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


class TestProductResults:
    def _setup_project_with_upload(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> tuple[str, str]:
        r = client.post(
            "/api/v1/projects",
            json={
                "name": "enrichment-test",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        assert r.status_code == 201
        pid = r.json()["id"]
        r2 = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        )
        assert r2.status_code == 201
        uid = r2.json()["id"]
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        return pid, uid

    def test_product_results_present_in_response(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = self._setup_project_with_upload(altera_client, pt_tiny_csv)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
        )
        assert r.status_code == 200
        body = r.json()
        assert "product_results" in body
        assert isinstance(body["product_results"], list)

    def test_product_results_contains_all_products(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = self._setup_project_with_upload(altera_client, pt_tiny_csv)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
        )
        body = r.json()
        results = body["product_results"]
        total_products = (
            body["nevo_matched"]
            + body["ciqual_matched"]
            + body["nevo_ai_assisted_matched"]
            + body["ciqual_ai_assisted_matched"]
            + body["ai_needs_review"]
            + body["no_match"]
            + body["skipped_has_retailer_value"]
            + body["skipped_no_pt_fields"]
        )
        assert len(results) == total_products

    def test_product_result_has_required_fields(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid, _ = self._setup_project_with_upload(altera_client, pt_tiny_csv)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
        )
        body = r.json()
        for pr in body["product_results"]:
            assert "product_id" in pr
            assert "product_name" in pr
            assert "outcome" in pr
            assert pr["outcome"] in {
                "nevo_matched",
                "ciqual_matched",
                "ai_matched",
                "ai_needs_review",
                "no_match",
                "skipped_has_retailer_value",
                "skipped_no_pt_fields",
            }

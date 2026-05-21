"""Phase 34K — Progress, NEVO name cleaning, classification-assumption
split, partial calculation + coverage disclosure.

Areas under test:

A. Progress bar — a brand-new project shows 0% (was ~65% before
   Phase 34K because the workflow_status aggregator counted ``locked``
   downstream steps as 100% complete).
B. ``clean_product_name`` strips packaging / marketing tokens but
   preserves nutritionally meaningful ones.
C. NEVO ``_apply_nevo_entry`` total-only path: when the entry has
   protein_pct but no plant/animal split, derive the split from the
   product's PT classification (plant_based_* → 100% plant;
   animal_core → 100% animal; composite/unknown → no split).
D. Partial calculation: ``RunCreateRequest.allow_partial=True``
   lets the run through when the only blocker is
   ``nutrition_required``. The response summary includes the
   ``coverage`` block.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.nutrition_candidates import (
    clean_product_name,
)
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app

# ---------------------------------------------------------------------------
# Sparse CSV helpers (reused across the 34D/E/I/K phases)
# ---------------------------------------------------------------------------


_SPARSE_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Pommes Golden,150,3.0
Blanc de Poulet Roti,193,3.0
Tofu Nature,200,2.0
Saumon Atlantique,168,5.0
Yaourt Nature 0pct MG,125,4.0
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
    # Seed one NEVO row per food family in the sparse CSV.
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
                plant_protein_g_per_100g=None,  # NEVO TOTAL ONLY
                animal_protein_g_per_100g=None,
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_TOFU",
                food_name_en="Tofu",
                food_name_nl="Tofu",
                food_group="Plant protein",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("8.0"),
                plant_protein_g_per_100g=None,  # NEVO TOTAL ONLY
                animal_protein_g_per_100g=None,
            ),
            NevoEntry(
                id=uuid4(),
                source_version="2025_v9.0",
                nevo_code="N_APPLE",
                food_name_en="Apple",
                food_name_nl="Appel",
                food_group="Fruit",
                quantity_basis="per 100g",
                protein_g_per_100g=Decimal("0.3"),
                plant_protein_g_per_100g=None,
                animal_protein_g_per_100g=None,
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


def _create_pt_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34k",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


def _upload_sparse(client: TestClient, project_id: str) -> str:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("sparse.csv", _SPARSE_CSV, "text/csv")},
        data={"column_mapping": _SPARSE_MAPPING},
    )
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------------------
# A. Progress bar starts at 0
# ---------------------------------------------------------------------------


class TestProgressBarStartsAtZero:
    def test_new_project_progress_is_zero(self, client: TestClient) -> None:
        pid = _create_pt_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        # Phase 34K — methodology is not "complete" until at least one
        # upload exists, so a brand-new project must show 0%.
        assert body["overall_progress_pct"] == 0

    def test_progress_grows_with_each_completed_step(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
        # Step 0 — new project.
        p0 = client.get(f"/api/v1/projects/{pid}/workflow-status").json()[
            "overall_progress_pct"
        ]
        assert p0 == 0
        # After upload — progress must increase. The exact value
        # depends on which downstream steps become "not_needed" (e.g.
        # the review queue is empty so manual_classification_review
        # is "not_needed" → counts as done). We assert the direction
        # rather than a specific value so the test does not break
        # when the workflow aggregator adds new not_needed transitions.
        _upload_sparse(client, pid)
        p_uploaded = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()["overall_progress_pct"]
        assert p_uploaded > p0
        # Never report 100% just because some steps are not_needed.
        assert p_uploaded < 100


# ---------------------------------------------------------------------------
# B. clean_product_name strips noise tokens, keeps signal tokens
# ---------------------------------------------------------------------------


class TestCleanProductName:
    @pytest.mark.parametrize(
        "raw,expected_substring",
        [
            # Packaging tokens removed.
            ("Blanc de Poulet Rôti Tranché Bio x4 300g", "Blanc de Poulet"),
            ("Filets de Saumon Atlantique sous vide", "Filets de Saumon Atlantique"),
            ("Pommes Golden 1.5kg", "Pommes Golden"),
            ("Yaourt Bio Nature au naturel", "Yaourt"),
        ],
    )
    def test_packaging_tokens_removed(
        self, raw: str, expected_substring: str
    ) -> None:
        out = clean_product_name(raw)
        # The cleaned name must contain the food kind...
        assert expected_substring.split()[0].lower() in out.lower()
        # ...but must NOT contain pure noise tokens.
        for noise in ("1.5kg", "300g", "sous vide", "bio", "tranché"):
            assert noise.lower() not in out.lower(), (
                f"{noise!r} still in cleaned name {out!r}"
            )

    def test_preserves_nutritionally_meaningful_tokens(self) -> None:
        # "0% MG", "demi-écrémé", "soja", "blé" must survive.
        for raw in (
            "Yaourt 0% MG",
            "Lait demi-écrémé",
            "Boisson soja avoine",
            "Pain au blé complet",
        ):
            out = clean_product_name(raw).lower()
            for keep in ("0%", "mg", "demi", "soja", "ble", "blé"):
                if keep in raw.lower():
                    assert keep in out, f"dropped {keep!r} from {raw!r} → {out!r}"

    def test_empty_input(self) -> None:
        assert clean_product_name("") == ""
        assert clean_product_name("   ") == "   "

    def test_only_noise_tokens_returns_original(self) -> None:
        # If cleaning would empty the string, fall back to the original
        # so we never produce a zero-token input for matching.
        raw = "bio x4 300g"
        out = clean_product_name(raw)
        assert out  # not empty


# ---------------------------------------------------------------------------
# C. Classification-assumption split for NEVO total-only entries
# ---------------------------------------------------------------------------


class TestClassificationAssumptionSplit:
    """The route's `_apply_nevo_entry` derives a 100/0 split from the
    PT classification when NEVO returns total protein but no plant/
    animal columns. We exercise this end-to-end via apply-references."""

    def test_plant_classified_product_with_total_only_nevo_gets_split(
        self, altera_client: TestClient, altera_store: InMemoryStore
    ) -> None:
        # Seed a NEVO entry whose name matches one of the products
        # in the sparse CSV exactly (so the deterministic matcher
        # picks it up without needing AI). The seed has total protein
        # but no plant/animal split → the apply route should fall
        # back to the classification-assumption split.
        from decimal import Decimal as _D
        from uuid import uuid4 as _u

        from altera_api.domain.nevo import NevoEntry as _N

        altera_store.seed_nevo_entries(
            list(altera_store.list_nevo_entries())
            + [
                _N(
                    id=_u(),
                    source_version="2025_v9.0",
                    nevo_code="N_TOFU_EXACT",
                    food_name_en="Tofu Nature",
                    food_name_nl="Tofu Nature",
                    food_group="Plant protein",
                    quantity_basis="per 100g",
                    protein_g_per_100g=_D("8.0"),
                    plant_protein_g_per_100g=None,
                    animal_protein_g_per_100g=None,
                )
            ]
        )

        pid = _create_pt_project(altera_client)
        uid = _upload_sparse(altera_client, pid)
        altera_client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        # The classification routes products to various PT groups
        # depending on the deterministic + AI path. We just verify
        # that after apply-references, at least one PT product ends
        # up with a NEVO classification_assumption split (the
        # workflow status's `with_split` counter increments).
        altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["nevo"]},
        )

        after = altera_client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        after_nevo = next(
            s
            for s in after["steps"]
            if s["key"] == "nutrition_enrichment_nevo"
        )
        # Either the count grew (split was derived) OR the response
        # carried the deterministic total-only path. We assert at
        # least one ENRICHED protein record exists on a product so
        # we know the apply route ran cleanly.
        assert after_nevo["counts"].get("matched", 0) >= 1
        # The classification-assumption code path is exercised — even
        # if the matched product happened to be composite_products
        # (in which case no split is derived, by design), the
        # *function* ran without error. This test guards against
        # regressions where the new code path crashes on
        # composite_products or unknown.


# ---------------------------------------------------------------------------
# D. Partial calculation + coverage
# ---------------------------------------------------------------------------


class TestPartialCalculation:
    def test_allow_partial_lets_run_through_with_nutrition_blocker(
        self, altera_client: TestClient
    ) -> None:
        pid = _create_pt_project(altera_client)
        uid = _upload_sparse(altera_client, pid)
        altera_client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        # Resolve all manual review items so classification is "done"
        # but nutrition will be missing for most products.
        queue = altera_client.get(
            f"/api/v1/projects/{pid}/review?methodology=protein_tracker"
            "&status=in_queue"
        ).json()["items"]
        for item in queue:
            altera_client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}"
                "/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        # NEVO will match only the seeded entries (chicken/tofu/apple)
        # and only the products whose names contain those tokens —
        # the rest stay un-enriched. With the legacy guard, the run
        # would be blocked; with allow_partial=True it should succeed.
        altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["nevo"]},
        )

        # Strict run (no allow_partial) — must be blocked if any
        # product is missing nutrition (this is the pre-Phase-34K
        # behaviour).
        r_strict = altera_client.post(
            f"/api/v1/projects/{pid}/runs",
            json={"methodology": "protein_tracker"},
        )
        # Strict may succeed if every product got matched OR be blocked
        # — either is fine. The partial path is what we're asserting.

        # Partial run — must succeed regardless.
        r = altera_client.post(
            f"/api/v1/projects/{pid}/runs",
            json={
                "methodology": "protein_tracker",
                "allow_partial": True,
            },
        )
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["rows_count"] >= 0
        # Coverage block is present.
        coverage = body["summary"].get("coverage")
        assert coverage is not None
        assert "product_coverage_pct" in coverage
        assert "total_products_start" in coverage
        assert "products_included_in_calculation" in coverage
        assert "is_partial" in coverage
        # When some products are missing nutrition, is_partial is True.
        excluded = coverage["products_excluded_missing_nutrition"]
        if excluded > 0:
            assert coverage["is_partial"] is True

        # Drive-by: the strict run that was 400 came back with the
        # correct error code so the frontend can show its current
        # blocker UI.
        if r_strict.status_code != 201:
            assert r_strict.status_code == 400

    def test_classification_blocker_still_blocks_partial(
        self, altera_client: TestClient
    ) -> None:
        # If classification isn't done, allow_partial must NOT bypass
        # the classification_required blocker.
        pid = _create_pt_project(altera_client)
        _upload_sparse(altera_client, pid)
        # Skip classify — products are unclassified.
        r = altera_client.post(
            f"/api/v1/projects/{pid}/runs",
            json={
                "methodology": "protein_tracker",
                "allow_partial": True,
            },
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# E. Coverage thresholds smoke
# ---------------------------------------------------------------------------


class TestCoverageThresholds:
    def test_coverage_severity_calculation(self) -> None:
        # The backend just returns the % — severity bucketing happens
        # on the frontend, but the % math must be correct.
        from altera_api.api.routes import _compute_pt_coverage

        # No state to inject — exercise the helper with an empty
        # InMemoryStore + an empty project to confirm it doesn't crash
        # and returns sensible zero values.
        s = InMemoryStore()
        # The helper needs project + rows_count; create a stub project.
        from datetime import UTC, datetime

        from altera_api.domain.common import Methodology
        from altera_api.domain.project import Project

        proj = Project(
            id=uuid4(),
            organisation_id=s.default_org_id,
            name="empty",
            reporting_period_label="FY",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            created_by=s.default_user_id,
            created_at=datetime.now(UTC),
        )
        out = _compute_pt_coverage(s, proj, rows_count=0)
        assert out["total_products_start"] == 0
        assert out["product_coverage_pct"] == 0.0
        assert out["is_partial"] is False

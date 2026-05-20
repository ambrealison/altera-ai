"""Phase 33I-AI — AI-assisted nutrition reference matching tests.

Coverage:

  Unit (matcher)
    - empty candidate list → no_match without calling the LLM
    - AI proposes a code NOT in the shortlist → rejected as no_match
    - high-confidence (>=0.85) AI NEVO match → decision=match
    - medium-confidence (0.60-0.85) → decision=needs_review
    - low-confidence (<0.60) → decision=no_match
    - non-JSON / malformed JSON → no_match (never crashes the pipeline)
    - product card scrubbing: commercial fields raise on attempt to add

  Candidates (deterministic shortlist)
    - tokenisation drops short/stopwords; "Chicken Breast Fillet" finds
      "Chicken breast"
    - retailer_category extends the token set for better recall

  Endpoint
    - AI disabled → no AI calls; deterministic-only flow still works
    - AI enabled but no candidates → no AI call attempted
    - AI high-confidence match for "Chicken Breast Fillet" → NEVO record
      with match_method="ai_assisted" + plant/animal split
    - Ingredient-style product ("Mixed Grain Salad") with AI shortlist
      → AI-assisted CIQUAL (CIQUAL has total only, no split)
    - Medium-confidence AI proposal → NEEDS_MANUAL_REVIEW record, value
      not applied to the calculation
    - Retailer protein_pct is never overwritten by AI
    - AI cannot introduce an out-of-candidate code (defends against
      hallucinated NEVO codes)

  Calculation
    - PT summary counts nevo_ai_assisted_count separately from
      nevo_enrichment_used_count's total
    - Coverage caveats disclose AI-assisted matching with "AI was used
      only to assist reference matching, not to generate values."
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.fakes import StaticFakeProvider
from altera_api.ai.nutrition_candidates import (
    NutritionCandidate,
    candidates_for_product,
)
from altera_api.ai.nutrition_matcher import (
    NUTRITION_PROMPT_VERSION,
    THRESHOLD_AUTO_APPLY,
    THRESHOLD_REVIEW,
    build_product_card,
    propose_match,
)
from altera_api.ai.policy import CommercialDataBlockError
from altera_api.api.orchestrator import PT_VERSIONS
from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.api.store_factory import get_store
from altera_api.calculation.protein_tracker import calculate_pt_run
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    Methodology,
    OrganisationType,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.enrichment.selection import (
    ResolvedProteinEnrichment,
    select_protein_enrichment,
)
from altera_api.main import app

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _nevo(code: str, en: str, prot: Decimal, *, plant=None, animal=None, group="") -> NevoEntry:
    return NevoEntry(
        id=uuid4(),
        source_version="2025_v9.0",
        nevo_code=code,
        food_name_nl=en,
        food_name_en=en,
        food_group=group,
        quantity_basis="per 100g",
        protein_g_per_100g=prot,
        plant_protein_g_per_100g=plant,
        animal_protein_g_per_100g=animal,
    )


def _ciqual(code: str, en: str, prot: Decimal, *, group="") -> CiqualEntry:
    return CiqualEntry(
        id=uuid4(),
        source_version="2025",
        source_food_code=code,
        food_name_en=en,
        food_group=group,
        food_subgroup=None,
        food_subsubgroup=None,
        protein_g_per_100g=prot,
        is_below_detection=False,
    )


def _ai_response(
    *,
    decision: str,
    source: str | None,
    code: str | None,
    name: str | None = None,
    confidence: float = 0.9,
    reason: str = "test",
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "target": "product",
            "source": source,
            "reference_code": code,
            "reference_name": name,
            "confidence": confidence,
            "reason": reason,
            "normalised_query": "test",
        }
    )


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    # Default: AI is OFF unless a specific test turns it on.
    monkeypatch.delenv("AI_NUTRITION_MATCHING_ENABLED", raising=False)
    monkeypatch.delenv("ALTERA_AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_NUTRITION_MODEL", raising=False)


@pytest.fixture
def seeded_store() -> InMemoryStore:
    s = InMemoryStore()
    # Promote bootstrap user to Altera so dev-auth resolves with
    # can_apply_enrichment=True.
    org_id = s.default_org_id
    user_id = s.default_user_id
    existing_org = s.organisations[org_id]
    s.organisations[org_id] = Organisation(
        id=org_id,
        name=existing_org.name,
        slug=existing_org.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing_org.created_at,
    )
    existing_user = s.users[user_id]
    s.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=existing_user.email,
            display_name=existing_user.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=existing_user.created_at,
        )
    )
    # Seed nutrition references. "Chicken breast" is named close enough
    # to "Chicken Breast Fillet" that the candidate generator finds it,
    # but not bit-identical so the deterministic exact-name match
    # misses (forcing the AI fallback path).
    s.seed_nevo_entries(
        [
            _nevo(
                "100",
                "Chicken breast",
                Decimal("23.2"),
                plant=Decimal("0"),
                animal=Decimal("23.2"),
                group="Vlees",
            ),
            _nevo("200", "Tofu firm", Decimal("12.5"), plant=Decimal("12.5"), animal=Decimal("0")),
        ]
    )
    s.seed_ciqual_entries(
        [
            _ciqual("C-1", "Mixed grain salad bowl", Decimal("7.5"), group="Salads"),
        ]
    )
    return s


@pytest.fixture
def client(seeded_store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: seeded_store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _create_pt_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "33I",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _add_pt_product(
    store: InMemoryStore,
    *,
    project_id: UUID,
    org_id: UUID,
    name: str,
    category: str | None = None,
    protein_pct: Decimal | None = None,
    pt_group: ProteinTrackerGroup = ProteinTrackerGroup.COMPOSITE_PRODUCTS,
) -> NormalizedProduct:
    p = NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=name,
        product_name=name,
        retailer_category=category,
        weight_per_item_kg=Decimal("1.0"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=protein_pct,
            protein_source=ProteinSource.LABEL,
        ),
        created_at=_NOW,
    )
    store.add_product(p)
    store.upsert_pt_classification(
        ProteinTrackerProductClassification(
            product_id=p.id,
            pt_group=pt_group,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="test",
            updated_at=_NOW,
        )
    )
    return p


# ---------------------------------------------------------------------------
# Candidate generation — deterministic shortlist
# ---------------------------------------------------------------------------


class TestCandidateGeneration:
    def test_finds_chicken_breast_for_chicken_breast_fillet(self) -> None:
        cands = candidates_for_product(
            product_name="Chicken Breast Fillet",
            retailer_category=None,
            nevo_entries=[
                _nevo("100", "Chicken breast", Decimal("23.2")),
                _nevo("999", "Aardappel rauw", Decimal("2")),
            ],
            ciqual_entries=[],
        )
        codes = [c.reference_code for c in cands]
        assert "100" in codes
        assert "999" not in codes

    def test_empty_when_no_token_overlap(self) -> None:
        cands = candidates_for_product(
            product_name="Quantum cheese widget",
            retailer_category=None,
            nevo_entries=[_nevo("100", "Chicken breast", Decimal("23"))],
            ciqual_entries=[],
        )
        assert cands == []

    def test_retailer_category_adds_recall(self) -> None:
        # Product name alone shares no significant token with "chicken
        # breast"; the category supplies the bridge.
        cands = candidates_for_product(
            product_name="Fillet pack",
            retailer_category="Poultry chicken",
            nevo_entries=[_nevo("100", "Chicken breast", Decimal("23"))],
            ciqual_entries=[],
        )
        assert any(c.reference_code == "100" for c in cands)


# ---------------------------------------------------------------------------
# Matcher — unit tests, never makes real HTTP calls
# ---------------------------------------------------------------------------


class TestMatcherUnit:
    def _card(self) -> dict:
        return build_product_card(
            product_name="Chicken Breast Fillet",
            brand="AcmeFarm",
            retailer_category="Poultry",
            retailer_subcategory=None,
            ingredients_text=None,
        )

    def _cands(self) -> list[NutritionCandidate]:
        return [
            NutritionCandidate(
                source="nevo",
                reference_code="100",
                name="Chicken breast",
                food_group="Vlees",
            )
        ]

    def test_empty_candidates_does_not_call_provider(self) -> None:
        called: list[int] = []

        class Spy(StaticFakeProvider):
            def classify(self, prompt):  # type: ignore[override]
                called.append(1)
                return super().classify(prompt)

        provider = Spy(raw_text="ignored")
        p = propose_match(product_card=self._card(), candidates=[], provider=provider)
        assert p.decision == "no_match"
        assert called == []

    def test_high_confidence_match(self) -> None:
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                name="Chicken breast",
                confidence=0.91,
            )
        )
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "match"
        assert p.source == "nevo"
        assert p.reference_code == "100"
        assert p.confidence == pytest.approx(0.91)
        assert p.prompt_version == NUTRITION_PROMPT_VERSION

    def test_medium_confidence_needs_review(self) -> None:
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                confidence=0.72,  # between REVIEW (0.60) and AUTO (0.85)
            )
        )
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "needs_review"
        assert THRESHOLD_REVIEW <= p.confidence < THRESHOLD_AUTO_APPLY

    def test_low_confidence_no_match(self) -> None:
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                confidence=0.3,
            )
        )
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "no_match"

    def test_code_outside_shortlist_rejected(self) -> None:
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="HALLUCINATED-999",
                confidence=0.99,
            )
        )
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "no_match"
        assert "outside candidate list" in p.reason

    def test_malformed_json_falls_through(self) -> None:
        provider = StaticFakeProvider(raw_text="not json at all")
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "no_match"

    def test_explicit_no_match_decision_preserved(self) -> None:
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="no_match",
                source=None,
                code=None,
                confidence=0.95,
                reason="genuinely unknown",
            )
        )
        p = propose_match(product_card=self._card(), candidates=self._cands(), provider=provider)
        assert p.decision == "no_match"

    def test_product_card_rejects_commercial_fields(self) -> None:
        # build_product_card only emits allow-listed fields, so it
        # cannot construct a forbidden payload from kwargs. But any
        # future code that builds a dict by hand and calls into the
        # matcher must trip assert_payload_allowed. Smoke that path.
        bad_card = {
            "product_name": "Chicken",
            "items_purchased": 100,  # commercial → must raise
        }
        provider = StaticFakeProvider(raw_text=_ai_response(decision="no_match", source=None, code=None))
        with pytest.raises(CommercialDataBlockError):
            propose_match(
                product_card=bad_card,
                candidates=self._cands(),
                provider=provider,
            )


# ---------------------------------------------------------------------------
# Endpoint — apply-references with AI fallback
# ---------------------------------------------------------------------------


def _enable_mock_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_NUTRITION_MATCHING_ENABLED", "true")
    monkeypatch.setenv("ALTERA_AI_PROVIDER", "mock")


def _override_ai(provider) -> None:
    """Force ``get_nutrition_ai_provider()`` to return ``provider``.

    The route imports the function inside the handler body, so we patch
    at the module level rather than via dependency injection.
    """
    from altera_api.ai import config as ai_config

    ai_config.get_nutrition_ai_provider = lambda: provider  # type: ignore[assignment]


def _restore_ai() -> None:
    import importlib

    from altera_api.ai import config as ai_config

    importlib.reload(ai_config)


class TestEndpointAIDisabled:
    def test_no_ai_when_flag_off(
        self, client: TestClient, seeded_store: InMemoryStore
    ) -> None:
        org_id = seeded_store.default_org_id
        pid_str = _create_pt_project(client)
        pid = UUID(pid_str)
        _add_pt_product(
            seeded_store,
            project_id=pid,
            org_id=org_id,
            name="Chicken Breast Fillet",
            category="Poultry",
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ai_enabled"] is False
        assert body["nevo_ai_assisted_matched"] == 0
        assert body["nevo_matched"] == 0  # deterministic exact-match also misses
        assert body["no_match"] == 1

    def test_openai_provider_without_key_falls_back(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Flag is on but no API key → factory returns None silently.
        monkeypatch.setenv("AI_NUTRITION_MATCHING_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        org_id = seeded_store.default_org_id
        pid_str = _create_pt_project(client)
        pid = UUID(pid_str)
        _add_pt_product(
            seeded_store,
            project_id=pid,
            org_id=org_id,
            name="Chicken Breast Fillet",
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        assert r.status_code == 200
        body = r.json()
        assert body["ai_enabled"] is False


class TestEndpointAIHighConfidence:
    def test_chicken_breast_fillet_routes_to_ai_nevo(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_mock_ai(monkeypatch)
        # Inject a fake provider that picks NEVO code 100 with high
        # confidence — bypasses real OpenAI.
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                name="Chicken breast",
                confidence=0.92,
            ),
            model_name="fake-nutrition-v1",
        )
        _override_ai(provider)
        try:
            org_id = seeded_store.default_org_id
            pid_str = _create_pt_project(client)
            pid = UUID(pid_str)
            product = _add_pt_product(
                seeded_store,
                project_id=pid,
                org_id=org_id,
                name="Chicken Breast Fillet",
                category="Poultry",
            )
            r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ai_enabled"] is True
            assert body["ai_model"] == "fake-nutrition-v1"
            assert body["nevo_ai_assisted_matched"] == 1
            assert body["nevo_ai_assisted_with_split"] == 1
            assert body["nevo_matched"] == 0  # deterministic missed
            records = seeded_store.get_enrichment_records_for_product(product.id)
            # protein_pct + plant_protein_pct + animal_protein_pct
            assert len(records) == 3
            for rec in records:
                assert rec.source is NutritionEnrichmentSource.NEVO
                assert rec.match_method == "ai_assisted"
                assert rec.status is NutritionEnrichmentStatus.ENRICHED
                # AI never provides nutrition values — verify the
                # stored numbers match the NEVO row, not anything the
                # AI returned (it returned no values).
                if rec.nutrient == "protein_pct":
                    assert rec.enriched_value == Decimal("23.2")
                elif rec.nutrient == "animal_protein_pct":
                    assert rec.enriched_value == Decimal("23.2")
        finally:
            _restore_ai()

    def test_ai_proposed_out_of_shortlist_code_falls_to_no_match(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_mock_ai(monkeypatch)
        # AI returns a hallucinated NEVO code → matcher rejects, route
        # falls through to no_match.
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="HALLUCINATED-9999",
                confidence=0.99,
            )
        )
        _override_ai(provider)
        try:
            org_id = seeded_store.default_org_id
            pid_str = _create_pt_project(client)
            pid = UUID(pid_str)
            _add_pt_product(
                seeded_store,
                project_id=pid,
                org_id=org_id,
                name="Chicken Breast Fillet",
            )
            r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
            assert r.status_code == 200
            body = r.json()
            assert body["nevo_ai_assisted_matched"] == 0
            assert body["no_match"] == 1
        finally:
            _restore_ai()

    def test_mixed_grain_salad_picks_ai_ciqual(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_mock_ai(monkeypatch)
        # AI picks CIQUAL — total only, no plant/animal split.
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="ciqual",
                code="C-1",
                name="Mixed grain salad bowl",
                confidence=0.88,
            )
        )
        _override_ai(provider)
        try:
            org_id = seeded_store.default_org_id
            pid_str = _create_pt_project(client)
            pid = UUID(pid_str)
            product = _add_pt_product(
                seeded_store,
                project_id=pid,
                org_id=org_id,
                name="Mixed Grain Salad",
            )
            r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
            assert r.status_code == 200
            body = r.json()
            assert body["ciqual_ai_assisted_matched"] == 1
            records = seeded_store.get_enrichment_records_for_product(product.id)
            assert len(records) == 1  # CIQUAL has no plant/animal split
            assert records[0].source is NutritionEnrichmentSource.CIQUAL
            assert records[0].match_method == "ai_assisted"
            assert records[0].enriched_value == Decimal("7.5")
        finally:
            _restore_ai()


class TestEndpointAIMediumConfidence:
    def test_medium_confidence_creates_needs_review_record(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_mock_ai(monkeypatch)
        provider = StaticFakeProvider(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                confidence=0.7,  # in review band
            )
        )
        _override_ai(provider)
        try:
            org_id = seeded_store.default_org_id
            pid_str = _create_pt_project(client)
            pid = UUID(pid_str)
            product = _add_pt_product(
                seeded_store,
                project_id=pid,
                org_id=org_id,
                name="Chicken Breast Fillet",
            )
            r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
            body = r.json()
            assert body["ai_needs_review"] == 1
            assert body["nevo_ai_assisted_matched"] == 0
            records = seeded_store.get_enrichment_records_for_product(product.id)
            assert len(records) == 1
            rec = records[0]
            assert rec.status is NutritionEnrichmentStatus.NEEDS_MANUAL_REVIEW
            assert rec.match_method == "ai_assisted"
            # CRITICAL: AI never provides a value; the record exists for
            # reviewer attention but the value field is None so the
            # calculation skips it.
            assert rec.enriched_value is None
        finally:
            _restore_ai()


class TestRetailerNutritionNeverOverwritten:
    def test_ai_skipped_for_products_with_retailer_protein(
        self,
        client: TestClient,
        seeded_store: InMemoryStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_mock_ai(monkeypatch)
        called: list[int] = []

        class Spy(StaticFakeProvider):
            def classify(self, prompt):  # type: ignore[override]
                called.append(1)
                return super().classify(prompt)

        provider = Spy(
            raw_text=_ai_response(
                decision="match",
                source="nevo",
                code="100",
                confidence=0.99,
            )
        )
        _override_ai(provider)
        try:
            org_id = seeded_store.default_org_id
            pid_str = _create_pt_project(client)
            pid = UUID(pid_str)
            _add_pt_product(
                seeded_store,
                project_id=pid,
                org_id=org_id,
                name="Chicken Breast Fillet",
                protein_pct=Decimal("18.0"),  # retailer-provided
            )
            r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
            body = r.json()
            assert body["skipped_has_retailer_value"] == 1
            assert body["nevo_ai_assisted_matched"] == 0
            assert called == []  # AI never invoked when retailer values exist
        finally:
            _restore_ai()


# ---------------------------------------------------------------------------
# Calculation — counts AI-assisted separately + report disclosure
# ---------------------------------------------------------------------------


class TestCalculationAndDisclosure:
    def test_summary_counts_ai_assisted_separately(self) -> None:
        product = NormalizedProduct(
            id=uuid4(),
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            row_number=1,
            external_product_id="x",
            product_name="x",
            weight_per_item_kg=Decimal("1.0"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(
                items_purchased=Decimal("100"),
                protein_pct=None,
                protein_source=ProteinSource.LABEL,
            ),
            created_at=_NOW,
        )
        clf = ProteinTrackerProductClassification(
            product_id=product.id,
            pt_group=ProteinTrackerGroup.ANIMAL_CORE,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="r",
            updated_at=_NOW,
        )
        lookup = {
            product.id: ResolvedProteinEnrichment(
                protein_pct=Decimal("23.2"),
                source=NutritionEnrichmentSource.NEVO,
                plant_protein_pct=Decimal("0"),
                animal_protein_pct=Decimal("23.2"),
                match_method="ai_assisted",
            )
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="FY24",
            versions=PT_VERSIONS,
            enrichment_lookup=lookup,
        )
        s = result.summary
        assert s.nevo_enrichment_used_count == 1
        assert s.nevo_ai_assisted_count == 1
        assert s.ciqual_ai_assisted_count == 0

    def test_coverage_caveats_disclose_ai_assistance(self) -> None:
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        # Reuse default org (Demo) — fine because we only need the
        # caveats string, not RLS.
        org_id = store.default_org_id
        project = store.create_project(
            name="33I-Caveats",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="FY 2024",
            organisation_id=org_id,
        )
        # Build a run with PT summary that has AI-assisted counters > 0.
        from altera_api.domain.protein_tracker import (
            ProteinTrackerCalculationSummary,
        )

        run_id = uuid4()
        summary = ProteinTrackerCalculationSummary(
            run_id=run_id,
            reporting_period_label="FY 2024",
            per_group=(),
            plant_protein_kg=Decimal("0"),
            animal_protein_kg=Decimal("23.2"),
            total_in_scope_protein_kg=Decimal("23.2"),
            plant_share_pct=Decimal("0"),
            animal_share_pct=Decimal("100"),
            rows_with_per_product_split=1,
            rows_protein_source_label=0,
            rows_protein_source_reference_db=0,
            out_of_scope_count=0,
            unknown_count=0,
            use_enriched_nutrition=True,
            enriched_nutrition_used_count=1,
            nevo_enrichment_used_count=1,
            nevo_ai_assisted_count=1,
            rows_with_enriched_split=1,
            methodology_version="v1",
            methodology_source_edition="edA",
            taxonomy_version="v1",
            rules_version="v1",
        )
        run = RunRecord(
            id=run_id,
            project_id=project.id,
            organisation_id=org_id,
            methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW,
            finished_at=_NOW,
            triggered_by=uuid4(),
            rows_payload=[],
            summary_payload=summary.model_dump(),
            rows_count=1,
        )
        section = build_coverage_section(store, run, project)
        joined = " ".join(section.caveats)
        assert "AI assistance" in joined or "AI was used only to assist" in joined
        assert "AI" in joined  # at minimum
        assert "1 reference" in joined  # the AI-assisted disclosure line


class TestSelectionPreservesMatchMethod:
    def test_select_carries_ai_assisted_through(self) -> None:
        from altera_api.domain.enrichment import (
            NutritionEnrichmentRecord,
        )

        pid = uuid4()
        records = [
            NutritionEnrichmentRecord(
                product_id=pid,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("23.2"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.NEVO,
                confidence=Decimal("0.91"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="ai test",
                created_at=_NOW,
                created_by=None,
                match_method="ai_assisted",
            ),
        ]
        resolved = select_protein_enrichment(records)
        assert resolved is not None
        assert resolved.match_method == "ai_assisted"

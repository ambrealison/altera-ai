"""Phase 36J — guard metrics + audit script + audit fixture.

This module verifies three independent surfaces of Phase 36I's
guard machinery (see ``altera_api/ai/pt_guards.py``):

  A. ``BatchVerdictBundle`` carries ``guard_overrides_by_rule`` and
     ``unknown_safety_net_total`` so the orchestrator can persist or
     log them per advance batch.

  B. The orchestrator's ``classify.advance.timing`` log line now
     surfaces ``guard_overrides_total``, ``guard_overrides_by_rule``,
     and ``unknown_safety_net_total``.

  C. The audit script (``scripts/evaluate_pt_classification.py``)
     runs end-to-end against the bundled batch-150 fixture and lands
     at 100% accuracy with the expected rule mix.

Out of scope (per the brief):
  * Persisting guard counters on the ``ClassificationJob`` row
    (would require an InMemoryStore + Postgres migration; the
    advance log + bundle is enough for operator observability).
  * Surfacing guard reasons in the wizard review item rows (current
    sample_errors already carry the rule id).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.batch_classifier import BatchVerdictBundle, batch_classify
from altera_api.ai.classifier import AIAccepted, AINeedsReviewLowConfidence
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.api.classification_job_orchestrator import (
    advance_classification_job,
    create_classification_job,
)
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.main import app

_API_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _API_ROOT
    / "altera_api"
    / "data"
    / "audit"
    / "pt_batch_150_fixture.json"
)
_SCRIPT_PATH = _API_ROOT / "scripts" / "evaluate_pt_classification.py"


# ---------------------------------------------------------------------------
# Fixtures
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


def _make_product(name: str, project_id: Any, upload_id: Any) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        project_id=project_id,
        upload_id=upload_id,
        organisation_id=uuid4(),
        row_number=2,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("2.0")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


class _GuardTriggerProvider(ClassifierProvider):
    """Fake provider that returns hand-picked categories matching the
    Phase 36I guard families, so every category arrives at the guard
    layer in a state that fires it.

    Mapping:
      - "coulis"  → plant_based_core (plant_core_demoted guard)
      - "thé"      → plant_based_non_core (beverage_out_of_scope)
      - "smoothie" → unknown (fruit_drink_non_core)
      - "sablés"   → plant_based_non_core (bakery_composite)
      - "cassoulet" → animal_core (animal_prepared_meal_composite)
    """

    @property
    def model(self) -> str:
        return "phase36j-trigger"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError("batch only")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        rows: list[dict[str, Any]] = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row or "product_name" not in row:
                continue
            name = str(row["product_name"]).lower()
            if "coulis" in name:
                cat, conf = "plant_based_core", 0.95
            elif "thé" in name or "the corse" in name:
                cat, conf = "plant_based_non_core", 0.9
            elif "smoothie" in name:
                cat, conf = "unknown", 0.5
            elif "sablés" in name or "sables" in name:
                cat, conf = "plant_based_non_core", 0.9
            elif "cassoulet" in name:
                cat, conf = "animal_core", 0.9
            else:
                cat, conf = "plant_based_non_core", 0.9
            rows.append(
                {
                    "id": row["id"],
                    "pt_group": cat,
                    "confidence": conf,
                    "rationale": "phase36j fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="phase36j-trigger",
        )


# ---------------------------------------------------------------------------
# A. Bundle carries guard counters
# ---------------------------------------------------------------------------


class TestBundleGuardCounters:
    def test_bundle_exposes_guard_counters(self) -> None:
        provider = _GuardTriggerProvider()
        # One product per guard rule, plus one that triggers no guard.
        products = [
            _make_product("Coulis Mangue", uuid4(), uuid4()),
            _make_product("Thé Corsé", uuid4(), uuid4()),
            _make_product("Smoothie Pêche", uuid4(), uuid4()),
            _make_product("Sablés Noisette", uuid4(), uuid4()),
            _make_product("Cassoulet Provençale", uuid4(), uuid4()),
            _make_product("Tofu Nature Bio", uuid4(), uuid4()),  # no guard
        ]
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert isinstance(bundle, BatchVerdictBundle)
        # Phase 36K — model ``unknown`` on a "smoothie" name is now
        # caught by the readable_fallback_fruit_drink rule (the
        # Phase 36K early-fallback path) instead of the Phase 36I
        # fruit_drink_non_core guard. Final category is identical;
        # only the rule id changed.
        assert bundle.guard_overrides_by_rule == {
            "plant_core_demoted_preparation_or_simple_veg": 1,
            "beverage_out_of_scope": 1,
            "readable_fallback_fruit_drink": 1,
            "bakery_composite": 1,
            "animal_prepared_meal_composite": 1,
        }
        # No unknown-safety-net firings here (the smoothie's
        # unknown is consumed by the fruit_drink_non_core guard
        # BEFORE the safety net runs, because we route unknowns
        # through the guard branch in that flow).
        assert bundle.unknown_safety_net_total == 0
        # Guard-corrected rows are clamped to ≤0.69 confidence so
        # they route to needs_review_low_confidence rather than
        # AIAccepted.
        n_low = sum(
            1
            for v in bundle.verdicts
            if isinstance(v, AINeedsReviewLowConfidence)
        )
        assert n_low >= 5  # five guard firings ⇒ five review-required

    def test_unknown_safety_net_counts_when_no_guard_applies(self) -> None:
        """A readable name that the model labels ``unknown`` but
        which does NOT match any Phase 36I guard pattern AND no
        Phase 36K readable-fallback rule fires (e.g. a brand-only
        name like "Marque Bleue Sélection") increments
        ``unknown_safety_net_total`` and routes to needs_review."""

        class _UnknownEverywhere(ClassifierProvider):
            @property
            def model(self) -> str:
                return "phase36j-unknown"

            def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
                raise NotImplementedError("batch only")

            def supports_batch(self) -> bool:
                return True

            def batch_classify(self, prompt: Any) -> ProviderResponse:
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
                            "confidence": 0.3,
                            "rationale": "phase36j fake",
                        }
                    )
                return ProviderResponse(
                    raw_text=json.dumps({"results": rows}),
                    model="phase36j-unknown",
                )

        provider = _UnknownEverywhere()
        products = [
            # No food / non-food / beverage / bakery token AND no
            # food-token substring (avoiding the food-guard branch).
            # The readable fallback can't pick a category, so the
            # legacy unknown_safety_net path fires.
            _make_product("Promotion Premium", uuid4(), uuid4()),
        ]
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert bundle.unknown_safety_net_total == 1


# ---------------------------------------------------------------------------
# B. Advance log surfaces guard counters
# ---------------------------------------------------------------------------


class TestAdvanceLogIncludesGuards:
    def test_advance_log_includes_guard_breakdown(
        self,
        store: InMemoryStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Calls ``advance_classification_job`` directly to bypass
        the route layer's auth — the point is to verify the log line
        shape, not the HTTP wiring."""

        # Replace the AI provider with one that always returns
        # plant_based_core 0.95 — for products with names matching
        # the plant_core demotion pattern, the guard fires on every
        # row.
        class _AlwaysPlantCore(ClassifierProvider):
            @property
            def model(self) -> str:
                return "phase36j-pc"

            def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
                raise NotImplementedError

            def supports_batch(self) -> bool:
                return True

            def batch_classify(self, prompt: Any) -> ProviderResponse:
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
                            "pt_group": "plant_based_core",
                            "confidence": 0.95,
                            "rationale": "phase36j fake",
                        }
                    )
                return ProviderResponse(
                    raw_text=json.dumps({"results": rows}),
                    model="phase36j-pc",
                )

        provider = _AlwaysPlantCore()

        # Seed a project + upload + 3 products whose names all fire
        # the plant_core_demoted guard (coulis pattern).
        project = store.create_project(
            name="phase36j",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="FY 2024",
            organisation_id=store.default_org_id,
            created_by=store.default_user_id,
        )
        project_id = project.id
        upload_id = uuid4()
        products: list[NormalizedProduct] = []
        for name in [
            "Coulis Mangue",
            "Coulis Figue",
            "Velouté Tomate",
        ]:
            p = _make_product(name, project_id, upload_id)
            p = p.model_copy(
                update={"organisation_id": store.default_org_id}
            )
            products.append(p)
        store.add_products_bulk(products)
        upload = Upload(
            id=upload_id,
            project_id=project_id,
            organisation_id=store.default_org_id,
            storage_path=f"uploads/{upload_id}.csv",
            original_filename="seeded.csv",
            status=UploadStatus.VALID,
            content_type="text/csv",
            file_size_bytes=1024,
            uploaded_by=store.default_user_id,
            created_at=datetime.now(UTC),
            row_count=len(products),
        )
        store.add_upload(upload, [p.id for p in products])

        # Create the job + advance.
        job = create_classification_job(
            store,
            organisation_id=store.default_org_id,
            project_id=project_id,
            upload_id=upload_id,
            methodology=Methodology.PROTEIN_TRACKER,
        )

        with caplog.at_level(
            logging.INFO,
            logger="altera_api.classification_advance",
        ):
            advance_classification_job(
                store, job.id, ai_provider=provider
            )

        msgs = [rec.getMessage() for rec in caplog.records]
        joined = "\n".join(msgs)
        assert any("classify.advance.timing" in m for m in msgs)
        assert "guard_overrides_total=3" in joined
        assert (
            "plant_core_demoted_preparation_or_simple_veg=3" in joined
        )
        assert "unknown_safety_net_total=0" in joined


# ---------------------------------------------------------------------------
# C. Audit script + fixture
# ---------------------------------------------------------------------------


class TestAuditFixture:
    def test_fixture_file_is_well_formed(self) -> None:
        assert _FIXTURE_PATH.exists(), (
            f"fixture missing at {_FIXTURE_PATH}"
        )
        data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        assert "cases" in data and isinstance(data["cases"], list)
        assert data["cases"], "fixture must not be empty"
        required = {
            "product_name",
            "expected_pt_group",
            "expected_review_required",
            "expected_guard_rule",
            "model_pt_group",
        }
        for case in data["cases"]:
            assert required.issubset(case), (
                f"missing keys in case {case.get('product_name')!r}: "
                f"{required - case.keys()}"
            )

    def test_fixture_includes_brief_cases(self) -> None:
        """Sanity — the audit cases listed in the Phase 36J brief
        must all appear in the fixture."""
        data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        names = {case["product_name"].lower() for case in data["cases"]}
        for needle in (
            "coulis mangue",
            "coulis figue bio",
            "confiture mangue sucrée",
            "épinards jardinière",
            "maïs doux huile d'olive",
            "velouté tomate",
            "gaspacho potiron",
            "mouliné poireaux pommes de terre",
            "thé corsé",
            "café noisette",
            "smoothie pêche",
            "sablés noisette",
            "croissants maïs",
            "tablette lait",
            "poêlée saumon légumes",
            "cassoulet provençale",
        ):
            assert needle in names, f"audit case missing: {needle!r}"


class TestAuditScript:
    def test_script_runs_and_lands_perfect_on_bundled_fixture(self) -> None:
        # Run the script in-process so we don't pay subprocess
        # cost in pytest. Import it directly via importlib so the
        # module name doesn't pollute the global namespace.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_phase36j_eval", _SCRIPT_PATH
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        # The script appends apps/api to sys.path; let it.
        sys.modules["_phase36j_eval"] = mod
        spec.loader.exec_module(mod)

        fixture = json.loads(
            _FIXTURE_PATH.read_text(encoding="utf-8")
        )
        report = mod.run_audit(fixture)
        # Phase 36J — Phase 36I guards bring the audit fixture
        # to 100% accuracy.
        assert report.accuracy == 1.0, (
            f"accuracy {report.accuracy:.2%}, "
            f"mismatches={[m.product_name for m in report.mismatches]}"
        )
        assert report.unknown_rate == 0.0
        # Every guard rule should fire at least once on the audit
        # fixture (it was hand-picked to exercise all of them).
        for rule in (
            "plant_core_demoted_preparation_or_simple_veg",
            "beverage_out_of_scope",
            "fruit_drink_non_core",
            "bakery_composite",
            "animal_prepared_meal_composite",
        ):
            assert report.guard_overrides_by_rule.get(rule, 0) >= 1, (
                f"rule {rule!r} never fired on the audit fixture; "
                f"got {report.guard_overrides_by_rule}"
            )
        # Markdown formatter does not blow up and includes section
        # headers.
        md = mod.format_markdown(report)
        assert "Predicted category distribution" in md
        assert "Guard overrides by rule" in md


# ---------------------------------------------------------------------------
# D. Non-regression — Phase 36I AIAccepted flow still works for good cases.
# ---------------------------------------------------------------------------


class TestNonRegressionAcceptedFlow:
    def test_high_confidence_good_case_still_accepted(self) -> None:
        """A clean classification (no guard firing) at confidence
        >= 0.70 must still land as AIAccepted, exactly like
        pre-Phase-36J behaviour."""

        class _CleanProvider(ClassifierProvider):
            @property
            def model(self) -> str:
                return "phase36j-clean"

            def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
                raise NotImplementedError

            def supports_batch(self) -> bool:
                return True

            def batch_classify(self, prompt: Any) -> ProviderResponse:
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
                    # Tofu → plant_based_core, no guard fires.
                    rows.append(
                        {
                            "id": row["id"],
                            "pt_group": "plant_based_core",
                            "confidence": 0.95,
                            "rationale": "phase36j fake",
                        }
                    )
                return ProviderResponse(
                    raw_text=json.dumps({"results": rows}),
                    model="phase36j-clean",
                )

        provider = _CleanProvider()
        bundle = batch_classify(
            [_make_product("Tofu Nature Bio", uuid4(), uuid4())],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert any(
            isinstance(v, AIAccepted) for v in bundle.verdicts
        )
        # No guard fired on a clean tofu name.
        assert bundle.guard_overrides_by_rule == {}
        assert bundle.unknown_safety_net_total == 0

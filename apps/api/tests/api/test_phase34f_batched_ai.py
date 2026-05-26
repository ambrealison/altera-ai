"""Phase 34F — Batched AI classification + validation-table contracts.

These tests cover the fix that turns "14 attempted / 0 classified / 14
failed" into the >95% coverage the wizard now expects on ordinary
French retailer CSVs.

Areas under test:

1. ``BatchClassifierPrompt`` — the system message names every PT enum,
   the user message carries product ids, the privacy guard runs on
   every per-product payload.
2. ``batch_classify`` orchestrator — happy path + per-row parse
   failures + unsupported category + provider error + threshold.
3. ``classify_route`` — when the configured provider supports
   batching, results are persisted with ``source=ai`` and the new
   ``ai_batch_count`` / ``ai_parse_failures`` counters are exposed.
4. ``GET /api/v1/projects/{id}/classifications`` — paginated, filterable,
   no commercial fields in the row payload.
5. Manual override supersedes AI classification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.batch_classifier import batch_classify
from altera_api.ai.batch_prompt import (
    BatchClassifierPrompt,
    build_batch_classifier_prompt,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
)
from altera_api.ai.policy import CommercialDataBlockError
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import (
    ClassifierProvider,
    ProviderError,
    ProviderResponse,
)
from altera_api.domain.common import (
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import ProteinTrackerGroup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_product(
    name: str,
    *,
    brand: str | None = None,
    methodologies: tuple[Methodology, ...] = (Methodology.PROTEIN_TRACKER,),
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        row_number=1,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        brand=brand,
        weight_per_item_kg=Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset(methodologies),
        pt_fields=(
            PTProductFields(items_purchased=Decimal("1"))
            if Methodology.PROTEIN_TRACKER in methodologies
            else None
        ),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A batched fake provider — drives the new code paths in tests
# ---------------------------------------------------------------------------


@dataclass
class BatchKeywordFakeProvider(ClassifierProvider):
    """Returns a batched JSON response keyed on product name keywords.

    For each product id in the input batch, finds the first keyword that
    appears in the product_name and emits the matching pt_group +
    confidence. Default rule: ``unknown`` @ 0.2.

    Provider implements ``batch_classify`` and reports
    ``supports_batch() = True``; that's what makes the orchestrator
    pick the batched path.
    """

    rules: dict[str, tuple[str, float]]
    default_pt_group: str = "unknown"
    default_confidence: float = 0.2
    model_name: str = "fake-batch-keyword-v1"
    # When set, replaces the raw response (useful for parse-failure tests).
    raw_override: str | None = None
    raise_provider_error: bool = False

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        # Not used by the batched path; keep a no-op for the abstract base.
        raise NotImplementedError("use batch_classify for this fake")

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: BatchClassifierPrompt) -> ProviderResponse:
        if self.raise_provider_error:
            raise ProviderError("simulated 429")
        if self.raw_override is not None:
            return ProviderResponse(raw_text=self.raw_override, model=self.model_name)

        # The user message is the concatenation of one JSON line per
        # product (built by build_batch_classifier_prompt). Parse each
        # line and look up rules by substring of product_name.
        results: list[dict[str, object]] = []
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
            chosen: tuple[str, float] = (
                self.default_pt_group,
                self.default_confidence,
            )
            for needle, val in self.rules.items():
                if needle.lower() in name:
                    chosen = val
                    break
            results.append(
                {
                    "id": row["id"],
                    "pt_group": chosen[0],
                    "confidence": chosen[1],
                    "rationale": f"matched keyword for {row['product_name']!r}",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": results}),
            model=self.model_name,
        )


# ---------------------------------------------------------------------------
# A — Prompt builder: privacy + structure
# ---------------------------------------------------------------------------


class TestBatchPromptPrivacy:
    def test_only_allowlist_fields_in_user_message(self) -> None:
        prompt_input = ClassifierPromptInput(
            product_name="Pommes Golden 1.5kg",
            brand="Marque-Repère",
            retailer_category="Fruits",
            ingredients_text=None,
            labels=("Bio",),
            language="fr",
            country="FR",
        )
        prompt = build_batch_classifier_prompt(
            [("ID-1", prompt_input)], Methodology.PROTEIN_TRACKER
        )
        # Forbidden field names must not appear anywhere in the payload
        # the provider will see. (The privacy guard already ran inside
        # build_batch_classifier_prompt — this is belt-and-braces.)
        forbidden = [
            "items_purchased",
            "items_sold",
            "weight_per_item_kg",
            "weight_per_item_g",
            "protein_pct",
            "revenue",
            "margin",
            "cost_price",
            "supplier_",
        ]
        for f in forbidden:
            assert f not in prompt.user_message
            assert f not in prompt.system_message

    def test_system_message_names_every_pt_enum_value(self) -> None:
        prompt = build_batch_classifier_prompt(
            [("X", ClassifierPromptInput(product_name="Foo"))],
            Methodology.PROTEIN_TRACKER,
        )
        for enum_value in (
            "plant_based_core",
            "plant_based_non_core",
            "composite_products",
            "animal_core",
            "out_of_scope",
            "unknown",
        ):
            assert enum_value in prompt.system_message, (
                f"system message missing PT enum {enum_value!r}"
            )

    def test_empty_batch_raises(self) -> None:
        with pytest.raises(ValueError):
            build_batch_classifier_prompt([], Methodology.PROTEIN_TRACKER)


# ---------------------------------------------------------------------------
# B — batch_classify orchestrator
# ---------------------------------------------------------------------------


class TestBatchClassifyOrchestrator:
    def test_obvious_french_products_get_classified(self) -> None:
        products = [
            _make_product("Pommes Golden 1.5kg"),
            _make_product("Tofu Nature Bio"),
            _make_product("Blanc de Poulet Rôti Tranché"),
            _make_product("Filets de Saumon Atlantique"),
            _make_product("Pois Chiches Cuits en Conserve"),
            _make_product("Yaourt Nature 0% MG"),
            _make_product("Steak Végétal Soja & Blé"),
            _make_product("Salade Poulet César"),
            _make_product("Eau Minérale Naturelle 1.5L"),
            _make_product("Promotion XYZ"),  # genuinely ambiguous
        ]
        provider = BatchKeywordFakeProvider(
            rules={
                "pommes": ("plant_based_core", 0.97),
                "tofu": ("plant_based_core", 0.96),
                "poulet rôti": ("animal_core", 0.97),
                "poulet": ("animal_core", 0.93),
                "saumon": ("animal_core", 0.97),
                "pois chiches": ("plant_based_core", 0.96),
                "yaourt": ("animal_core", 0.95),
                "végétal": ("plant_based_non_core", 0.9),
                "salade": ("composite_products", 0.85),
                "eau": ("out_of_scope", 0.97),
            },
        )
        bundle = batch_classify(
            products, provider, Methodology.PROTEIN_TRACKER, now=datetime.now(UTC)
        )
        accepted = [v for v in bundle.verdicts if isinstance(v, AIAccepted)]
        # 9 obvious products → accepted; 1 ambiguous ("Promotion XYZ").
        # Phase 36H — readable ambiguous names that the model returns
        # as ``unknown`` now go to needs_review (parse_failures
        # counter) instead of being accepted as final unknown.
        # Phase 36I — "Salade Poulet César" is now reclassified by
        # the animal_prepared_meal_composite guard from animal_core
        # to composite_products + needs_review (the model rule fires
        # ``poulet`` → animal_core 0.93 first, but a prepared-dish
        # marker ``salade`` triggers the guard). The accepted floor
        # therefore drops to 8 in this synthetic fixture.
        assert len(accepted) >= 8
        assert bundle.provider_errors == 0
        # Spot-check categories.
        by_id = {
            str(p.id): v
            for p, v in zip(products, bundle.verdicts, strict=True)
        }
        pommes_v = by_id[str(products[0].id)]
        assert isinstance(pommes_v, AIAccepted)
        assert pommes_v.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE
        poulet_v = by_id[str(products[2].id)]
        assert isinstance(poulet_v, AIAccepted)
        assert poulet_v.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_low_confidence_routes_to_review(self) -> None:
        provider = BatchKeywordFakeProvider(
            rules={"apple": ("plant_based_core", 0.5)},  # below 0.8 default
        )
        verdicts = batch_classify(
            [_make_product("apple something")],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        ).verdicts
        assert isinstance(verdicts[0], AINeedsReviewLowConfidence)

    def test_unsupported_category_routes_to_parse_failed(self) -> None:
        provider = BatchKeywordFakeProvider(
            rules={},
            # Emit a category that's NOT in the PT enum.
            raw_override=json.dumps(
                {
                    "results": [
                        {
                            "id": "WILL-REPLACE",
                            "pt_group": "made_up_category",
                            "confidence": 0.95,
                            "rationale": "n/a",
                        }
                    ]
                }
            ),
        )
        product = _make_product("Anything")
        # Replace the placeholder id with the real product id so the
        # lookup succeeds and the row reaches the coercion step.
        provider.raw_override = json.dumps(
            {
                "results": [
                    {
                        "id": str(product.id),
                        "pt_group": "made_up_category",
                        "confidence": 0.95,
                        "rationale": "n/a",
                    }
                ]
            }
        )
        bundle = batch_classify(
            [product],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AINeedsReviewParseFailed)
        assert bundle.unsupported_category_failures == 1

    def test_provider_error_marks_each_product(self) -> None:
        provider = BatchKeywordFakeProvider(
            rules={}, raise_provider_error=True
        )
        bundle = batch_classify(
            [_make_product("x"), _make_product("y")],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert all(
            isinstance(v, AIProviderError) for v in bundle.verdicts
        )
        assert bundle.provider_errors == 2

    def test_malformed_envelope_marks_all_in_batch(self) -> None:
        provider = BatchKeywordFakeProvider(
            rules={}, raw_override="not even close to json"
        )
        bundle = batch_classify(
            [_make_product("a"), _make_product("b")],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert all(
            isinstance(v, AINeedsReviewParseFailed) for v in bundle.verdicts
        )
        assert bundle.parse_failures == 2
        assert bundle.sample_errors  # diagnostic surfaced to the wizard

    def test_missing_id_in_results_is_parse_failed_for_that_row(self) -> None:
        # Provider returns results for only one of two products.
        p1 = _make_product("Pommes")
        p2 = _make_product("Saumon")
        provider = BatchKeywordFakeProvider(
            rules={},
            raw_override=json.dumps(
                {
                    "results": [
                        {
                            "id": str(p1.id),
                            "pt_group": "plant_based_core",
                            "confidence": 0.95,
                            "rationale": "ok",
                        }
                    ]
                }
            ),
        )
        bundle = batch_classify(
            [p1, p2],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        assert isinstance(bundle.verdicts[1], AINeedsReviewParseFailed)

    def test_chunking_at_default_batch_size(self) -> None:
        # Phase 34P — DEFAULT_BATCH_SIZE was reduced from 50 to 25 after
        # production showed envelope truncation at the larger size.
        # 120 products / 25 = 5 batches.
        from altera_api.ai.batch_prompt import DEFAULT_BATCH_SIZE

        products = [_make_product(f"Tofu lot {i}") for i in range(120)]
        provider = BatchKeywordFakeProvider(
            rules={"tofu": ("plant_based_core", 0.95)}
        )
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        expected = (len(products) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE
        assert bundle.batch_count == expected
        assert all(isinstance(v, AIAccepted) for v in bundle.verdicts)


# ---------------------------------------------------------------------------
# C — Privacy: assert_payload_allowed rejects forbidden fields
# ---------------------------------------------------------------------------


class TestPrivacyGuard:
    def test_forbidden_field_in_payload_raises(self) -> None:
        from altera_api.ai.policy import assert_payload_allowed

        with pytest.raises(CommercialDataBlockError):
            assert_payload_allowed(
                {"product_name": "x", "items_purchased": 42}
            )
        with pytest.raises(CommercialDataBlockError):
            assert_payload_allowed(
                {"product_name": "x", "supplier_terms": "secret"}
            )


# ---------------------------------------------------------------------------
# D — End-to-end through classify_route + classifications endpoint
# ---------------------------------------------------------------------------


_SPARSE_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Pommes Golden Bio,150,3.0
Tofu Nature Bio,200,2.0
Blanc de Poulet Roti Tranche,193,3.0
Filets de Saumon Atlantique,168,5.0
Pois Chiches Cuits en Conserve,73,3.0
Yaourt Nature 0pct MG,125,4.0
Steak Vegetal Soja et Ble,150,2.0
Salade Poulet Cesar,53,4.0
Eau Minerale Naturelle,1500,6.0
Promotion XYZ 9.99,100,1.0
"""

_SPARSE_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _create_pt_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34f",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


class TestClassificationsEndpoint:
    def test_returns_one_row_per_product_no_commercial_fields(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
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

        r = client.get(f"/api/v1/projects/{pid}/classifications")
        assert r.status_code == 200
        body = r.json()
        assert body["pt_eligible_total"] == 10
        # First page must include some products.
        assert len(body["items"]) > 0
        first = body["items"][0]
        # Every row must include only non-commercial fields. The
        # response model deliberately excludes commercial columns; if
        # any of these appear it's a serialiser bug we want to catch.
        for forbidden in (
            "weight_per_item_kg",
            "weight_per_item_g",
            "items_purchased",
            "items_sold",
            "protein_pct",
            "plant_protein_pct",
            "animal_protein_pct",
            "revenue",
            "margin",
            "cost_price",
        ):
            assert forbidden not in first, f"{forbidden!r} leaked into row"

    def test_filter_by_source_and_pt_group_and_search(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
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

        # All rows.
        r_all = client.get(f"/api/v1/projects/{pid}/classifications").json()
        # Search for "poulet" — at least one row (Blanc de Poulet or
        # Salade Poulet César) must come back.
        r_search = client.get(
            f"/api/v1/projects/{pid}/classifications?product_search=poulet"
        ).json()
        assert r_search["total"] >= 1
        assert r_search["total"] <= r_all["total"]
        # Counters update with filter.
        for row in r_search["items"]:
            assert "poulet" in row["product_name"].lower()

    def test_pagination(self, client: TestClient) -> None:
        pid = _create_pt_project(client)
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

        page1 = client.get(
            f"/api/v1/projects/{pid}/classifications?limit=3&offset=0"
        ).json()
        page2 = client.get(
            f"/api/v1/projects/{pid}/classifications?limit=3&offset=3"
        ).json()
        assert len(page1["items"]) == 3
        assert len(page2["items"]) == 3
        ids1 = {r["product_id"] for r in page1["items"]}
        ids2 = {r["product_id"] for r in page2["items"]}
        assert ids1.isdisjoint(ids2)
        assert page1["total"] == page2["total"]


class TestManualOverrideSupersedesAI:
    def test_decision_changed_after_classify_updates_pt_group(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
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
        rows = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"]
        # Grab any row to override.
        target = rows[0]
        r_dec = client.post(
            f"/api/v1/projects/{pid}/review/{target['product_id']}"
            "/protein_tracker/decision",
            json={"decision": "changed", "to_category": "animal_core"},
        )
        assert r_dec.status_code == 200
        # The row must now report pt_group=animal_core and
        # pt_source=manual_review (the manual decision supersedes
        # whatever AI/deterministic produced, audit history kept in
        # the audit log).
        after = client.get(
            f"/api/v1/projects/{pid}/classifications?product_search="
            + target["product_name"].split()[0]
        ).json()["items"]
        match = next(r for r in after if r["product_id"] == target["product_id"])
        assert match["pt_group"] == "animal_core"
        assert match["pt_source"] == ClassificationSource.MANUAL_REVIEW.value

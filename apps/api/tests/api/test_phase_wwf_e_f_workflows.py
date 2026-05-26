"""Phase WWF-E/F — required-column upload alerts + WWF-only and PT+WWF
end-to-end workflow tests.

Phase WWF-D shipped the deterministic WWF guards + audit fixture +
evaluator. Phase WWF-E/F now proves the surrounding workflow:

  * Part A — methodology-aware required-field reporting on
    ``POST /api/v1/uploads/preview-mapping`` and new synonyms
    (``Canal``, ``Marque distributeur``, ``Volume vendu``, ...).
  * Part B — WWF-only project: upload → ingest → classify creates
    WWF classifications and does NOT require PT-only fields
    (protein_pct, items_purchased).
  * Part C — PT+WWF project: PT and WWF classification jobs are
    independent rows; running one does not satisfy or overwrite
    the other.
  * Part F — privacy: WWF prompt payload contains ONLY the eight
    allow-listed product descriptor fields (no items_sold,
    weight_per_item_kg, retail_channel, is_own_brand, etc.).

These tests pin the contract — if anyone adds a commercial field
to the prompt, Phase F catches it; if anyone breaks methodology
isolation, Phase B/C catches it.
"""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from altera_api.ai.batch_prompt import build_batch_classifier_prompt
from altera_api.ai.policy import ALLOWED_PROMPT_FIELDS
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.common import Methodology
from altera_api.ingestion.mapping import infer_mapping

# =============================================================================
# Part A — required-column upload alerts (Phase WWF-E)
# =============================================================================


class TestRequiredFieldsByMethodology:
    """``infer_mapping`` reports missing required fields *only* for
    the methodologies that the project has enabled."""

    def test_wwf_only_missing_retail_channel(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "items_sold",
                "is_own_brand",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == ["retail_channel"]
        assert result.missing_required_pt == []  # PT not enabled

    def test_wwf_only_missing_is_own_brand(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "items_sold",
                "retail_channel",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == ["is_own_brand"]

    def test_wwf_only_missing_items_sold(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "retail_channel",
                "is_own_brand",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == ["items_sold"]

    def test_wwf_only_missing_weight(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "items_sold",
                "retail_channel",
                "is_own_brand",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == ["weight_per_item_kg"]

    def test_wwf_only_does_not_require_pt_fields(self) -> None:
        # Missing items_purchased + protein_pct — but project is WWF-only,
        # so PT requirements are not reported.
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "items_sold",
                "retail_channel",
                "is_own_brand",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == []
        assert result.missing_required_pt == []

    def test_pt_only_does_not_require_wwf_fields(self) -> None:
        result = infer_mapping(
            ["product_name", "weight_per_item_kg", "items_purchased"],
            methodologies=["protein_tracker"],
        )
        assert result.missing_required_pt == []
        assert result.missing_required_wwf == []  # WWF not enabled

    def test_pt_wwf_missing_wwf_only(self) -> None:
        result = infer_mapping(
            ["product_name", "weight_per_item_kg", "items_purchased"],
            methodologies=["protein_tracker", "wwf"],
        )
        assert result.missing_required_pt == []
        assert "items_sold" in result.missing_required_wwf
        assert "retail_channel" in result.missing_required_wwf
        assert "is_own_brand" in result.missing_required_wwf
        assert "external_product_id" in result.missing_required_wwf

    def test_pt_wwf_missing_pt_only(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "items_sold",
                "retail_channel",
                "is_own_brand",
            ],
            methodologies=["protein_tracker", "wwf"],
        )
        assert result.missing_required_wwf == []
        assert "items_purchased" in result.missing_required_pt

    def test_no_methodologies_falls_back_to_both(self) -> None:
        # Backwards-compat: when methodologies=None, both lists are
        # populated as a strict superset (so legacy callers still see
        # everything).
        result = infer_mapping(["product_name"])
        assert "items_purchased" in result.missing_required_pt
        assert "items_sold" in result.missing_required_wwf


class TestPhaseWWFENewSynonyms:
    """Phase WWF-E added French + English aliases per the brief."""

    def test_canal_maps_to_retail_channel(self) -> None:
        result = infer_mapping(["Canal"])
        assert result.entries[0].canonical_field == "retail_channel"

    def test_canal_de_vente_maps_to_retail_channel(self) -> None:
        result = infer_mapping(["Canal de vente"])
        assert result.entries[0].canonical_field == "retail_channel"

    def test_rayon_canal_maps_to_retail_channel(self) -> None:
        result = infer_mapping(["Rayon canal"])
        assert result.entries[0].canonical_field == "retail_channel"

    def test_marque_distributeur_maps_to_is_own_brand(self) -> None:
        result = infer_mapping(["Marque distributeur"])
        assert result.entries[0].canonical_field == "is_own_brand"

    def test_ventes_unites_maps_to_items_sold(self) -> None:
        result = infer_mapping(["Ventes unités"])
        assert result.entries[0].canonical_field == "items_sold"

    def test_quantite_vendue_maps_to_items_sold(self) -> None:
        result = infer_mapping(["Quantité vendue"])
        assert result.entries[0].canonical_field == "items_sold"

    def test_nombre_vendu_maps_to_items_sold(self) -> None:
        result = infer_mapping(["Nombre vendu"])
        assert result.entries[0].canonical_field == "items_sold"

    def test_volume_vendu_maps_to_items_sold(self) -> None:
        result = infer_mapping(["Volume vendu"])
        assert result.entries[0].canonical_field == "items_sold"

    def test_synonym_satisfies_wwf_required(self) -> None:
        """A full WWF header set built from the new aliases should
        satisfy all required-WWF fields."""
        result = infer_mapping(
            [
                "SKU",                       # external_product_id
                "Nom du produit",            # product_name
                "Poids unitaire kg",         # weight_per_item_kg
                "Ventes unités",             # items_sold
                "Canal",                     # retail_channel
                "Marque distributeur",       # is_own_brand
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_wwf == []


# =============================================================================
# Part A.2 — preview-mapping endpoint propagates methodology context
# =============================================================================


class TestPreviewMappingEndpoint:
    def test_wwf_only_reports_only_wwf_missing(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/v1/uploads/preview-mapping",
            json={
                "headers": ["product_name", "weight_per_item_kg"],
                "methodologies": ["wwf"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["missing_required_pt"] == []
        # WWF requires items_sold, retail_channel, is_own_brand, external_product_id
        assert "items_sold" in body["missing_required_wwf"]
        assert "retail_channel" in body["missing_required_wwf"]
        assert "is_own_brand" in body["missing_required_wwf"]

    def test_pt_only_reports_only_pt_missing(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/uploads/preview-mapping",
            json={
                "headers": ["product_name", "weight_per_item_kg"],
                "methodologies": ["protein_tracker"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "items_purchased" in body["missing_required_pt"]
        assert body["missing_required_wwf"] == []

    def test_pt_wwf_reports_both(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/uploads/preview-mapping",
            json={
                "headers": ["product_name"],
                "methodologies": ["protein_tracker", "wwf"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "items_purchased" in body["missing_required_pt"]
        assert "items_sold" in body["missing_required_wwf"]


# =============================================================================
# Part B — WWF-only end-to-end workflow
# =============================================================================


def _make_wwf_csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _create_wwf_only_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "WWF-Only Test",
            "methodologies_enabled": ["wwf"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_pt_wwf_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "PT+WWF Test",
            "methodologies_enabled": ["protein_tracker", "wwf"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestWWFOnlyWorkflow:
    def test_wwf_only_project_accepts_minimal_wwf_csv(
        self, client: TestClient
    ) -> None:
        """A WWF-only project ingests a CSV with the minimal WWF fields
        (no protein_pct, no items_purchased)."""
        pid = _create_wwf_only_project(client)
        csv_bytes = _make_wwf_csv(
            [
                {
                    "external_product_id": "WWF-001",
                    "product_name": "Pizza Jambon",
                    "weight_per_item_kg": "0.4",
                    "items_sold": "1000",
                    "retail_channel": "grocery_ambient",
                    "is_own_brand": "true",
                },
                {
                    "external_product_id": "WWF-002",
                    "product_name": "Camembert AOP",
                    "weight_per_item_kg": "0.25",
                    "items_sold": "500",
                    "retail_channel": "fresh",
                    "is_own_brand": "false",
                },
            ]
        )
        r = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", csv_bytes, "text/csv")},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] in (
            "ready_for_classification",
            "valid",
        ), body
        assert body["products_count"] == 2

    def test_wwf_only_project_classifies_with_wwf_methodology(
        self, client: TestClient, wwf_tiny_csv: bytes
    ) -> None:
        pid = _create_wwf_only_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", wwf_tiny_csv, "text/csv")},
        ).json()
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "wwf"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["methodology"] == "wwf"

    def test_wwf_only_project_rejects_pt_classification(
        self, client: TestClient, wwf_tiny_csv: bytes
    ) -> None:
        pid = _create_wwf_only_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", wwf_tiny_csv, "text/csv")},
        ).json()
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 400  # PT not enabled on this project


# =============================================================================
# Part C — PT+WWF independence
# =============================================================================


class TestPTWWFIndependence:
    def test_pt_and_wwf_classifications_coexist_on_same_product(
        self, client: TestClient
    ) -> None:
        """Same product can have a PT classification AND a WWF
        classification — they live in separate tables/blocks."""
        pid = _create_pt_wwf_project(client)
        csv_bytes = _make_wwf_csv(
            [
                {
                    "external_product_id": "DUAL-001",
                    "product_name": "Lentilles Vertes du Puy",
                    "weight_per_item_kg": "0.5",
                    "items_purchased": "1000",
                    "items_sold": "950",
                    "retail_channel": "grocery_ambient",
                    "is_own_brand": "false",
                    "protein_pct": "9.0",
                },
            ]
        )
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("dual.csv", csv_bytes, "text/csv")},
        ).json()
        upload_id = upload["id"]

        # PT classification
        r_pt = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r_pt.status_code == 200
        assert r_pt.json()["methodology"] == "protein_tracker"

        # WWF classification — separate call.
        r_wwf = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/classify",
            json={"methodology": "wwf"},
        )
        assert r_wwf.status_code == 200
        assert r_wwf.json()["methodology"] == "wwf"

        # Validation table returns both blocks for the product.
        r_list = client.get(
            f"/api/v1/projects/{pid}/classifications"
        )
        assert r_list.status_code == 200, r_list.text
        items = r_list.json()["items"]
        assert len(items) == 1
        item = items[0]
        # Each row should expose both methodology blocks.
        assert "protein_tracker" in item or "pt" in item or "pt_group" in item
        # The row should also expose WWF data.
        has_wwf = any(
            k.startswith("wwf") or k == "wwf_food_group" for k in item
        )
        assert has_wwf, f"expected WWF data on row, got keys={list(item)}"

    def test_pt_classification_does_not_set_wwf_food_group(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """Running PT classification leaves WWF rows untouched."""
        pid = _create_pt_wwf_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        # No WWF classify yet — wwf_food_group must be absent / unknown.
        r = client.get(
            f"/api/v1/projects/{pid}/classifications"
        )
        assert r.status_code == 200
        for item in r.json()["items"]:
            wwf_group = item.get("wwf_food_group")
            # Either WWF block is missing, or it's the system unknown state.
            assert wwf_group in (None, "unknown"), item


# =============================================================================
# Part F — privacy: WWF prompt excludes commercial fields
# =============================================================================


class TestWWFPromptPrivacy:
    """The WWF prompt must contain ONLY ALLOWED_PROMPT_FIELDS — never
    items_sold / items_purchased / weight_per_item_kg / retail_channel /
    is_own_brand / price / margin / revenue / store-level data."""

    def _build_prompt_input(self) -> ClassifierPromptInput:
        return ClassifierPromptInput(
            product_name="Pizza Jambon",
            brand="Carrefour",
            retailer_category="Plats cuisinés",
            retailer_subcategory="Pizza",
            ingredients_text="Pâte, tomate, jambon, fromage",
            labels=("AB",),
            language="fr",
            country="FR",
        )

    def test_allow_list_unchanged(self) -> None:
        """The allow-list MUST equal the exact eight descriptor fields."""
        assert ALLOWED_PROMPT_FIELDS == frozenset(
            {
                "product_name",
                "retailer_category",
                "retailer_subcategory",
                "brand",
                "ingredients_text",
                "labels",
                "language",
                "country",
            }
        )

    def test_wwf_prompt_payload_only_contains_allow_list_keys(self) -> None:
        prompt_input = self._build_prompt_input()
        payload = prompt_input.to_payload()
        # No commercial keys.
        forbidden = {
            "items_sold",
            "items_purchased",
            "weight_per_item_kg",
            "weight_per_item_g",
            "retail_channel",
            "is_own_brand",
            "protein_pct",
            "plant_protein_pct",
            "animal_protein_pct",
            "external_product_id",
            "sales_value",
            "revenue",
            "margin",
            "cost_price",
            "store_id",
            "supplier_name",
            "ean",
        }
        for k in forbidden:
            assert k not in payload, f"forbidden key {k!r} leaked into WWF payload"
        # And only allow-list keys appear.
        for k in payload:
            assert k in ALLOWED_PROMPT_FIELDS, (
                f"payload key {k!r} not in ALLOWED_PROMPT_FIELDS"
            )

    def test_wwf_batch_prompt_user_message_excludes_commercial_fields(
        self,
    ) -> None:
        """Build a full WWF batched prompt and verify the user message
        does not contain any commercial field name."""
        prompt = build_batch_classifier_prompt(
            items=[("p1", self._build_prompt_input())],
            methodology=Methodology.WWF,
        )
        msg = prompt.user_message
        assert "items_sold" not in msg
        assert "items_purchased" not in msg
        assert "weight_per_item_kg" not in msg
        assert "retail_channel" not in msg
        assert "is_own_brand" not in msg
        assert "revenue" not in msg
        assert "margin" not in msg
        assert "sales_value" not in msg
        # The expected product descriptor fields ARE present.
        assert "product_name" in msg
        assert "Pizza Jambon" in msg

    def test_pt_and_wwf_prompts_share_same_payload_keys(self) -> None:
        """The same product descriptor inputs are sent regardless of
        methodology — only the system prompt differs."""
        prompt_input = self._build_prompt_input()
        pt = build_batch_classifier_prompt(
            items=[("p1", prompt_input)],
            methodology=Methodology.PROTEIN_TRACKER,
        )
        wwf = build_batch_classifier_prompt(
            items=[("p1", prompt_input)], methodology=Methodology.WWF
        )
        # System prompts differ.
        assert pt.system_message != wwf.system_message
        # User messages contain the same payload data
        # (only "Classify each…" prefix is shared; item JSON is the same).
        pt_lines = [
            line for line in pt.user_message.splitlines() if line.startswith("{")
        ]
        wwf_lines = [
            line
            for line in wwf.user_message.splitlines()
            if line.startswith("{")
        ]
        assert pt_lines == wwf_lines


# =============================================================================
# Part G — methodology metric carries through to job result
# =============================================================================


class TestMethodologyMetric:
    def test_classify_response_includes_methodology(
        self, client: TestClient, wwf_tiny_csv: bytes
    ) -> None:
        pid = _create_wwf_only_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", wwf_tiny_csv, "text/csv")},
        ).json()
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "wwf"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["methodology"] == "wwf"
        assert "matched" in body
        assert "queued_for_review" in body

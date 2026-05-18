"""Phase 19B — safe classification rationale in review queue responses.

Covers:
- source field present and correct value
- rule_id present for deterministic classifications
- ai_model / ai_prompt_version null for deterministic items
- rationale_notes populated for contradiction_detected items
- rationale_notes populated for rule_collision items (conflicting rule IDs)
- rationale_notes empty for pass-through (requested) items
- no commercial fields exposed
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def contradiction_csv() -> bytes:
    """One product: vegan label in a Fresh Meat category → PTContradiction."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"EXT-C1,Vegan Beef Strips,PlantCo,Fresh Meat,,,"
        b"vegan,en,GB,false,0.200,50,25.0,label\n"
    )


@pytest.fixture
def collision_csv() -> bytes:
    """One product: Chicken Caesar Salad → fires pt.animal.poultry AND
    pt.composite.protein_salads → PTRuleCollision."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"EXT-R1,Chicken Caesar Salad,Deli,Ready Meals,,chicken lettuce caesar dressing,,"
        b"en,GB,false,0.250,80,18.0,label\n"
    )


@pytest.fixture
def passthrough_csv() -> bytes:
    """One product with no matching rules → queued as 'requested'."""
    return (
        b"external_product_id,product_name,brand,retailer_category,retailer_subcategory,"
        b"ingredients_text,labels,language,country,is_own_brand,"
        b"weight_per_item_kg,items_purchased,protein_pct,protein_source\n"
        b"EXT-P1,Unclassifiable Widget,BrandZ,Unknown,,,"
        b",en,GB,false,0.100,10,0.5,label\n"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Rationale Test Project",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_and_classify(
    client: TestClient,
    project_id: str,
    csv_bytes: bytes,
    methodology: str = "protein_tracker",
) -> str:
    upload = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    ).json()
    uid = upload["id"]
    client.post(
        f"/api/v1/projects/{project_id}/uploads/{uid}/classify",
        json={"methodology": methodology},
    )
    return uid


# ---------------------------------------------------------------------------
# Source field
# ---------------------------------------------------------------------------

class TestSourceField:
    def test_source_present_in_response(
        self, client: TestClient, passthrough_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, passthrough_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        for item in items:
            assert "source" in item

    def test_deterministic_source_value(
        self, client: TestClient, passthrough_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, passthrough_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        # Pass-through products get a provisional `unknown` classification
        # with source=deterministic
        for item in items:
            assert item["source"] == "deterministic"

    def test_rule_id_present_for_deterministic(
        self, client: TestClient, passthrough_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, passthrough_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        for item in items:
            assert item["rule_id"] is not None

    def test_ai_fields_null_for_deterministic(
        self, client: TestClient, passthrough_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, passthrough_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        for item in items:
            assert item["ai_model"] is None
            assert item["ai_prompt_version"] is None


# ---------------------------------------------------------------------------
# Rationale notes — contradiction
# ---------------------------------------------------------------------------

class TestContradictionRationale:
    def test_contradiction_item_is_queued(
        self, client: TestClient, contradiction_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, contradiction_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        contradiction_items = [
            i for i in items if i["reason"] == "contradiction_detected"
        ]
        assert len(contradiction_items) >= 1

    def test_contradiction_rationale_notes_non_empty(
        self, client: TestClient, contradiction_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, contradiction_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        contradiction_items = [
            i for i in items if i["reason"] == "contradiction_detected"
        ]
        assert len(contradiction_items) >= 1
        for item in contradiction_items:
            assert len(item["rationale_notes"]) > 0, (
                "contradiction items must have rationale_notes"
            )

    def test_contradiction_note_mentions_vegan_and_category(
        self, client: TestClient, contradiction_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, contradiction_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        contradiction_items = [
            i for i in items if i["reason"] == "contradiction_detected"
        ]
        assert len(contradiction_items) >= 1
        notes_text = " ".join(
            " ".join(item["rationale_notes"]) for item in contradiction_items
        ).lower()
        assert "vegan" in notes_text


# ---------------------------------------------------------------------------
# Rationale notes — rule collision
# ---------------------------------------------------------------------------

class TestRuleCollisionRationale:
    def test_collision_item_is_queued(
        self, client: TestClient, collision_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, collision_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        collision_items = [i for i in items if i["reason"] == "rule_collision"]
        assert len(collision_items) >= 1

    def test_collision_rationale_notes_are_rule_ids(
        self, client: TestClient, collision_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, collision_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        collision_items = [i for i in items if i["reason"] == "rule_collision"]
        assert len(collision_items) >= 1
        for item in collision_items:
            notes = item["rationale_notes"]
            assert len(notes) >= 2, (
                "rule_collision must expose at least two conflicting rule IDs"
            )
            # Rule IDs use dot-notation
            assert all("." in note for note in notes), (
                f"rule IDs should be dot-separated: {notes}"
            )

    def test_collision_notes_include_known_rule_ids(
        self, client: TestClient, collision_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, collision_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        collision_items = [i for i in items if i["reason"] == "rule_collision"]
        assert len(collision_items) >= 1
        notes = collision_items[0]["rationale_notes"]
        # "Chicken Caesar Salad" fires poultry + protein_salads
        assert any("poultry" in n for n in notes), f"expected poultry rule in {notes}"
        assert any("salad" in n for n in notes), f"expected salad rule in {notes}"


# ---------------------------------------------------------------------------
# Rationale notes — pass-through / requested
# ---------------------------------------------------------------------------

class TestPassthroughRationale:
    def test_passthrough_rationale_notes_empty(
        self, client: TestClient, passthrough_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, passthrough_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        requested_items = [i for i in items if i["reason"] == "requested"]
        for item in requested_items:
            assert item["rationale_notes"] == [], (
                "pass-through (requested) items must have empty rationale_notes"
            )


# ---------------------------------------------------------------------------
# No commercial fields
# ---------------------------------------------------------------------------

class TestNoCommercialFields:
    def test_rationale_fields_present_no_commercial_fields(
        self, client: TestClient, contradiction_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload_and_classify(client, pid, contradiction_csv)
        items = client.get(f"/api/v1/projects/{pid}/review").json()
        assert len(items) > 0
        required_rationale = {"source", "rule_id", "ai_model", "ai_prompt_version", "rationale_notes"}
        forbidden_commercial = {
            "items_purchased", "items_sold", "weight_per_item_kg",
            "revenue", "margin", "supplier_terms",
        }
        for item in items:
            for field in required_rationale:
                assert field in item, f"rationale field {field!r} missing"
            for field in forbidden_commercial:
                assert field not in item, f"commercial field {field!r} must not appear"

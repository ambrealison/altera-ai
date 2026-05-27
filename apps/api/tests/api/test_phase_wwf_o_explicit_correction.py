"""Phase WWF-O — explicit WWF correction backend.

The brief: the manual-review decision endpoint must accept a full
WWF payload (food group + subgroup + composite + bucket) so a
reviewer can pin every WWF field instead of relying on the
orchestrator's safe-default fallback (``_build_wwf_target``).

These tests pin:
  A. The new ``wwf`` payload routes the correction through
     ``build_wwf_target_explicit`` and lands the exact field values
     in the stored ``WWFProductClassification``.
  B. Domain invariants are still enforced (400 on bad combinations).
  C. The legacy single-field ``to_category`` path still works
     (non-regression).
  D. Correcting WWF leaves PT untouched (and vice versa).
"""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient


def _create_dual_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "WWF-O test",
            "methodologies_enabled": ["protein_tracker", "wwf"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload_one(client: TestClient, pid: str) -> tuple[str, UUID]:
    """Upload a single dual-methodology product and return
    (upload_id, product_id)."""
    csv_bytes = (
        b"external_product_id,product_name,weight_per_item_kg,"
        b"items_purchased,items_sold,retail_channel,is_own_brand,"
        b"protein_pct\n"
        b"X-001,Pizza Jambon,0.4,100,95,grocery_ambient,false,15.0\n"
    )
    r = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("dual.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 201
    upload_id = r.json()["id"]
    # Pull the product id off the /products endpoint via classifications.
    rows = client.get(f"/api/v1/projects/{pid}/classifications").json()["items"]
    assert len(rows) == 1
    return upload_id, UUID(rows[0]["product_id"])


def _force_wwf_classification_pending(
    client: TestClient, pid: str, upload_id: str
) -> None:
    """Run WWF classification so the product has SOME stored
    WWFProductClassification record. The exact category doesn't
    matter — we just need ``store.get_wwf_classification(pid)`` to
    return non-None."""
    r = client.post(
        f"/api/v1/projects/{pid}/uploads/{upload_id}/classify",
        json={"methodology": "wwf"},
    )
    assert r.status_code == 200, r.text


def _submit_wwf_correction(
    client: TestClient, pid: str, product_id: UUID, wwf_payload: dict
) -> int:
    r = client.post(
        f"/api/v1/projects/{pid}/review/{product_id}/wwf/decision",
        json={
            "decision": "changed",
            "wwf": wwf_payload,
            "reason": "Manual correction",
        },
    )
    return r.status_code


# ---------------------------------------------------------------------------
# A. Explicit WWF correction lands the exact field values
# ---------------------------------------------------------------------------


class TestExplicitWWFCorrection:
    def test_fg1_legumes_correction(self, client: TestClient) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG1",
                "wwf_is_composite": False,
                "fg1_subgroup": "legumes",
            },
        )
        assert status == 200, status
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_food_group"] == "FG1"
        assert row["wwf_fg1_subgroup"] == "legumes"
        assert row["wwf_source"] == "manual_review"

    def test_fg2_cheese_correction(self, client: TestClient) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG2",
                "fg2_subgroup": "cheese",
            },
        )
        assert status == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_food_group"] == "FG2"
        assert row["wwf_fg2_subgroup"] == "cheese"

    def test_fg3_animal_fat_correction(self, client: TestClient) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG3",
                "fg3_subgroup": "animal_based_fat",
            },
        )
        assert status == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_fg3_subgroup"] == "animal_based_fat"

    def test_fg5_whole_grain_correction(self, client: TestClient) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG5",
                "fg5_grain_kind": "whole_grain",
            },
        )
        assert status == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_fg5_grain_kind"] == "whole_grain"

    def test_fg7_animal_snack_correction(self, client: TestClient) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG7",
                "fg7_snack_kind": "animal_based_snack",
            },
        )
        assert status == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_fg7_snack_kind"] == "animal_based_snack"

    def test_composite_meat_based_correction(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG1",
                "wwf_is_composite": True,
                "composite_step1_bucket": "meat_based",
                "fg1_subgroup": "processed_meats_alternatives",
            },
        )
        assert status == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_is_composite"] is True
        assert row["wwf_composite_step1_bucket"] == "meat_based"


# ---------------------------------------------------------------------------
# B. Domain invariants enforced
# ---------------------------------------------------------------------------


class TestInvariantsEnforced:
    def test_fg1_without_subgroup_rejected(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG1",
                # missing fg1_subgroup → 400
            },
        )
        assert status == 400

    def test_composite_without_bucket_rejected(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG1",
                "wwf_is_composite": True,
                "fg1_subgroup": "legumes",
                # missing composite_step1_bucket → 400
            },
        )
        assert status == 400

    def test_unknown_food_group_value_rejected(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {"wwf_food_group": "FG_BOGUS"},
        )
        assert status == 400

    def test_wwf_payload_on_pt_methodology_rejected(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # POST a WWF payload on the PT endpoint → 400 (methodology
        # mismatch).
        r = client.post(
            f"/api/v1/projects/{pid}/review/{product_id}/protein_tracker/decision",
            json={
                "decision": "changed",
                "wwf": {
                    "wwf_food_group": "FG1",
                    "fg1_subgroup": "legumes",
                },
            },
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# C. Non-regression: legacy to_category path
# ---------------------------------------------------------------------------


class TestLegacyToCategoryStillWorks:
    def test_legacy_to_category_path(self, client: TestClient) -> None:
        """The legacy single-field correction must keep working —
        ``_build_wwf_target`` picks safe defaults for the subgroup."""
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Phase WWF-O — we DON'T pre-classify; ``submit_decision``
        # synthesises a review item on the fly and the change path
        # accepts a fresh target when no prior classification exists.
        r = client.post(
            f"/api/v1/projects/{pid}/review/{product_id}/wwf/decision",
            json={"decision": "changed", "to_category": "FG1"},
        )
        assert r.status_code == 200
        row = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"][0]
        assert row["wwf_food_group"] == "FG1"


# ---------------------------------------------------------------------------
# D. WWF correction doesn't touch PT
# ---------------------------------------------------------------------------


class TestMethodologyIsolation:
    def test_wwf_correction_does_not_touch_pt(
        self, client: TestClient
    ) -> None:
        pid = _create_dual_project(client)
        upload_id, product_id = _upload_one(client, pid)
        # Run PT first so there's a PT classification to compare.
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload_id}/classify",
            json={"methodology": "protein_tracker"},
        )
        _force_wwf_classification_pending(client, pid, upload_id)
        # Snapshot PT state.
        rows_before = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"]
        pt_before = rows_before[0]["pt_group"]
        pt_source_before = rows_before[0]["pt_source"]
        # Submit explicit WWF correction.
        status = _submit_wwf_correction(
            client,
            pid,
            product_id,
            {
                "wwf_food_group": "FG2",
                "fg2_subgroup": "dairy_alternative_plant",
            },
        )
        assert status == 200
        rows_after = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()["items"]
        assert rows_after[0]["pt_group"] == pt_before
        assert rows_after[0]["pt_source"] == pt_source_before

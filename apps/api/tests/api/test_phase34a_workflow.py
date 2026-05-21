"""Phase 34A — guided-workflow status + zero-row run guard.

The two contracts this commit ships:

  1. ``GET /projects/{id}/workflow-status`` returns a stepper-shaped
     payload whose ``current_step`` and ``next_action`` change as the
     project moves through upload → ingest → classify → review →
     enrich → calculate → report.

  2. ``POST /projects/{id}/runs`` is gated by the same workflow logic.
     A 0-row calculation may never persist; instead the route returns
     a structured ``run_not_ready`` 400 with localised
     ``blocking_reasons`` so the UI can show the exact next action.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "34A workflow",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(client: TestClient, pid: str, csv: bytes) -> str:
    r = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("data.csv", csv, "text/csv")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _step(payload: dict, key: str) -> dict:
    return next(s for s in payload["steps"] if s["key"] == key)


# ---------------------------------------------------------------------------
# Workflow status across project lifecycle stages
# ---------------------------------------------------------------------------


class TestWorkflowStatusEmpty:
    def test_brand_new_project_current_step_is_upload(self, client: TestClient) -> None:
        pid = _create_project(client)
        r = client.get(f"/api/v1/projects/{pid}/workflow-status")
        assert r.status_code == 200
        body = r.json()
        assert body["current_step"] == "upload"
        assert _step(body, "upload")["status"] == "needs_action"
        assert _step(body, "calculation")["status"] in ("blocked", "locked")

    def test_empty_project_has_no_eligible_products(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        calc = _step(body, "calculation")
        assert calc["status"] == "blocked"
        assert any(
            r["code"] == "no_eligible_products" for r in calc["blocking_reasons"]
        )


class TestWorkflowStatusAfterUpload:
    def test_classification_needed_after_upload(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        # Mapping / ingestion auto-complete once products exist.
        assert _step(body, "ingestion")["status"] == "complete"
        # Phase 34I — AI classification is now the primary step (the
        # deterministic step has been removed from the normal flow).
        ai = _step(body, "ai_classification")
        assert ai["status"] == "needs_action"
        assert ai["counts"]["classified"] == 0
        assert ai["counts"]["remaining"] > 0
        # Calculation blocked by classification_required.
        calc = _step(body, "calculation")
        assert calc["status"] == "blocked"
        codes = {r["code"] for r in calc["blocking_reasons"]}
        assert "classification_required" in codes


class TestWorkflowStatusAfterClassify:
    def _full_pipeline_through_review(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> str:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        return pid

    def test_pt_tiny_after_full_pipeline_is_ready(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = self._full_pipeline_through_review(client, pt_tiny_csv)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        calc = _step(body, "calculation")
        # pt_tiny rows all have retailer protein → eligible without
        # enrichment. Calculation step should be "ready".
        assert calc["status"] == "ready", body
        assert calc["counts"]["eligible_rows"] > 0
        assert calc["blocking_reasons"] == []
        assert body["current_step"] == "calculation"
        assert body["next_action"]["action"] == "run_calculation"


class TestWorkflowStatusMissingNutrition:
    """Sparse CSV (no protein_pct anywhere) lands in
    ``nutrition_required`` after classification."""

    def test_missing_nutrition_blocks_calculation(self, client: TestClient) -> None:
        pid = _create_project(client)
        # Sparse Carrefour-style 3-column CSV — no protein_pct.
        csv = (
            b"Product Name (FR),Poids unitaire produit,Volume\n"
            b"Blanc de poulet,133,1000\n"
            b"Tofu nature,200,500\n"
        )
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        # Clear any review items so the only blocker is nutrition.
        queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        calc = _step(body, "calculation")
        assert calc["status"] == "blocked"
        codes = {r["code"] for r in calc["blocking_reasons"]}
        assert "nutrition_required" in codes
        # NEVO step should be the next recommended action.
        assert body["current_step"] in (
            "nutrition_enrichment_nevo",
            "manual_nutrition_review",
        )


# ---------------------------------------------------------------------------
# Run preflight — zero-row runs are impossible
# ---------------------------------------------------------------------------


class TestRunPreflightZeroRowGuard:
    def test_empty_project_run_blocked(self, client: TestClient) -> None:
        pid = _create_project(client)
        r = client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error_code"] == "run_not_ready"
        assert "calcul" in detail["message"].lower()

    def test_blocked_run_does_not_persist_calculation_row(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        before = client.get(f"/api/v1/projects/{pid}/runs").json()
        client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        after = client.get(f"/api/v1/projects/{pid}/runs").json()
        assert len(before["items"]) == len(after["items"])
        assert after["items"] == []

    def test_missing_nutrition_returns_nutrition_required(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client)
        csv = (
            b"Product Name (FR),Poids unitaire produit,Volume\n"
            b"Blanc de poulet,133,1000\n"
        )
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("sparse.csv", csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        r = client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        codes = {br["code"] for br in detail["blocking_reasons"]}
        assert "nutrition_required" in codes
        # The blocker carries a localised label and a next_action hint.
        nut = next(
            br for br in detail["blocking_reasons"] if br["code"] == "nutrition_required"
        )
        assert nut["label"]
        assert nut["next_action"] == "apply_nevo"

    def test_ready_project_runs_succeed(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("data.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        queue = client.get(f"/api/v1/projects/{pid}/review").json()["items"]
        for item in queue:
            client.post(
                f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
                json={"decision": "changed", "to_category": "plant_based_core"},
            )
        r = client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["rows_count"] > 0

"""Phase WWF-G — workflow-status response for WWF-only and PT+WWF projects.

The wizard relies on the per-step status emitted by
``compute_workflow_status`` to decide what is "available", "locked",
"not_needed", etc. Phase WWF-G makes the frontend render NEVO and
Nutrition Validation only when Protein Tracker is enabled, but the
backend response is left untouched (the steps are still emitted, but
their status correctly reflects the absence of PT data).

These tests pin the backend invariants Phase WWF-G's frontend
filtering relies on:

  1. WWF-only project: ``nutrition_enrichment_nevo`` is ``"locked"``
     (no PT data) and accessible=False — so even if the wizard chose
     to render it, the user couldn't act on it.
  2. PT-only project (existing behavior unchanged): NEVO is reachable
     when products lack retailer-supplied nutrition.
  3. PT+WWF project: both methodologies are reported in
     ``methodologies_enabled``; PT-shaped steps still fire normally
     so a PT+WWF user can drive PT classification through the wizard.
  4. workflow-status returns no 500 on a freshly-created WWF-only
     project (the "no products yet" branch handles WWF cleanly).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project(
    client: TestClient,
    *,
    methodologies: list[str],
    name: str = "Test project",
) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": name,
            "methodologies_enabled": methodologies,
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestWWFOnlyWorkflowStatus:
    def test_workflow_status_200_on_empty_wwf_only_project(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["wwf"])
        r = client.get(f"/api/v1/projects/{pid}/workflow-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["methodologies_enabled"] == ["wwf"]
        assert isinstance(body["steps"], list)

    def test_wwf_only_nevo_step_not_needed(self, client: TestClient) -> None:
        """Phase Product-UX-F — PT not enabled (WWF-only) → the PT-only
        NEVO step is ``not_needed`` (complete-neutral for progress), NOT
        ``locked`` (which previously dragged WWF-only progress below 100%)."""
        pid = _create_project(client, methodologies=["wwf"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        nevo = next(
            s for s in body["steps"] if s["key"] == "nutrition_enrichment_nevo"
        )
        assert nevo["status"] == "not_needed"

    def test_wwf_only_nutrition_validation_not_needed(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["wwf"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        nv = next(
            s for s in body["steps"] if s["key"] == "nutrition_validation"
        )
        assert nv["status"] == "not_needed"

    def test_wwf_only_pt_only_steps_not_needed_after_upload(
        self, client: TestClient, wwf_tiny_csv: bytes
    ) -> None:
        """Phase Product-UX-F — after a WWF CSV upload (no PT data), the
        PT-only steps stay ``not_needed`` so they never lower WWF-only
        progress."""
        pid = _create_project(client, methodologies=["wwf"])
        client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", wwf_tiny_csv, "text/csv")},
        )
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        nevo = next(
            s for s in body["steps"] if s["key"] == "nutrition_enrichment_nevo"
        )
        nv = next(
            s for s in body["steps"] if s["key"] == "nutrition_validation"
        )
        review = next(
            s for s in body["steps"]
            if s["key"] == "manual_classification_review"
        )
        assert nevo["status"] == "not_needed"
        assert nv["status"] == "not_needed"
        assert review["status"] == "not_needed"


class TestPTOnlyWorkflowStatusUnchanged:
    """Non-regression: PT-only projects still see PT-shaped steps."""

    def test_pt_only_methodologies_enabled_is_pt(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["protein_tracker"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        assert body["methodologies_enabled"] == ["protein_tracker"]

    def test_pt_only_has_pt_steps(self, client: TestClient) -> None:
        pid = _create_project(client, methodologies=["protein_tracker"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        keys = {s["key"] for s in body["steps"]}
        # Phase WWF-G doesn't drop any backend step; the frontend filters.
        assert "nutrition_enrichment_nevo" in keys
        assert "nutrition_validation" in keys
        assert "ai_classification" in keys
        assert "calculation" in keys
        assert "report" in keys


class TestPTWWFWorkflowStatus:
    def test_pt_wwf_methodologies_enabled_includes_both(
        self, client: TestClient
    ) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        assert set(body["methodologies_enabled"]) == {
            "protein_tracker",
            "wwf",
        }

    def test_pt_wwf_classification_by_methodology_has_both_keys(
        self, client: TestClient
    ) -> None:
        """Phase WWF-H — workflow-status now exposes per-methodology
        classification counts so the PT+WWF wizard can render two
        independent classification cards."""
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        assert "classification_by_methodology" in body
        cbm = body["classification_by_methodology"]
        assert "protein_tracker" in cbm
        assert "wwf" in cbm
        # Empty project: both have zero total/classified/pending.
        for m in ("protein_tracker", "wwf"):
            cm = cbm[m]
            assert cm["total"] == 0
            assert cm["classified"] == 0
            assert cm["pending"] == 0
            assert cm["status"] == "locked"

    def test_wwf_only_classification_by_methodology_has_only_wwf(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["wwf"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        cbm = body["classification_by_methodology"]
        assert list(cbm.keys()) == ["wwf"]

    def test_pt_only_classification_by_methodology_has_only_pt(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["protein_tracker"])
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        cbm = body["classification_by_methodology"]
        assert list(cbm.keys()) == ["protein_tracker"]

    def test_classifications_row_exposes_wwf_subgroup_fields(
        self, client: TestClient, wwf_tiny_csv: bytes
    ) -> None:
        """Phase WWF-I — the /classifications row payload now includes
        wwf subgroup + composite fields so the validation table can
        render the WWF view without a second round-trip."""
        pid = _create_project(client, methodologies=["wwf"])
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("wwf.csv", wwf_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "wwf"},
        )
        r = client.get(f"/api/v1/projects/{pid}/classifications")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) > 0
        # Pick any row that has a WWF classification — its payload
        # must expose ALL the new keys (even when null).
        sample = items[0]
        for key in (
            "wwf_food_group",
            "wwf_source",
            "wwf_confidence",
            "wwf_fg1_subgroup",
            "wwf_fg2_subgroup",
            "wwf_fg3_subgroup",
            "wwf_fg5_grain_kind",
            "wwf_fg7_snack_kind",
            "wwf_is_composite",
            "wwf_composite_step1_bucket",
            "wwf_rule_id",
        ):
            assert key in sample, f"missing key {key!r} on WWF row payload"

    def test_pt_wwf_steps_emitted_normally(self, client: TestClient) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        body = client.get(
            f"/api/v1/projects/{pid}/workflow-status"
        ).json()
        keys = {s["key"] for s in body["steps"]}
        # The full PT step set is present (PT+WWF projects drive PT
        # through the wizard; the WWF classification CTA is the explicit
        # follow-up — see Phase WWF-G frontend filtering).
        for required in (
            "upload",
            "methodology",
            "ai_classification",
            "manual_classification_review",
            "nutrition_enrichment_nevo",
            "nutrition_validation",
            "calculation",
            "report",
        ):
            assert required in keys, f"step {required!r} missing for PT+WWF"

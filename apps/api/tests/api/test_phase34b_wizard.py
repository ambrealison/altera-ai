"""Phase 34B — guided wizard fields on workflow-status + classify/enrich params.

Contracts under test:

1. ``GET /projects/{id}/workflow-status`` now returns ``accessible``,
   ``editable``, ``summary``, and ``active_step`` on each step.

2. ``POST .../classify`` accepts ``deterministic_only=true`` which skips AI.

3. ``POST .../enrichments/apply-references`` accepts ``{"providers":
   ["nevo"]}`` (NEVO-only) and ``{"providers": ["ciqual"]}``
   (CIQUAL-only).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import AlteraRole, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.main import app


def _promote_to_altera(store: InMemoryStore) -> None:
    """Promote the default dev-auth user to altera_analyst so
    can_apply_enrichment is True."""
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


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "34B wizard",
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
# 1. New wizard fields on workflow-status
# ---------------------------------------------------------------------------


class TestWizardFields:
    def test_empty_project_upload_step_is_accessible(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        upload = _step(body, "upload")
        # Upload step must always be accessible (user can import at any time).
        assert upload["accessible"] is True
        assert upload["editable"] is True

    def test_empty_project_locked_steps_not_accessible(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        # Report step must be locked and not accessible until a run exists.
        report = _step(body, "report")
        assert report["accessible"] is False
        # Calculation is "blocked" (not "locked") — it IS accessible so the
        # user can see the blocking reasons.
        calc = _step(body, "calculation")
        assert calc["status"] == "blocked"
        assert calc["accessible"] is True

    def test_active_step_alias_present(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        assert "active_step" in body
        assert body["active_step"] == body["current_step"]

    def test_upload_summary_after_import(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        upload = _step(body, "upload")
        assert upload["status"] == "complete"
        assert upload["summary"] is not None
        assert "produit" in upload["summary"]

    def test_methodology_summary_after_project_creation(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        meth = _step(body, "methodology")
        assert meth["status"] == "complete"
        assert meth["summary"] is not None

    def test_ai_classification_step_accessible_after_upload(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        # Phase 34I — the deterministic step has been removed from the
        # normal workflow. AI classification is the primary step.
        pid = _create_project(client)
        _upload(client, pid, pt_tiny_csv)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        ai = _step(body, "ai_classification")
        assert ai["accessible"] is True

    def test_ai_classification_summary_after_classify(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        ai = _step(body, "ai_classification")
        assert ai["summary"] is not None
        assert "classifié" in ai["summary"]

    def test_nevo_not_accessible_on_empty_project(self, client: TestClient) -> None:
        pid = _create_project(client)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        nevo = _step(body, "nutrition_enrichment_nevo")
        assert nevo["accessible"] is False

    def test_report_accessible_after_run(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
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
        r = client.post(
            f"/api/v1/projects/{pid}/runs", json={"methodology": "protein_tracker"}
        )
        assert r.status_code == 201, r.text
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        report = _step(body, "report")
        assert report["accessible"] is True
        assert report["summary"] is not None
        assert "calcul" in report["summary"]


# ---------------------------------------------------------------------------
# 2. deterministic_only flag on classify
# ---------------------------------------------------------------------------


class TestDeterministicOnly:
    def test_deterministic_only_classifies_without_ai(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker", "deterministic_only": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Should produce matched/pass_through/queued counts (deterministic only).
        assert "matched" in body
        assert "queued_for_review" in body

    def test_default_classify_accepts_false_deterministic_only(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker", "deterministic_only": False},
        )
        assert r.status_code == 200, r.text

    def test_classify_without_flag_still_works(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(client)
        uid = _upload(client, pid, pt_tiny_csv)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={"methodology": "protein_tracker"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 3. providers filter on apply-references
# ---------------------------------------------------------------------------


class TestProvidersFilter:
    """Providers filter tests require altera-internal auth (can_apply_enrichment)."""

    def _classify_all(self, client: TestClient, pid: str, uid: str) -> None:
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

    def test_nevo_only_accepted(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(altera_client)
        uid = _upload(altera_client, pid, pt_tiny_csv)
        self._classify_all(altera_client, pid, uid)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["nevo"]},
        )
        assert r.status_code == 200, r.text

    def test_ciqual_only_accepted(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(altera_client)
        uid = _upload(altera_client, pid, pt_tiny_csv)
        self._classify_all(altera_client, pid, uid)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={"providers": ["ciqual"]},
        )
        assert r.status_code == 200, r.text

    def test_no_providers_runs_all(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(altera_client)
        uid = _upload(altera_client, pid, pt_tiny_csv)
        self._classify_all(altera_client, pid, uid)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
            json={},
        )
        assert r.status_code == 200, r.text

    def test_empty_body_runs_all(
        self, altera_client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(altera_client)
        uid = _upload(altera_client, pid, pt_tiny_csv)
        self._classify_all(altera_client, pid, uid)
        r = altera_client.post(
            f"/api/v1/projects/{pid}/enrichments/apply-references",
        )
        assert r.status_code == 200, r.text

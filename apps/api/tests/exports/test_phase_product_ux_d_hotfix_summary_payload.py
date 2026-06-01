"""Hotfix — report builder must parse persisted JSON summary payloads.

``RunRecord.summary_payload`` is stored as JSON, so on read its values
are JSON primitives (UUID/Decimal as strings, tuples as lists, enums as
their string values). The strict domain summary models reject that shape
under a plain ``model_validate`` — which crashed report generation in
production while in-memory tests passed (they build the payload via
python-mode ``model_dump`` with native types).

These tests reproduce the production shape with ``model_dump(mode="json")``
and pin that:
  * the parse helpers restore domain types (UUID / Decimal / tuple / Enum);
  * a strict ``model_validate`` of the JSON shape DOES raise (documents the
    bug the helpers work around);
  * the report endpoint returns 200 for PT-only, WWF-only, and a project
    with both methodologies, with a frontend-friendly JSON response
    (numbers as strings, run_id as string, enum as value).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.dependencies import get_data_store
from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import (
    AlteraRole,
    ClientRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
)
from altera_api.domain.wwf import (
    WWFCalculationSummary,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
)
from altera_api.exports.summary_payload import (
    parse_pt_summary_payload,
    parse_wwf_summary_payload,
)
from altera_api.main import app

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Summary fixtures (built as domain objects, then JSON-serialized like the DB)
# ---------------------------------------------------------------------------


def _pt_summary(run_id: UUID) -> ProteinTrackerCalculationSummary:
    aggs = tuple(
        ProteinTrackerGroupAggregate(
            pt_group=g, volume_kg=Decimal("100"), protein_kg=Decimal("20"), item_count=5
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    )
    return ProteinTrackerCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_group=aggs,
        plant_protein_kg=Decimal("60.12345000"),
        animal_protein_kg=Decimal("40.00000000"),
        total_in_scope_protein_kg=Decimal("100.12345000"),
        plant_share_pct=Decimal("60.06"),
        animal_share_pct=Decimal("39.94"),
        rows_with_per_product_split=0,
        rows_protein_source_label=10,
        rows_protein_source_reference_db=5,
        out_of_scope_count=2,
        unknown_count=1,
        methodology_version="1.0.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    )


def _wwf_summary(run_id: UUID) -> WWFCalculationSummary:
    fg = tuple(
        WWFFoodGroupAggregate(
            food_group=f,
            weight_kg=Decimal("100"),
            weight_kg_dairy_equiv=Decimal("100") if f is WWFFoodGroup.FG2 else None,
            share_pct=Decimal("14"),
            phd_reference_share_pct=Decimal("15") if f is WWFFoodGroup.FG1 else None,
        )
        for f in (
            WWFFoodGroup.FG1,
            WWFFoodGroup.FG2,
            WWFFoodGroup.FG3,
            WWFFoodGroup.FG4,
            WWFFoodGroup.FG5,
            WWFFoodGroup.FG6,
            WWFFoodGroup.FG7,
        )
    )
    return WWFCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_food_group=fg,
        total_sales_weight_in_scope_kg=Decimal("175266.95000000"),
        composites_total_weight_kg=Decimal("40"),
        composites_meat_based_kg=Decimal("10"),
        composites_seafood_based_kg=Decimal("10"),
        composites_vegetarian_kg=Decimal("10"),
        composites_vegan_kg=Decimal("10"),
        whole_diet_plant_weight_kg=Decimal("400"),
        whole_diet_animal_weight_kg=Decimal("300"),
        out_of_scope_count=3,
        unknown_count=2,
        methodology_version="1.0.0",
        methodology_source_edition="WWF Food Practice 2024",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    )


def _json_payload(summary) -> dict:
    """Round-trip through JSON exactly like a Postgres jsonb read."""
    return json.loads(summary.model_dump_json())


# ---------------------------------------------------------------------------
# Unit — the helpers restore domain types from the JSON shape
# ---------------------------------------------------------------------------


def test_wwf_json_payload_has_serialized_primitives() -> None:
    payload = _json_payload(_wwf_summary(uuid4()))
    assert isinstance(payload["run_id"], str)
    assert isinstance(payload["total_sales_weight_in_scope_kg"], str)
    assert payload["total_sales_weight_in_scope_kg"] == "175266.95000000"
    assert isinstance(payload["per_food_group"], list)
    assert payload["methodology"] == "wwf"


def test_strict_model_validate_rejects_json_payload() -> None:
    """Documents the production bug the helper works around."""
    from pydantic import ValidationError

    payload = _json_payload(_wwf_summary(uuid4()))
    with pytest.raises(ValidationError):
        WWFCalculationSummary.model_validate(payload)


def test_parse_wwf_summary_payload_restores_types() -> None:
    rid = uuid4()
    payload = _json_payload(_wwf_summary(rid))
    parsed = parse_wwf_summary_payload(payload)
    assert isinstance(parsed, WWFCalculationSummary)
    assert parsed.run_id == rid and isinstance(parsed.run_id, UUID)
    assert parsed.total_sales_weight_in_scope_kg == Decimal("175266.95000000")
    assert isinstance(parsed.total_sales_weight_in_scope_kg, Decimal)
    assert parsed.methodology is Methodology.WWF
    assert isinstance(parsed.per_food_group, tuple)
    assert len(parsed.per_food_group) == 7
    assert isinstance(parsed.per_food_group[0].weight_kg, Decimal)


def test_parse_pt_summary_payload_restores_types() -> None:
    rid = uuid4()
    payload = _json_payload(_pt_summary(rid))
    assert isinstance(payload["run_id"], str)
    assert isinstance(payload["plant_protein_kg"], str)
    assert isinstance(payload["per_group"], list)
    parsed = parse_pt_summary_payload(payload)
    assert isinstance(parsed, ProteinTrackerCalculationSummary)
    assert parsed.run_id == rid and isinstance(parsed.run_id, UUID)
    assert parsed.plant_protein_kg == Decimal("60.12345000")
    assert isinstance(parsed.plant_protein_kg, Decimal)
    assert parsed.methodology is Methodology.PROTEIN_TRACKER
    assert isinstance(parsed.per_group, tuple)


def test_parse_helpers_passthrough_domain_instances() -> None:
    pt = _pt_summary(uuid4())
    wwf = _wwf_summary(uuid4())
    assert parse_pt_summary_payload(pt) is pt
    assert parse_wwf_summary_payload(wwf) is wwf


# ---------------------------------------------------------------------------
# Integration — the report endpoint returns 200 from JSON-shaped payloads
# ---------------------------------------------------------------------------


def _org(store: InMemoryStore, org_type: OrganisationType) -> Organisation:
    o = Organisation(
        id=uuid4(), name="Org", slug="org", organisation_type=org_type, created_at=_NOW
    )
    store.organisations[o.id] = o
    return o


def _user(store: InMemoryStore, org: Organisation, role) -> UserProfile:
    uid = uuid4()
    p = UserProfile(
        user_id=uid,
        organisation_id=org.id,
        email=f"{uid}@t.local",
        display_name="U",
        role=role,
        created_at=_NOW,
    )
    store.users[uid] = p
    return p


def _auth(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id,
        email=user.email,
        organisation_id=org.id,
        role=user.role,
        organisation_type=org.organisation_type,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
    )


def _run_with_json_payload(
    store: InMemoryStore, *, project_id: UUID, org_id: UUID, methodology: Methodology
) -> RunRecord:
    run_id = uuid4()
    summary = (
        _pt_summary(run_id)
        if methodology is Methodology.PROTEIN_TRACKER
        else _wwf_summary(run_id)
    )
    rec = RunRecord(
        id=run_id,
        project_id=project_id,
        methodology=methodology,
        started_at=_NOW,
        finished_at=_NOW,
        triggered_by=uuid4(),
        rows_payload=[],
        # The production shape: JSON primitives, NOT native types.
        summary_payload=_json_payload(summary),
        rows_count=0,
        organisation_id=org_id,
    )
    store.runs[run_id] = rec
    return rec


@contextmanager
def _client(store: InMemoryStore, auth: AuthContext):
    app.dependency_overrides[authed_user] = lambda: auth
    app.dependency_overrides[get_data_store] = lambda: store
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(authed_user, None)
        app.dependency_overrides.pop(get_data_store, None)


def _build_project(store: InMemoryStore, methodologies: frozenset[Methodology]):
    altera = _org(store, OrganisationType.ALTERA_INTERNAL)
    client_org = _org(store, OrganisationType.GMS_CLIENT)
    altera_user = _user(store, altera, AlteraRole.ALTERA_ANALYST)
    client_user = _user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = store.create_project(
        name="Retailer",
        methodologies_enabled=methodologies,
        reporting_period_label="2024",
        organisation_id=client_org.id,
        created_by=altera_user.user_id,
    )
    return client_org, client_user, project


def test_report_endpoint_200_for_wwf_only_json_payload() -> None:
    store = InMemoryStore()
    client_org, client_user, project = _build_project(
        store, frozenset({Methodology.WWF})
    )
    run = _run_with_json_payload(
        store, project_id=project.id, org_id=client_org.id, methodology=Methodology.WWF
    )
    with _client(store, _auth(client_user, client_org)) as c:
        r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wwf_section"] is not None
    assert body["pt_section"] is None
    # Frontend-friendly JSON: decimals are strings, run_id a string, enum a value.
    assert body["wwf_section"]["total_in_scope_weight_kg"] == "175266.95000000"
    assert body["meta"]["run_id"] == str(run.id)
    assert body["meta"]["methodology"] == "wwf"


def test_report_endpoint_200_for_pt_only_json_payload() -> None:
    store = InMemoryStore()
    client_org, client_user, project = _build_project(
        store, frozenset({Methodology.PROTEIN_TRACKER})
    )
    run = _run_with_json_payload(
        store,
        project_id=project.id,
        org_id=client_org.id,
        methodology=Methodology.PROTEIN_TRACKER,
    )
    with _client(store, _auth(client_user, client_org)) as c:
        r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pt_section"] is not None
    assert body["wwf_section"] is None
    assert body["pt_section"]["plant_protein_kg"] == "60.12345000"
    assert body["meta"]["methodology"] == "protein_tracker"


def test_report_endpoint_200_for_pt_and_wwf_project_json_payloads() -> None:
    store = InMemoryStore()
    client_org, client_user, project = _build_project(
        store, frozenset({Methodology.PROTEIN_TRACKER, Methodology.WWF})
    )
    pt_run = _run_with_json_payload(
        store,
        project_id=project.id,
        org_id=client_org.id,
        methodology=Methodology.PROTEIN_TRACKER,
    )
    wwf_run = _run_with_json_payload(
        store, project_id=project.id, org_id=client_org.id, methodology=Methodology.WWF
    )
    with _client(store, _auth(client_user, client_org)) as c:
        r_pt = c.get(f"/api/v1/projects/{project.id}/runs/{pt_run.id}/report")
        r_wwf = c.get(f"/api/v1/projects/{project.id}/runs/{wwf_run.id}/report")
    assert r_pt.status_code == 200, r_pt.text
    assert r_wwf.status_code == 200, r_wwf.text
    assert r_pt.json()["pt_section"] is not None
    assert r_wwf.json()["wwf_section"] is not None

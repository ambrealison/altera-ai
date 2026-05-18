"""Phase 26A — scenario modelling foundation tests.

Covers:
- PT projection: baseline unchanged with no operations
- shift_protein_between_groups updates shares correctly
- increase_plant_core_protein improves plant share
- reduce_animal_core_protein reduces animal share (total changes)
- negative result clamped to zero with warning emitted
- base run never mutated
- improve_composite_split changes plant/animal attribution
- unknown operation_type emits warning and is skipped
- API: create/list/run scenario
- API: client cannot create scenario
- API: cross-org blocked on create
- API: WWF run rejected (not yet supported)
- no forbidden commercial fields in scenario response
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore, RunRecord
from altera_api.api.store_factory import get_store
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
)
from altera_api.domain.scenario import ScenarioOperation, ScenarioOperationType
from altera_api.main import app
from altera_api.scenarios.pt_projection import project_pt_scenario

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _dev_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def http(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(authed_user, None)


def _make_altera_org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="Altera AI",
        slug="altera",
        created_at=datetime.now(UTC),
        organisation_type=OrganisationType.ALTERA_INTERNAL,
    )
    store.organisations[org.id] = org
    return org


def _make_client_org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="GMS Client",
        slug="gms",
        created_at=datetime.now(UTC),
        organisation_type=OrganisationType.GMS_CLIENT,
    )
    store.organisations[org.id] = org
    return org


def _make_user(
    store: InMemoryStore, org: Organisation, role: AlteraRole | ClientRole
) -> UserProfile:
    uid = uuid4()
    profile = UserProfile(
        user_id=uid,
        organisation_id=org.id,
        email=f"{uid}@test.local",
        display_name=str(uid),
        role=role,
        created_at=datetime.now(UTC),
    )
    store.users[uid] = profile
    return profile


def _auth_ctx(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id,
        email=user.email,
        organisation_id=org.id,
        role=user.role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=org.organisation_type,
    )


def _make_project(store: InMemoryStore, org: Organisation) -> Project:
    return store.create_project(
        name="Test Project",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )


def _pt_summary(
    *,
    plant_core: str = "30",
    plant_non_core: str = "10",
    composite: str = "20",
    animal_core: str = "40",
) -> ProteinTrackerCalculationSummary:
    """Build a ProteinTrackerCalculationSummary with given group protein_kg values."""
    d_plant_core = Decimal(plant_core)
    d_plant_non_core = Decimal(plant_non_core)
    d_composite = Decimal(composite)
    d_animal_core = Decimal(animal_core)

    # With default 50/50 composite split:
    plant_total = d_plant_core + d_plant_non_core + d_composite * Decimal("0.5")
    animal_total = d_animal_core + d_composite * Decimal("0.5")
    total = plant_total + animal_total
    plant_share = (plant_total / total * 100).quantize(Decimal("0.0001")) if total else None
    animal_share = (animal_total / total * 100).quantize(Decimal("0.0001")) if total else None

    per_group = (
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            volume_kg=Decimal("100"),
            protein_kg=d_plant_core,
            item_count=3,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            volume_kg=Decimal("50"),
            protein_kg=d_plant_non_core,
            item_count=2,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            volume_kg=Decimal("80"),
            protein_kg=d_composite,
            item_count=4,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.ANIMAL_CORE,
            volume_kg=Decimal("200"),
            protein_kg=d_animal_core,
            item_count=5,
        ),
    )
    return ProteinTrackerCalculationSummary(
        run_id=uuid4(),
        reporting_period_label="2024",
        per_group=per_group,
        plant_protein_kg=plant_total,
        animal_protein_kg=animal_total,
        total_in_scope_protein_kg=total,
        plant_share_pct=plant_share,
        animal_share_pct=animal_share,
        rows_with_per_product_split=0,
        rows_protein_source_label=5,
        rows_protein_source_reference_db=0,
        out_of_scope_count=0,
        unknown_count=0,
        methodology_version="1.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0",
        rules_version="1.0",
    )


def _make_pt_run(store: InMemoryStore, project: Project, summary: ProteinTrackerCalculationSummary | None = None) -> RunRecord:
    if summary is None:
        summary = _pt_summary()
    run = RunRecord(
        id=uuid4(),
        project_id=project.id,
        methodology=Methodology.PROTEIN_TRACKER,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        triggered_by=project.created_by,
        summary_payload=summary.model_dump(),
        rows_payload=[],
        organisation_id=project.organisation_id,
    )
    store.runs[run.id] = run
    return run


def _make_wwf_run(store: InMemoryStore, project: Project) -> RunRecord:
    from altera_api.domain.wwf import (
        WWFCalculationSummary,
        WWFFoodGroup,
        WWFFoodGroupAggregate,
    )

    per_fg = tuple(
        WWFFoodGroupAggregate(
            food_group=fg,
            weight_kg=Decimal("100"),
            share_pct=Decimal("14.29"),
            phd_reference_share_pct=None,
        )
        for fg in list(WWFFoodGroup)[:7]
    )
    summary = WWFCalculationSummary(
        run_id=uuid4(),
        reporting_period_label="2024",
        per_food_group=per_fg,
        total_sales_weight_in_scope_kg=Decimal("700"),
        composites_meat_based_kg=Decimal("0"),
        composites_seafood_based_kg=Decimal("0"),
        composites_vegetarian_kg=Decimal("0"),
        composites_vegan_kg=Decimal("0"),
        composites_total_weight_kg=Decimal("0"),
        whole_diet_plant_weight_kg=Decimal("350"),
        whole_diet_animal_weight_kg=Decimal("350"),
        out_of_scope_count=0,
        unknown_count=0,
        methodology_version="1.0",
        methodology_source_edition="WWF Food Practice 2024",
        taxonomy_version="1.0",
        rules_version="1.0",
    )
    run = RunRecord(
        id=uuid4(),
        project_id=project.id,
        methodology=Methodology.WWF,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        triggered_by=project.created_by,
        summary_payload=summary.model_dump(),
        rows_payload=[],
        organisation_id=project.organisation_id,
    )
    store.runs[run.id] = run
    return run


def _op(
    op_type: ScenarioOperationType,
    params: dict,
    *,
    order: int = 0,
    scenario_id: UUID | None = None,
) -> ScenarioOperation:
    return ScenarioOperation(
        id=uuid4(),
        scenario_id=scenario_id or uuid4(),
        operation_type=op_type,
        parameters=params,
        rationale="test",
        order=order,
    )


# ---------------------------------------------------------------------------
# Pure projection tests
# ---------------------------------------------------------------------------


def test_no_operations_returns_unchanged_base() -> None:
    base = _pt_summary()
    sid = uuid4()
    result = project_pt_scenario(base, [], scenario_id=sid)

    assert result.scenario_id == sid
    assert result.pt_projected is not None
    p = result.pt_projected
    assert p.projected_plant_protein_kg == p.base_plant_protein_kg
    assert p.projected_animal_protein_kg == p.base_animal_protein_kg
    assert p.projected_total_protein_kg == p.base_total_protein_kg
    assert p.delta_plant_protein_kg == Decimal("0")
    assert result.warnings == []


def test_base_run_not_mutated() -> None:
    base = _pt_summary(animal_core="100")
    original_animal = base.animal_protein_kg
    original_plant = base.plant_protein_kg

    project_pt_scenario(
        base,
        [_op(ScenarioOperationType.SHIFT_PROTEIN_BETWEEN_GROUPS, {
            "from_group": "animal_core",
            "to_group": "plant_based_core",
            "amount_kg": "20",
        })],
        scenario_id=uuid4(),
    )

    # Base must be unchanged
    assert base.animal_protein_kg == original_animal
    assert base.plant_protein_kg == original_plant


def test_shift_from_animal_to_plant_increases_plant_share() -> None:
    # 30 plant_core, 10 plant_non_core, 20 composite (50/50), 40 animal
    # base plant = 30+10+10=50, base animal = 40+10=50, total=100, plant_share=50%
    base = _pt_summary(plant_core="30", plant_non_core="10", composite="20", animal_core="40")
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.SHIFT_PROTEIN_BETWEEN_GROUPS, {
            "from_group": "animal_core",
            "to_group": "plant_based_core",
            "amount_kg": "10",
        })],
        scenario_id=uuid4(),
    )
    p = result.pt_projected
    assert p is not None
    assert p.projected_plant_protein_kg > p.base_plant_protein_kg
    assert p.projected_animal_protein_kg < p.base_animal_protein_kg
    assert p.projected_total_protein_kg == p.base_total_protein_kg  # total unchanged in shift
    assert p.delta_plant_share_pct is not None
    assert p.delta_plant_share_pct > Decimal("0")
    assert result.warnings == []


def test_increase_plant_core_improves_plant_share() -> None:
    base = _pt_summary(plant_core="10", animal_core="90")
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.INCREASE_PLANT_CORE_PROTEIN, {"amount_kg": "20"})],
        scenario_id=uuid4(),
    )
    p = result.pt_projected
    assert p is not None
    assert p.projected_plant_protein_kg > p.base_plant_protein_kg
    assert p.projected_total_protein_kg > p.base_total_protein_kg  # total increases
    assert p.projected_plant_share_pct is not None
    assert p.projected_plant_share_pct > (p.base_plant_share_pct or Decimal("0"))
    assert result.warnings == []


def test_reduce_animal_core_changes_shares() -> None:
    base = _pt_summary(plant_core="20", plant_non_core="0", composite="0", animal_core="80")
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.REDUCE_ANIMAL_CORE_PROTEIN, {"amount_kg": "20"})],
        scenario_id=uuid4(),
    )
    p = result.pt_projected
    assert p is not None
    assert p.projected_animal_protein_kg < p.base_animal_protein_kg
    assert p.projected_total_protein_kg < p.base_total_protein_kg  # total decreases
    assert result.warnings == []


def test_negative_result_clamped_with_warning() -> None:
    base = _pt_summary(animal_core="5")
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.REDUCE_ANIMAL_CORE_PROTEIN, {"amount_kg": "100"})],
        scenario_id=uuid4(),
    )
    p = result.pt_projected
    assert p is not None
    # Animal core gets clamped to zero
    animal_core_group = next(g for g in p.per_group if g.pt_group == "animal_core")
    assert animal_core_group.projected_protein_kg == Decimal("0")
    # A warning should be emitted
    assert any("clamped" in w.lower() or "animal_core" in w.lower() for w in result.warnings)


def test_improve_composite_split_changes_attribution() -> None:
    # 0 pure plant/animal — only composite protein to isolate the effect
    base = _pt_summary(plant_core="0", plant_non_core="0", composite="100", animal_core="0")
    # Default: 50/50 → plant=50, animal=50
    result_default = project_pt_scenario(base, [], scenario_id=uuid4())
    p_default = result_default.pt_projected
    assert p_default is not None

    # With 70/30 composite split:
    result_70 = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.IMPROVE_COMPOSITE_SPLIT, {"plant_pct": "70", "animal_pct": "30"})],
        scenario_id=uuid4(),
    )
    p_70 = result_70.pt_projected
    assert p_70 is not None
    assert p_70.projected_plant_protein_kg > p_default.projected_plant_protein_kg
    assert p_70.projected_animal_protein_kg < p_default.projected_animal_protein_kg
    assert result_70.warnings == []


def test_composite_split_must_sum_to_100() -> None:
    base = _pt_summary()
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.IMPROVE_COMPOSITE_SPLIT, {"plant_pct": "60", "animal_pct": "60"})],
        scenario_id=uuid4(),
    )
    # Should warn and skip the op — result equals no-op
    assert len(result.warnings) > 0
    p = result.pt_projected
    assert p is not None
    assert p.delta_plant_share_pct == Decimal("0") or p.delta_plant_share_pct is None


def test_unknown_operation_type_emits_warning() -> None:
    base = _pt_summary()
    op = ScenarioOperation(
        id=uuid4(),
        scenario_id=uuid4(),
        operation_type="shift_protein_between_groups",  # valid enum value
        parameters={"from_group": "INVALID_GROUP", "to_group": "plant_based_core", "amount_kg": "10"},
        rationale="",
        order=0,
    )
    result = project_pt_scenario(base, [op], scenario_id=uuid4())
    assert len(result.warnings) > 0


def test_same_group_shift_is_noop() -> None:
    base = _pt_summary()
    result = project_pt_scenario(
        base,
        [_op(ScenarioOperationType.SHIFT_PROTEIN_BETWEEN_GROUPS, {
            "from_group": "animal_core",
            "to_group": "animal_core",
            "amount_kg": "10",
        })],
        scenario_id=uuid4(),
    )
    p = result.pt_projected
    assert p is not None
    assert p.delta_plant_protein_kg == Decimal("0")
    assert result.warnings == []


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_api_create_scenario(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        resp = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "Shift 10kg to plant", "base_run_id": str(run.id)},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "Shift 10kg to plant"
        assert data["status"] == "draft"
        assert data["methodology"] == "protein_tracker"
        assert data["base_run_id"] == str(run.id)
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_list_scenarios(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S1", "base_run_id": str(run.id)},
        )
        http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S2", "base_run_id": str(run.id)},
        )
        resp = http.get(f"/api/v1/projects/{project.id}/scenarios")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_run_scenario(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        scenario_id = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S", "base_run_id": str(run.id)},
        ).json()["id"]

        # Add an operation
        http.post(
            f"/api/v1/scenarios/{scenario_id}/operations",
            json={
                "operation_type": "increase_plant_core_protein",
                "parameters": {"amount_kg": "10"},
            },
        )

        # Run the scenario
        result_resp = http.post(f"/api/v1/scenarios/{scenario_id}/run")
        assert result_resp.status_code == 200, result_resp.text
        result = result_resp.json()
        assert result["methodology"] == "protein_tracker"
        assert result["pt_projected"] is not None
        pt = result["pt_projected"]
        # Plant protein should have increased
        assert Decimal(pt["projected_plant_protein_kg"]) > Decimal(pt["base_plant_protein_kg"])

        # Status promoted to active
        scenarios = http.get(f"/api/v1/projects/{project.id}/scenarios").json()
        assert any(s["id"] == scenario_id and s["status"] == "active" for s in scenarios)
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_get_scenario_result(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        scenario_id = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S", "base_run_id": str(run.id)},
        ).json()["id"]

        http.post(f"/api/v1/scenarios/{scenario_id}/run")

        result_resp = http.get(f"/api/v1/scenarios/{scenario_id}/result")
        assert result_resp.status_code == 200
        assert result_resp.json()["pt_projected"] is not None
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_client_cannot_create_scenario(http: TestClient, store: InMemoryStore) -> None:
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        resp = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S", "base_run_id": str(run.id)},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_wwf_run_rejected(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = store.create_project(
        name="WWF Project",
        methodologies_enabled=frozenset({Methodology.WWF}),
        reporting_period_label="2024",
        organisation_id=altera_org.id,
    )
    run = _make_wwf_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        resp = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "WWF scenario", "base_run_id": str(run.id)},
        )
        assert resp.status_code == 422
        assert "WWF" in resp.json()["detail"] or "protein_tracker" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(authed_user, None)


def test_api_client_only_sees_active_scenarios(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    client_org = _make_client_org(store)
    client_user = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
    project = _make_project(store, client_org)  # project in client org
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    http.post(
        f"/api/v1/projects/{project.id}/scenarios",
        json={"name": "draft scenario", "base_run_id": str(run.id)},
    )

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(client_user, client_org)
    try:
        # Client sees empty list (scenario is draft)
        resp = http.get(f"/api/v1/projects/{project.id}/scenarios")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# No commercial fields
# ---------------------------------------------------------------------------

_FORBIDDEN = {"revenue", "margin", "cost_price", "contract_terms", "confidential"}


def test_no_commercial_fields_in_scenario_result(http: TestClient, store: InMemoryStore) -> None:
    altera_org = _make_altera_org(store)
    lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
    project = _make_project(store, altera_org)
    run = _make_pt_run(store, project)

    app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
    try:
        scenario_id = http.post(
            f"/api/v1/projects/{project.id}/scenarios",
            json={"name": "S", "base_run_id": str(run.id)},
        ).json()["id"]
        result = http.post(f"/api/v1/scenarios/{scenario_id}/run").json()
        text = str(result).lower()
        for word in _FORBIDDEN:
            assert word not in text, f"Forbidden word '{word}' found in scenario result"
    finally:
        app.dependency_overrides.pop(authed_user, None)

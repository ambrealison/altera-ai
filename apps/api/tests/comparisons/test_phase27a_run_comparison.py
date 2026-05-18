"""Phase 27A — run comparison foundation tests.

Covers:
- PT comparison: plant/animal share deltas computed correctly
- PT comparison: per-group deltas correct
- PT comparison: direction "improving" when plant share rises
- PT comparison: direction "declining" when plant share falls
- PT comparison: direction "stable" when delta <= 0.1 pp
- PT comparison: methodology version mismatch emits warning
- PT comparison: taxonomy version mismatch emits warning
- PT comparison: rules version mismatch emits warning
- WWF comparison: weight and share deltas computed correctly
- WWF comparison: direction from plant fraction change
- API: PT comparison endpoint returns correct data
- API: same run_id returns 422
- API: mismatched methodologies returns 422
- API: run from different project returns 404
- API: client blocked without approved exports
- API: no commercial fields in response
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import ExportRecord, InMemoryStore, RunRecord
from altera_api.api.store_factory import get_store
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.comparisons.engine import (
    compare_pt_runs,
    compare_wwf_runs,
)
from altera_api.domain.common import AlteraRole, ClientRole, Methodology, OrganisationType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
)
from altera_api.main import app

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
    period: str = "2024",
    methodology_version: str = "1.0",
    taxonomy_version: str = "1.0",
    rules_version: str = "1.0",
) -> ProteinTrackerCalculationSummary:
    d_pc = Decimal(plant_core)
    d_pnc = Decimal(plant_non_core)
    d_comp = Decimal(composite)
    d_ac = Decimal(animal_core)

    plant_total = d_pc + d_pnc + d_comp * Decimal("0.5")
    animal_total = d_ac + d_comp * Decimal("0.5")
    total = plant_total + animal_total
    plant_share = (plant_total / total * 100).quantize(Decimal("0.0001")) if total else None
    animal_share = (animal_total / total * 100).quantize(Decimal("0.0001")) if total else None

    per_group = (
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            volume_kg=Decimal("100"),
            protein_kg=d_pc,
            item_count=3,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            volume_kg=Decimal("50"),
            protein_kg=d_pnc,
            item_count=2,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            volume_kg=Decimal("80"),
            protein_kg=d_comp,
            item_count=4,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.ANIMAL_CORE,
            volume_kg=Decimal("200"),
            protein_kg=d_ac,
            item_count=5,
        ),
    )
    return ProteinTrackerCalculationSummary(
        run_id=uuid4(),
        reporting_period_label=period,
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
        methodology_version=methodology_version,
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version=taxonomy_version,
        rules_version=rules_version,
    )


def _make_pt_run(
    store: InMemoryStore,
    project: Project,
    summary: ProteinTrackerCalculationSummary | None = None,
) -> RunRecord:
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


def _make_approved_export(store: InMemoryStore, run: RunRecord) -> ExportRecord:
    export = ExportRecord(
        id=uuid4(),
        run_id=run.id,
        organisation_id=run.organisation_id,
        format="json",
        status="success",
        storage_path="",
        filename="report.json",
        size_bytes=100,
        approval_status="approved",
        requested_by=None,
        approved_by=None,
        rejected_by=None,
        rejection_reason=None,
        under_review_by=None,
        delivered_by=None,
        approved_at=None,
        rejected_at=None,
        under_review_at=None,
        delivered_at=None,
        client_downloaded_at=None,
        client_download_count=0,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        sha256=None,
    )
    store.export_records[export.id] = export
    return export


# ---------------------------------------------------------------------------
# Pure engine tests
# ---------------------------------------------------------------------------


class TestComparePTRuns:
    def test_plant_protein_delta_correct(self) -> None:
        base = _pt_summary(plant_core="30", animal_core="70")
        comp = _pt_summary(plant_core="40", animal_core="70")
        result, _ = compare_pt_runs(base, comp)
        # With same composite=20, non_core=10:
        # base plant = 30+10+10 = 50, comp plant = 40+10+10 = 60
        assert result.delta_plant_protein_kg == comp.plant_protein_kg - base.plant_protein_kg

    def test_animal_protein_delta_correct(self) -> None:
        base = _pt_summary(animal_core="80")
        comp = _pt_summary(animal_core="60")
        result, _ = compare_pt_runs(base, comp)
        assert result.delta_animal_protein_kg == comp.animal_protein_kg - base.animal_protein_kg

    def test_share_delta_correct(self) -> None:
        base = _pt_summary(plant_core="30", animal_core="70")  # base plant share ~51.5%
        comp = _pt_summary(plant_core="50", animal_core="50")  # comp plant share higher
        result, _ = compare_pt_runs(base, comp)
        assert result.delta_plant_share_pct is not None
        expected_delta = comp.plant_share_pct - base.plant_share_pct  # type: ignore[operator]
        assert result.delta_plant_share_pct == expected_delta

    def test_per_group_deltas(self) -> None:
        base = _pt_summary(plant_core="30", animal_core="70")
        comp = _pt_summary(plant_core="50", animal_core="50")
        result, _ = compare_pt_runs(base, comp)
        by_group = {g.pt_group: g for g in result.per_group}
        assert by_group["plant_based_core"].delta_protein_kg == Decimal("20")
        assert by_group["animal_core"].delta_protein_kg == Decimal("-20")

    def test_direction_improving(self) -> None:
        base = _pt_summary(plant_core="20", animal_core="80")
        comp = _pt_summary(plant_core="40", animal_core="60")
        result, _ = compare_pt_runs(base, comp)
        assert result.direction == "improving"

    def test_direction_declining(self) -> None:
        base = _pt_summary(plant_core="40", animal_core="60")
        comp = _pt_summary(plant_core="20", animal_core="80")
        result, _ = compare_pt_runs(base, comp)
        assert result.direction == "declining"

    def test_direction_stable_within_threshold(self) -> None:
        # Same data → delta = 0 → stable
        base = _pt_summary()
        comp = _pt_summary()
        result, _ = compare_pt_runs(base, comp)
        assert result.direction == "stable"

    def test_methodology_version_mismatch_warning(self) -> None:
        base = _pt_summary(methodology_version="1.0")
        comp = _pt_summary(methodology_version="2.0")
        _, warnings = compare_pt_runs(base, comp)
        assert any("methodology version" in w.lower() for w in warnings)

    def test_taxonomy_version_mismatch_warning(self) -> None:
        base = _pt_summary(taxonomy_version="1.0")
        comp = _pt_summary(taxonomy_version="1.1")
        _, warnings = compare_pt_runs(base, comp)
        assert any("taxonomy version" in w.lower() for w in warnings)

    def test_rules_version_mismatch_warning(self) -> None:
        base = _pt_summary(rules_version="1.0")
        comp = _pt_summary(rules_version="0.9")
        _, warnings = compare_pt_runs(base, comp)
        assert any("rules version" in w.lower() for w in warnings)

    def test_no_warnings_when_versions_match(self) -> None:
        base = _pt_summary()
        comp = _pt_summary()
        _, warnings = compare_pt_runs(base, comp)
        assert warnings == []


class TestCompareWWFRuns:
    def _wwf_summary(
        self,
        *,
        plant_weight: str = "350",
        animal_weight: str = "350",
        period: str = "2024",
    ):
        from altera_api.domain.wwf import (
            WWFCalculationSummary,
            WWFFoodGroup,
            WWFFoodGroupAggregate,
        )

        total = Decimal(plant_weight) + Decimal(animal_weight)
        per_fg = tuple(
            WWFFoodGroupAggregate(
                food_group=fg,
                weight_kg=Decimal("100"),
                share_pct=Decimal("14.29"),
                phd_reference_share_pct=None,
            )
            for fg in list(WWFFoodGroup)[:7]
        )
        return WWFCalculationSummary(
            run_id=uuid4(),
            reporting_period_label=period,
            per_food_group=per_fg,
            total_sales_weight_in_scope_kg=total,
            composites_meat_based_kg=Decimal("0"),
            composites_seafood_based_kg=Decimal("0"),
            composites_vegetarian_kg=Decimal("0"),
            composites_vegan_kg=Decimal("0"),
            composites_total_weight_kg=Decimal("0"),
            whole_diet_plant_weight_kg=Decimal(plant_weight),
            whole_diet_animal_weight_kg=Decimal(animal_weight),
            out_of_scope_count=0,
            unknown_count=0,
            methodology_version="1.0",
            methodology_source_edition="WWF Food Practice 2024",
            taxonomy_version="1.0",
            rules_version="1.0",
        )

    def test_plant_delta_correct(self) -> None:
        base = self._wwf_summary(plant_weight="350")
        comp = self._wwf_summary(plant_weight="400", animal_weight="300")
        result, _ = compare_wwf_runs(base, comp)
        assert result.delta_plant_weight_kg == Decimal("50")

    def test_animal_delta_correct(self) -> None:
        base = self._wwf_summary(animal_weight="350")
        comp = self._wwf_summary(animal_weight="300")
        result, _ = compare_wwf_runs(base, comp)
        assert result.delta_animal_weight_kg == Decimal("-50")

    def test_direction_improving_when_plant_fraction_rises(self) -> None:
        base = self._wwf_summary(plant_weight="350", animal_weight="350")  # 50%
        comp = self._wwf_summary(plant_weight="450", animal_weight="250")  # 64%
        result, _ = compare_wwf_runs(base, comp)
        assert result.direction == "improving"

    def test_direction_declining_when_plant_fraction_falls(self) -> None:
        base = self._wwf_summary(plant_weight="450", animal_weight="250")  # 64%
        comp = self._wwf_summary(plant_weight="350", animal_weight="350")  # 50%
        result, _ = compare_wwf_runs(base, comp)
        assert result.direction == "declining"


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestRunComparisonAPI:
    def test_pt_comparison_returns_correct_deltas(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project = _make_project(store, altera_org)

        base_summary = _pt_summary(plant_core="30", animal_core="70", period="2023")
        comp_summary = _pt_summary(plant_core="50", animal_core="50", period="2024")
        run_a = _make_pt_run(store, project, base_summary)
        run_b = _make_pt_run(store, project, comp_summary)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["methodology"] == "protein_tracker"
        pt = body["pt_comparison"]
        assert pt is not None
        assert pt["baseline_reporting_period"] == "2023"
        assert pt["comparison_reporting_period"] == "2024"
        # Plant protein should have increased
        delta = float(pt["delta_plant_protein_kg"])
        assert delta > 0
        # Direction should be improving
        assert pt["direction"] == "improving"

    def test_same_run_returns_422(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project = _make_project(store, altera_org)
        run = _make_pt_run(store, project)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run.id}&comparison_run_id={run.id}"
        )
        assert resp.status_code == 422

    def test_mismatched_methodologies_returns_422(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project = store.create_project(
            name="Mixed",
            methodologies_enabled=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
            reporting_period_label="2024",
            organisation_id=altera_org.id,
        )
        run_pt = _make_pt_run(store, project)
        run_wwf = _make_wwf_run(store, project)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_pt.id}&comparison_run_id={run_wwf.id}"
        )
        assert resp.status_code == 422
        assert "different methodologies" in resp.json()["detail"]

    def test_run_from_different_project_returns_404(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project_a = _make_project(store, altera_org)
        project_b = _make_project(store, altera_org)
        run_a = _make_pt_run(store, project_a)
        run_b = _make_pt_run(store, project_b)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        # project_a in path, but run_b belongs to project_b
        resp = http.get(
            f"/api/v1/projects/{project_a.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 404

    def test_client_blocked_without_approved_exports(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        client_org = _make_client_org(store)
        viewer = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
        project = _make_project(store, client_org)
        run_a = _make_pt_run(store, project)
        run_b = _make_pt_run(store, project)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(viewer, client_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 403

    def test_client_allowed_with_approved_exports(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        client_org = _make_client_org(store)
        viewer = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
        project = _make_project(store, client_org)
        run_a = _make_pt_run(store, project, _pt_summary(period="2023"))
        run_b = _make_pt_run(store, project, _pt_summary(period="2024"))
        _make_approved_export(store, run_a)
        _make_approved_export(store, run_b)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(viewer, client_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 200

    def test_cross_org_blocked_for_client(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        client_org = _make_client_org(store)
        viewer = _make_user(store, client_org, ClientRole.CLIENT_VIEWER)
        project = _make_project(store, altera_org)  # project in altera org
        run_a = _make_pt_run(store, project)
        run_b = _make_pt_run(store, project)
        _make_approved_export(store, run_a)
        _make_approved_export(store, run_b)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(viewer, client_org)
        # Client from different org → get_project returns 404
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 404

    def test_no_commercial_fields_in_response(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project = _make_project(store, altera_org)
        run_a = _make_pt_run(store, project)
        run_b = _make_pt_run(store, project)

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        body = resp.text
        forbidden = [
            "product_name",
            "external_product_id",
            "brand",
            "retailer",
            "ean",
            "barcode",
        ]
        for f in forbidden:
            assert f not in body, f"commercial field {f!r} found in comparison response"

    def test_version_mismatch_warnings_in_response(
        self, store: InMemoryStore, http: TestClient
    ) -> None:
        altera_org = _make_altera_org(store)
        lead = _make_user(store, altera_org, AlteraRole.ALTERA_METHODOLOGY_LEAD)
        project = _make_project(store, altera_org)
        run_a = _make_pt_run(
            store, project, _pt_summary(methodology_version="1.0", period="2023")
        )
        run_b = _make_pt_run(
            store, project, _pt_summary(methodology_version="2.0", period="2024")
        )

        app.dependency_overrides[authed_user] = lambda: _auth_ctx(lead, altera_org)
        resp = http.get(
            f"/api/v1/projects/{project.id}/comparisons"
            f"?baseline_run_id={run_a.id}&comparison_run_id={run_b.id}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["warnings"]) > 0
        assert any("methodology version" in w.lower() for w in body["warnings"])

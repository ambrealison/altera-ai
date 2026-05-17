"""Cross-tenant isolation: a user in org A cannot see/touch org B's data."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.domain.common import Role
from altera_api.domain.organisation import Organisation, UserProfile
from tests.auth.conftest import TEST_JWT_SECRET

REPO_ROOT = Path(__file__).resolve().parents[4]
PT_TINY_CSV = (REPO_ROOT / "tests" / "fixtures" / "pt" / "pt_tiny.csv").read_bytes()


@pytest.fixture
def two_tenants(store: InMemoryStore) -> tuple[UUID, UUID, UUID, UUID]:
    """Create two organisations, each with one analyst user.

    Returns ``(user_a, org_a, user_b, org_b)``.
    """
    now = datetime.now(UTC)
    org_a = UUID("11111111-1111-1111-1111-111111111111")
    org_b = UUID("22222222-2222-2222-2222-222222222222")
    user_a = UUID("00000000-0000-0000-0000-000000000a01")
    user_b = UUID("00000000-0000-0000-0000-000000000a02")
    store.organisations[org_a] = Organisation(
        id=org_a, name="Org A", slug="org-a", created_at=now
    )
    store.organisations[org_b] = Organisation(
        id=org_b, name="Org B", slug="org-b", created_at=now
    )
    store.users[user_a] = UserProfile(
        user_id=user_a,
        organisation_id=org_a,
        email="alice@a.test",
        display_name="Alice",
        role=Role.ANALYST,
        created_at=now,
    )
    store.users[user_b] = UserProfile(
        user_id=user_b,
        organisation_id=org_b,
        email="bob@b.test",
        display_name="Bob",
        role=Role.ANALYST,
        created_at=now,
    )
    return user_a, org_a, user_b, org_b


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_project_for(
    client: TestClient,
    *,
    token: str,
    name: str,
    methodology: str = "protein_tracker",
) -> str:
    r = client.post(
        "/api/v1/projects",
        headers=_auth(token),
        json={
            "name": name,
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_user_cannot_see_other_org_projects_in_list(
    client: TestClient,
    store: InMemoryStore,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _org_a, user_b, _org_b = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a, email="alice@a.test")
    token_b = mint_token(sub=user_b, email="bob@b.test")
    _create_project_for(client, token=token_a, name="A's project")
    _create_project_for(client, token=token_b, name="B's project")

    r = client.get("/api/v1/projects", headers=_auth(token_a))
    assert r.status_code == 200
    items = r.json()
    assert {p["name"] for p in items} == {"A's project"}


def test_user_cannot_read_other_org_project_detail(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    b_project = _create_project_for(client, token=token_b, name="B project")
    r = client.get(f"/api/v1/projects/{b_project}", headers=_auth(token_a))
    # 404, not 403, so we don't leak the project's existence.
    assert r.status_code == 404


def test_user_cannot_upload_to_other_org_project(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    b_project = _create_project_for(client, token=token_b, name="B project")
    r = client.post(
        f"/api/v1/projects/{b_project}/uploads",
        headers=_auth(token_a),
        files={"file": ("pt_tiny.csv", PT_TINY_CSV, "text/csv")},
    )
    assert r.status_code == 404


def test_user_cannot_classify_other_org_upload(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    b_project = _create_project_for(client, token=token_b, name="B project")
    upload = client.post(
        f"/api/v1/projects/{b_project}/uploads",
        headers=_auth(token_b),
        files={"file": ("pt_tiny.csv", PT_TINY_CSV, "text/csv")},
    ).json()
    r = client.post(
        f"/api/v1/projects/{b_project}/uploads/{upload['id']}/classify",
        headers=_auth(token_a),
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 404


def test_user_cannot_review_other_org_items(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    b_project = _create_project_for(client, token=token_b, name="B project")
    r = client.get(f"/api/v1/projects/{b_project}/review", headers=_auth(token_a))
    assert r.status_code == 404


def test_user_cannot_run_calc_on_other_org_project(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    b_project = _create_project_for(client, token=token_b, name="B project")
    r = client.post(
        f"/api/v1/projects/{b_project}/runs",
        headers=_auth(token_a),
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 404


def test_user_cannot_export_other_org_run(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a, _, user_b, _ = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    token_b = mint_token(sub=user_b)
    # B creates a project, uploads, classifies, runs.
    b_project = _create_project_for(client, token=token_b, name="B project")
    upload = client.post(
        f"/api/v1/projects/{b_project}/uploads",
        headers=_auth(token_b),
        files={"file": ("pt_tiny.csv", PT_TINY_CSV, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{b_project}/uploads/{upload['id']}/classify",
        headers=_auth(token_b),
        json={"methodology": "protein_tracker"},
    )
    run = client.post(
        f"/api/v1/projects/{b_project}/runs",
        headers=_auth(token_b),
        json={"methodology": "protein_tracker"},
    ).json()
    # A tries to download.
    r = client.get(
        f"/api/v1/projects/{b_project}/runs/{run['id']}/export?fmt=json",
        headers=_auth(token_a),
    )
    assert r.status_code == 404


def test_auto_provisioned_new_user_does_not_inherit_other_orgs(
    client: TestClient,
    two_tenants: tuple[UUID, UUID, UUID, UUID],
    mint_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brand-new Supabase user not in the in-memory store at all
    gets auto-provisioned on the *demo* organisation, not on any
    existing tenant's org. So they cannot see Org A or Org B data."""
    user_a, org_a, _user_b, _org_b = two_tenants
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    token_a = mint_token(sub=user_a)
    a_project = _create_project_for(client, token=token_a, name="A project")

    newcomer = uuid4()
    token_new = mint_token(sub=newcomer, email="newcomer@altera-ai.local")
    r = client.get("/api/v1/projects", headers=_auth(token_new))
    assert r.status_code == 200
    # Newcomer landed on demo org, not Org A.
    for entry in r.json():
        assert UUID(entry["organisation_id"]) != org_a
    # And cannot see A's project.
    r = client.get(f"/api/v1/projects/{a_project}", headers=_auth(token_new))
    assert r.status_code == 404

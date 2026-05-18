"""Unit-level tests for the auth dependency."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.domain.common import Role
from tests.auth.conftest import TEST_JWT_SECRET


class TestNoAuthConfigured:
    def test_missing_token_rejected_when_dev_disabled(self, client: TestClient) -> None:
        r = client.get("/api/v1/me")
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_invalid_token_rejected(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        r = client.get("/api/v1/me", headers={"Authorization": "Bearer this-is-not-a-jwt"})
        assert r.status_code == 401
        assert "invalid token" in r.json()["detail"].lower()

    def test_token_with_wrong_signature_rejected(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        wrong_secret_token = jwt.encode(
            {
                "sub": str(uuid4()),
                "aud": "authenticated",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            "different-secret-also-32-bytes-long!!!!!",
            algorithm="HS256",
        )
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {wrong_secret_token}"})
        assert r.status_code == 401

    def test_expired_token_rejected(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        expired = jwt.encode(
            {
                "sub": str(uuid4()),
                "aud": "authenticated",
                "exp": int((datetime.now(UTC) - timedelta(seconds=10)).timestamp()),
            },
            TEST_JWT_SECRET,
            algorithm="HS256",
        )
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401
        assert "expired" in r.json()["detail"].lower()

    def test_token_without_secret_configured_rejected(self, client: TestClient) -> None:
        # SUPABASE_JWT_SECRET not set → can't verify any token → 401.
        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "aud": "authenticated",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            "any-secret-with-enough-length-32bytes!!!",
            algorithm="HS256",
        )
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


class TestValidToken:
    def test_valid_token_returns_user(
        self,
        client: TestClient,
        store: InMemoryStore,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        user_id = store.default_user_id  # the demo user
        token = mint_token(sub=user_id, email="demo@altera-ai.local")
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == str(user_id)
        assert body["email"] == "demo@altera-ai.local"
        assert body["auth_provider"] == "supabase"
        assert body["is_dev_auth"] is False
        assert UUID(body["organisation_id"]) == store.default_org_id

    def test_auto_provisions_unknown_user(
        self,
        client: TestClient,
        store: InMemoryStore,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A new Supabase user signs in for the first time; the
        # in-memory store has no record. We auto-provision them on
        # the demo organisation so local dev works without manual
        # SQL inserts.
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        new_user = uuid4()
        token = mint_token(sub=new_user, email="alice@altera-ai.local")
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == str(new_user)
        assert body["email"] == "alice@altera-ai.local"
        # The new user lands on the demo org with owner role.
        assert UUID(body["organisation_id"]) == store.default_org_id
        # And is recorded for subsequent requests.
        assert new_user in store.users


class TestDevFallback:
    def test_dev_disabled_by_default(self, client: TestClient) -> None:
        # No Authorization header and dev not enabled → 401.
        r = client.get("/api/v1/me")
        assert r.status_code == 401

    def test_dev_enabled_lets_anon_request_through(
        self, client: TestClient, store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
        r = client.get("/api/v1/me")
        assert r.status_code == 200
        body = r.json()
        assert body["auth_provider"] == "dev"
        assert body["is_dev_auth"] is True
        assert UUID(body["user_id"]) == store.default_user_id

    def test_dev_user_id_override(
        self, client: TestClient, store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
        forced = uuid4()
        monkeypatch.setenv("ALTERA_DEV_USER_ID", str(forced))
        monkeypatch.setenv("ALTERA_DEV_USER_EMAIL", "ops@altera-ai.local")
        r = client.get("/api/v1/me")
        assert r.status_code == 200
        assert UUID(r.json()["user_id"]) == forced
        assert r.json()["email"] == "ops@altera-ai.local"

    def test_dev_fallback_only_when_no_token_present(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If a bad token is sent, dev mode does NOT silently fall back.
        # That hides bad-token bugs from devs running with the
        # fallback enabled.
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        r = client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401

    def test_dev_fallback_explicitly_disabled(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "false")
        r = client.get("/api/v1/me")
        assert r.status_code == 401


class TestAuthContextAttachedToRequest:
    def test_create_project_uses_caller_organisation(
        self,
        client: TestClient,
        store: InMemoryStore,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        token = mint_token(sub=store.default_user_id)
        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Scoped Project",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        assert r.status_code == 201
        # The project is owned by the caller's org, not silently the
        # store's "first org".
        assert UUID(r.json()["organisation_id"]) == store.default_org_id


class TestRoleCheck:
    def test_viewer_cannot_create_project(
        self,
        client: TestClient,
        store: InMemoryStore,
        mint_token: Callable[..., str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        # Set up: pre-populate a viewer user in the demo org.
        viewer_id = uuid4()
        from altera_api.domain.organisation import UserProfile

        store.users[viewer_id] = UserProfile(
            user_id=viewer_id,
            organisation_id=store.default_org_id,
            email="viewer@altera-ai.local",
            display_name="Viewer",
            role=Role.VIEWER,
            created_at=datetime.now(UTC),
        )
        token = mint_token(sub=viewer_id, email="viewer@altera-ai.local")
        r = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Viewer Attempt",
                "methodologies_enabled": ["protein_tracker"],
                "reporting_period_label": "FY 2024",
            },
        )
        assert r.status_code == 403
        assert "analyst" in r.json()["detail"]


# This is the contract assertion the user explicitly requested.
def test_dev_auth_disabled_by_default_when_env_unset(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401


_ = (Any,)  # keep imports honest for narrow tooling

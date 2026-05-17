"""Fixtures for the auth test suite.

Unlike ``tests/api/conftest.py``, dev auth is **disabled** by default
here. Each test toggles env explicitly so the matrix
(token-present / dev-on / both-off) is readable.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.main import app

# 32+ byte HMAC-SHA256 key per RFC 7518 §3.2 — silences the
# pyjwt InsecureKeyLengthWarning in test output.
TEST_JWT_SECRET = "test-secret-not-for-production-32bytes!!"


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip auth-related env vars at the start of every test.

    Tests set what they need; this fixture stops the host environment
    from leaking real secrets into a test run."""
    for name in (
        "SUPABASE_URL",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_SERVICE_ROLE_KEY",
        "ALTERA_DEV_AUTH_ENABLED",
        "ALTERA_DEV_USER_ID",
        "ALTERA_DEV_ORGANISATION_ID",
        "ALTERA_DEV_USER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


@pytest.fixture
def mint_token() -> Callable[..., str]:
    """Return a function that mints a Supabase-shaped JWT.

    Tests pass `sub` and `email`; the function sets exp + audience to
    Supabase defaults. The caller is responsible for setting
    ``SUPABASE_JWT_SECRET`` to :data:`TEST_JWT_SECRET` so the
    verifier accepts the resulting token.
    """

    def _mint(
        *,
        sub: UUID,
        email: str = "user@test.local",
        audience: str = "authenticated",
        expires_in_seconds: int = 3600,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": str(sub),
            "email": email,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
            "role": "authenticated",
        }
        if extra_claims:
            claims.update(extra_claims)
        return jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")

    return _mint

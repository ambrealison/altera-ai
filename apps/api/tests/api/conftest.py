"""Shared fixtures for HTTP API tests.

Each test gets a fresh ``InMemoryStore`` via FastAPI dependency override
so tests are isolated. We enable the dev-auth fallback for the duration
of every test in this directory so the existing Phase 12 flows
(designed before real auth) continue to work without modification.

Tests that exercise real Supabase JWT verification live under
``tests/auth/`` and configure their environment explicitly.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.main import app

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _enable_dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable dev-auth for every test in this directory.

    Without this, every Phase 12 happy-path test would have to mint
    its own JWT — adding noise for no signal. Auth-specific tests
    under ``tests/auth/`` toggle the flag off explicitly.
    """
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    # Clear any Supabase secret the host environment may have set so
    # the dev fallback runs deterministically.
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


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
def pt_tiny_csv() -> bytes:
    return (FIXTURE_ROOT / "pt" / "pt_tiny.csv").read_bytes()


@pytest.fixture
def wwf_tiny_csv() -> bytes:
    return (FIXTURE_ROOT / "wwf" / "wwf_tiny.csv").read_bytes()

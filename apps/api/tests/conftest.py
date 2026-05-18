"""Root-level conftest for the altera-api test suite.

Sets a safe ``CORS_ALLOWED_ORIGINS`` for all tests so the production
fail-closed CORS check (called in lifespan) does not raise when tests
use ``with TestClient(app) as c:`` without explicitly configuring CORS.
Individual test suites may override this for CORS-specific tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _cors_test_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure CORS_ALLOWED_ORIGINS is set for every test.

    Without this, tests that trigger the FastAPI lifespan (via ``with
    TestClient(app) as c:``) would fail the production CORS check if
    ``ALTERA_DEV_AUTH_ENABLED`` is also unset (as it is in auth tests).
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

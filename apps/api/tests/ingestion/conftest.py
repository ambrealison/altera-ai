"""Shared fixtures for ingestion tests."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

#: Repo root, four levels up from this test file.
REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    assert FIXTURE_ROOT.is_dir(), f"fixture root not found: {FIXTURE_ROOT}"
    return FIXTURE_ROOT


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def upload_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def project_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")

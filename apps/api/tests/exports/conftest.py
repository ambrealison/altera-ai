"""Shared fixtures for export tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def today() -> date:
    return date(2026, 5, 15)


@pytest.fixture
def run_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000abc")


@pytest.fixture
def upload_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def project_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-0000000000a1")

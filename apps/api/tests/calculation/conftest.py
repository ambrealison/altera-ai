"""Shared fixtures + helpers for calculation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.calculation import PTRunVersions

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def run_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000abc")


@pytest.fixture
def project_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def upload_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def pt_versions() -> PTRunVersions:
    return PTRunVersions(
        methodology_version="1.0.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0.0",
        rules_version="1.0.0",
    )


@pytest.fixture
def split_decimal() -> Decimal:
    return Decimal("0.00000001")

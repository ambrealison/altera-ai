"""Shared fixtures for domain tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def project_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def upload_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def product_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000004")


@pytest.fixture
def user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000005")


@pytest.fixture
def run_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000006")

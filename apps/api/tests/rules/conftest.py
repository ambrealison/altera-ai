"""Shared fixtures for rules-engine tests."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def _ids(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


@pytest.fixture
def make_pt_product(now: datetime):
    """Factory for a PT-only NormalizedProduct."""

    def _make(
        *,
        name: str,
        brand: str | None = None,
        labels: tuple[str, ...] = (),
        retailer_category: str | None = None,
        retailer_subcategory: str | None = None,
        ingredients_text: str | None = None,
        language: str | None = None,
        country: str | None = None,
        external_id: str = "P-001",
        product_uid: int = 1,
    ) -> NormalizedProduct:
        return NormalizedProduct(
            id=_ids(product_uid),
            upload_id=_ids(101),
            project_id=_ids(102),
            organisation_id=_ids(103),
            row_number=1,
            external_product_id=external_id,
            product_name=name,
            brand=brand,
            labels=labels,
            retailer_category=retailer_category,
            retailer_subcategory=retailer_subcategory,
            ingredients_text=ingredients_text,
            language=language,
            country=country,
            weight_per_item_kg=Decimal("0.400"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(
                items_purchased=Decimal("100"),
                protein_pct=Decimal("10"),
                protein_source=ProteinSource.REFERENCE_DB,
            ),
            created_at=now,
        )

    return _make


@pytest.fixture
def make_wwf_product(now: datetime):
    """Factory for a WWF-only NormalizedProduct."""

    def _make(
        *,
        name: str,
        is_own_brand: bool = False,
        brand: str | None = None,
        labels: tuple[str, ...] = (),
        retailer_category: str | None = None,
        retailer_subcategory: str | None = None,
        ingredients_text: str | None = None,
        retail_channel: RetailChannel = RetailChannel.GROCERY_AMBIENT,
        language: str | None = None,
        country: str | None = None,
        external_id: str = "W-001",
        product_uid: int = 2,
    ) -> NormalizedProduct:
        return NormalizedProduct(
            id=_ids(product_uid),
            upload_id=_ids(201),
            project_id=_ids(202),
            organisation_id=_ids(203),
            row_number=1,
            external_product_id=external_id,
            product_name=name,
            brand=brand,
            is_own_brand=is_own_brand,
            labels=labels,
            retailer_category=retailer_category,
            retailer_subcategory=retailer_subcategory,
            ingredients_text=ingredients_text,
            language=language,
            country=country,
            weight_per_item_kg=Decimal("0.400"),
            methodologies_enabled=frozenset({Methodology.WWF}),
            wwf_fields=WWFProductFields(
                items_sold=Decimal("100"),
                retail_channel=retail_channel,
                is_own_brand=is_own_brand,
            ),
            created_at=now,
        )

    return _make

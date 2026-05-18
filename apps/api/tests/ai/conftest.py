"""Shared fixtures for AI-classifier tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
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


def _ids(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def pt_product(now: datetime) -> NormalizedProduct:
    return NormalizedProduct(
        id=_ids(1),
        upload_id=_ids(101),
        project_id=_ids(102),
        organisation_id=_ids(103),
        row_number=1,
        external_product_id="P-001",
        product_name="Mystery Foodservice Item",
        brand="Unknown Brand",
        retailer_category="Ready Meals",
        retailer_subcategory="Speciality",
        ingredients_text="various ingredients",
        labels=("organic",),
        language="en",
        country="GB",
        weight_per_item_kg=Decimal("0.400"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=Decimal("10"),
            protein_source=ProteinSource.REFERENCE_DB,
        ),
        created_at=now,
    )


@pytest.fixture
def wwf_product(now: datetime) -> NormalizedProduct:
    return NormalizedProduct(
        id=_ids(2),
        upload_id=_ids(201),
        project_id=_ids(202),
        organisation_id=_ids(203),
        row_number=1,
        external_product_id="W-001",
        product_name="Mystery Ready Meal",
        brand="Unknown Brand",
        is_own_brand=True,
        retailer_category="Ready Meals",
        retailer_subcategory="Speciality",
        labels=(),
        language="en",
        country="GB",
        weight_per_item_kg=Decimal("0.350"),
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=True,
        ),
        created_at=now,
    )

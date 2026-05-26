from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.product import ProteinSource, RawProduct, RetailChannel
from altera_api.ingestion.normalizer import normalize_product


def _raw_full(upload_id: UUID) -> RawProduct:
    return RawProduct(
        upload_id=upload_id,
        row_number=1,
        external_product_id="P-001",
        product_name="Red Lentil Soup",
        is_own_brand=True,
        retail_channel=RetailChannel.GROCERY_AMBIENT,
        weight_per_item_kg=Decimal("0.400"),
        items_purchased=Decimal("1200"),
        items_sold=Decimal("1100"),
        protein_pct=Decimal("4.5"),
        protein_source=ProteinSource.REFERENCE_DB,
    )


def test_pt_only_happy_path(upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime) -> None:
    raw = _raw_full(upload_id)
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert errors == ()
    assert product is not None
    assert product.pt_fields is not None
    assert product.pt_fields.protein_pct == Decimal("4.5")
    assert product.wwf_fields is None


def test_wwf_only_happy_path(
    upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
) -> None:
    raw = _raw_full(upload_id)
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=now,
    )
    assert errors == ()
    assert product is not None
    assert product.wwf_fields is not None
    assert product.wwf_fields.retail_channel is RetailChannel.GROCERY_AMBIENT
    assert product.pt_fields is None


def test_both_methodologies_happy_path(
    upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
) -> None:
    raw = _raw_full(upload_id)
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER, Methodology.WWF}),
        now=now,
    )
    assert errors == ()
    assert product is not None
    assert product.pt_fields is not None and product.wwf_fields is not None


def test_wwf_missing_items_sold(
    upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
) -> None:
    """Phase WWF-I-hotfix — for a WWF-only project, a row missing
    ``items_sold`` produces a missing-field warning AND a row-level
    ``no_methodology_satisfiable`` error (no methodology can be
    applied), so the row is excluded with a structured explanation.

    The CSV is NOT silently emptied (the regression that hit PT+WWF
    projects); each broken row carries the precise reason.
    """
    raw = _raw_full(upload_id).model_copy(update={"items_sold": None})
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=now,
    )
    assert product is None
    assert any(
        w.code == "missing_for_methodology" and w.field == "items_sold"
        for w in warnings
    )
    assert any(e.code == "no_methodology_satisfiable" for e in errors)


def test_pt_missing_protein_pct_yields_warning(
    upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
) -> None:
    """Missing protein_pct produces a warning and the product is still created."""
    raw = _raw_full(upload_id).model_copy(update={"protein_pct": None})
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert errors == ()
    assert product is not None
    assert product.pt_fields is not None
    assert product.pt_fields.protein_pct is None
    assert any(
        w.code == "enrichment_needed" and w.field == "protein_pct" for w in warnings
    )


def test_missing_weight_blocks(
    upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
) -> None:
    raw = _raw_full(upload_id).model_copy(update={"weight_per_item_kg": None})
    product, errors, warnings = normalize_product(
        raw,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert product is None
    assert any(e.code == "missing_required" and e.field == "weight_per_item_kg" for e in errors)

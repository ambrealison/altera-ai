"""RawProduct → NormalizedProduct.

Applies methodology-aware requirements to a parsed row and produces a
persistent ``NormalizedProduct`` (or row-level validation errors when
methodology-required fields are missing).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RawProduct,
    WWFProductFields,
)
from altera_api.domain.validation import ValidationError


def _missing(row_number: int, field: str, methodology: Methodology) -> ValidationError:
    return ValidationError(
        row_number=row_number,
        field=field,
        code="missing_for_methodology",
        message=f"{field} is required when methodology={methodology.value} is enabled",
    )


def normalize_product(
    raw: RawProduct,
    *,
    project_id: UUID,
    organisation_id: UUID,
    methodologies_enabled: frozenset[Methodology],
    now: datetime,
    product_id: UUID | None = None,
) -> tuple[NormalizedProduct | None, tuple[ValidationError, ...]]:
    """Build a ``NormalizedProduct`` from a ``RawProduct``.

    Returns ``(product, errors)``. If errors is non-empty, ``product`` is
    ``None`` — the row is excluded from the normalised set.
    """
    errors: list[ValidationError] = []

    if raw.weight_per_item_kg is None:
        errors.append(
            ValidationError(
                row_number=raw.row_number,
                field="weight_per_item_kg",
                code="missing_required",
                message="weight_per_item_kg is required",
            )
        )

    pt_fields: PTProductFields | None = None
    wwf_fields: WWFProductFields | None = None

    if Methodology.PROTEIN_TRACKER in methodologies_enabled:
        if raw.items_purchased is None:
            errors.append(_missing(raw.row_number, "items_purchased", Methodology.PROTEIN_TRACKER))
        if raw.protein_pct is None:
            errors.append(_missing(raw.row_number, "protein_pct", Methodology.PROTEIN_TRACKER))
        if raw.items_purchased is not None and raw.protein_pct is not None:
            try:
                pt_fields = PTProductFields(
                    items_purchased=raw.items_purchased,
                    protein_pct=raw.protein_pct,
                    protein_source=raw.protein_source or ProteinSource.REFERENCE_DB,
                    plant_protein_pct=raw.plant_protein_pct,
                    animal_protein_pct=raw.animal_protein_pct,
                )
            except PydanticValidationError as exc:
                for err in exc.errors():
                    errors.append(
                        ValidationError(
                            row_number=raw.row_number,
                            field=".".join(str(p) for p in err.get("loc", ())) or None,
                            code=err["type"],
                            message=err["msg"],
                        )
                    )

    if Methodology.WWF in methodologies_enabled:
        if raw.items_sold is None:
            errors.append(_missing(raw.row_number, "items_sold", Methodology.WWF))
        if raw.retail_channel is None:
            errors.append(_missing(raw.row_number, "retail_channel", Methodology.WWF))
        if raw.is_own_brand is None:
            errors.append(_missing(raw.row_number, "is_own_brand", Methodology.WWF))
        if (
            raw.items_sold is not None
            and raw.retail_channel is not None
            and raw.is_own_brand is not None
        ):
            wwf_fields = WWFProductFields(
                items_sold=raw.items_sold,
                retail_channel=raw.retail_channel,
                is_own_brand=raw.is_own_brand,
            )

    if errors:
        return None, tuple(errors)

    assert raw.weight_per_item_kg is not None  # checked above

    try:
        product = NormalizedProduct(
            id=product_id or uuid4(),
            upload_id=raw.upload_id,
            project_id=project_id,
            organisation_id=organisation_id,
            row_number=raw.row_number,
            external_product_id=raw.external_product_id,
            product_name=raw.product_name,
            brand=raw.brand,
            is_own_brand=raw.is_own_brand,
            retailer_category=raw.retailer_category,
            retailer_subcategory=raw.retailer_subcategory,
            ingredients_text=raw.ingredients_text,
            labels=raw.labels,
            language=raw.language,
            country=raw.country,
            weight_per_item_kg=raw.weight_per_item_kg,
            methodologies_enabled=methodologies_enabled,
            pt_fields=pt_fields,
            wwf_fields=wwf_fields,
            created_at=now,
        )
    except PydanticValidationError as exc:
        for err in exc.errors():
            errors.append(
                ValidationError(
                    row_number=raw.row_number,
                    field=".".join(str(p) for p in err.get("loc", ())) or None,
                    code=err["type"],
                    message=err["msg"],
                )
            )
        return None, tuple(errors)

    return product, ()

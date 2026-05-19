"""Row → RawProduct parser.

Takes a header-keyed dict whose values are still strings (CSV cells)
and produces a ``RawProduct`` plus zero or more validation entries.
The parser is methodology-agnostic — methodology requirements are the
normalizer's job.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.product import ProteinSource, RawProduct, RetailChannel
from altera_api.domain.validation import ValidationError, ValidationWarning
from altera_api.ingestion.units import (
    _coerce_decimal,
    normalise_protein_pct,
    normalise_weight_kg,
)


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s == "":
        return None
    if s in {"true", "1", "yes", "y", "oui", "o", "vrai"}:
        return True
    if s in {"false", "0", "no", "n", "non", "faux"}:
        return False
    return None  # caller treats as missing; downstream may emit an error


def _coerce_tuple(value: Any, *, sep: str = "|") -> tuple[str, ...]:
    if value is None:
        return ()
    s = str(value).strip()
    if s == "":
        return ()
    return tuple(part.strip() for part in s.split(sep) if part.strip())


def _coerce_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def parse_row(
    row: dict[str, Any],
    *,
    upload_id: UUID,
    row_number: int,
) -> tuple[RawProduct | None, tuple[ValidationError, ...], tuple[ValidationWarning, ...]]:
    """Parse one normalised-header row into a ``RawProduct``.

    Returns ``(raw_product, errors, warnings)``. If errors is non-empty,
    ``raw_product`` is ``None`` and the row is dropped from the upload.
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    # --- Required string fields ---
    external_product_id = _coerce_str_or_none(row.get("external_product_id"))
    product_name = _coerce_str_or_none(row.get("product_name"))
    if not external_product_id:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="external_product_id",
                code="missing_required",
                message="external_product_id is required",
            )
        )
    if not product_name:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="product_name",
                code="missing_required",
                message="product_name is required",
            )
        )

    # --- Weight (canonical kg, with g/lb/oz accepted) ---
    weight_kg, weight_err = normalise_weight_kg(row)
    if weight_err is not None:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="weight_per_item_kg",
                code=weight_err,
                message=f"weight_per_item_kg: {weight_err}",
            )
        )

    # --- Protein (PT-only field; missing is allowed at parse time) ---
    protein_pct, protein_err = normalise_protein_pct(row)
    if protein_err is not None:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="protein_pct",
                code=protein_err,
                message=f"protein_pct: {protein_err}",
            )
        )

    # --- Numeric counts (items_purchased / items_sold) ---
    items_purchased = _coerce_decimal(row.get("items_purchased"))
    if items_purchased is not None and items_purchased.is_nan():
        errors.append(
            ValidationError(
                row_number=row_number,
                field="items_purchased",
                code="invalid_type",
                message="items_purchased is not numeric",
            )
        )
        items_purchased = None
    elif items_purchased is not None and items_purchased < 0:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="items_purchased",
                code="invalid_range",
                message="items_purchased must be >= 0",
            )
        )
        items_purchased = None
    elif items_purchased is not None and items_purchased != items_purchased.to_integral_value():
        warnings.append(
            ValidationWarning(
                row_number=row_number,
                field="items_purchased",
                code="non_integer_count",
                message="items_purchased has a fractional part; will be truncated",
            )
        )
        items_purchased = Decimal(int(items_purchased))

    items_sold = _coerce_decimal(row.get("items_sold"))
    if items_sold is not None and items_sold.is_nan():
        errors.append(
            ValidationError(
                row_number=row_number,
                field="items_sold",
                code="invalid_type",
                message="items_sold is not numeric",
            )
        )
        items_sold = None
    elif items_sold is not None and items_sold < 0:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="items_sold",
                code="invalid_range",
                message="items_sold must be >= 0",
            )
        )
        items_sold = None
    elif items_sold is not None and items_sold != items_sold.to_integral_value():
        warnings.append(
            ValidationWarning(
                row_number=row_number,
                field="items_sold",
                code="non_integer_count",
                message="items_sold has a fractional part; will be truncated",
            )
        )
        items_sold = Decimal(int(items_sold))

    # --- Per-product PT split (composite extension) ---
    plant_protein_pct, _ = _decimal_or_error(
        row.get("plant_protein_pct"), errors, row_number, "plant_protein_pct"
    )
    animal_protein_pct, _ = _decimal_or_error(
        row.get("animal_protein_pct"), errors, row_number, "animal_protein_pct"
    )
    for value, field in (
        (plant_protein_pct, "plant_protein_pct"),
        (animal_protein_pct, "animal_protein_pct"),
    ):
        if value is not None and (value < 0 or value > Decimal("100")):
            errors.append(
                ValidationError(
                    row_number=row_number,
                    field=field,
                    code="invalid_range",
                    message=f"{field} must be in [0, 100]",
                )
            )

    # --- Optional / enum / boolean fields ---
    is_own_brand = _coerce_bool(row.get("is_own_brand"))
    if row.get("is_own_brand") not in (None, "") and is_own_brand is None:
        errors.append(
            ValidationError(
                row_number=row_number,
                field="is_own_brand",
                code="invalid_type",
                message="is_own_brand must be true/false",
            )
        )

    retail_channel = _enum_or_error(
        row.get("retail_channel"), RetailChannel, errors, row_number, "retail_channel"
    )
    protein_source = _enum_or_error(
        row.get("protein_source"), ProteinSource, errors, row_number, "protein_source"
    )

    language = _coerce_str_or_none(row.get("language"))
    if language is not None:
        language = language.lower()
    country = _coerce_str_or_none(row.get("country"))
    if country is not None:
        country = country.upper()

    labels = _coerce_tuple(row.get("labels"))

    if errors:
        return None, tuple(errors), tuple(warnings)

    try:
        raw = RawProduct(
            upload_id=upload_id,
            row_number=row_number,
            external_product_id=external_product_id or "",
            product_name=product_name or "",
            brand=_coerce_str_or_none(row.get("brand")),
            is_own_brand=is_own_brand,
            retailer_category=_coerce_str_or_none(row.get("retailer_category")),
            retailer_subcategory=_coerce_str_or_none(row.get("retailer_subcategory")),
            ingredients_text=_coerce_str_or_none(row.get("ingredients_text")),
            labels=labels,
            language=language,
            country=country,
            retail_channel=retail_channel,
            weight_per_item_kg=weight_kg,
            items_purchased=items_purchased,
            items_sold=items_sold,
            protein_pct=protein_pct,
            protein_source=protein_source,
            plant_protein_pct=plant_protein_pct,
            animal_protein_pct=animal_protein_pct,
        )
    except PydanticValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            errors.append(
                ValidationError(
                    row_number=row_number,
                    field=loc or None,
                    code=err["type"],
                    message=err["msg"],
                )
            )
        return None, tuple(errors), tuple(warnings)

    return raw, tuple(errors), tuple(warnings)


def _decimal_or_error(
    value: Any,
    errors: list[ValidationError],
    row_number: int,
    field: str,
) -> tuple[Decimal | None, bool]:
    coerced = _coerce_decimal(value)
    if coerced is not None and coerced.is_nan():
        errors.append(
            ValidationError(
                row_number=row_number,
                field=field,
                code="invalid_type",
                message=f"{field} is not numeric",
            )
        )
        return None, True
    return coerced, False


_E = TypeVar("_E", bound=Enum)


def _enum_or_error(
    value: Any,
    enum_cls: type[_E],
    errors: list[ValidationError],
    row_number: int,
    field: str,
) -> _E | None:
    raw = _coerce_str_or_none(value)
    if raw is None:
        return None
    try:
        return enum_cls(raw)
    except ValueError:
        errors.append(
            ValidationError(
                row_number=row_number,
                field=field,
                code="invalid_enum",
                message=f"{field}={raw!r} is not a valid {enum_cls.__name__}",
            )
        )
        return None

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

#: Phase WWF-I-hotfix2 — brand-type words that map to is_own_brand=True.
#: Many retailer exports use full English/French words instead of
#: boolean literals (e.g. "Own brand", "Private label", "Marque
#: distributeur"). Without this map the parser rejected every row
#: of a perfectly valid 100-product CSV with an "invalid_type" error.
_BRAND_TYPE_TRUE_TOKENS: frozenset[str] = frozenset(
    {
        # English
        "own brand",
        "own-brand",
        "ownbrand",
        "own label",
        "private label",
        "private-label",
        "store brand",
        "store label",
        "house brand",
        "store-own label",
        "pl",                     # common abbreviation
        # French
        "marque distributeur",
        "marque du distributeur",
        "marque propre",
        "marque enseigne",
        "mdd",
    }
)

#: Brand-type words that map to is_own_brand=False (national brand).
_BRAND_TYPE_FALSE_TOKENS: frozenset[str] = frozenset(
    {
        # English
        "branded",
        "brand",
        "national brand",
        "national-brand",
        "manufacturer brand",
        "manufacturer-brand",
        "name brand",
        # French
        "marque nationale",
        "marque fabricant",
        "marque",
    }
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
    # Phase WWF-I-hotfix2 — brand-type free-text values used by
    # real retailer CSVs. Normalise the whitespace + punctuation to
    # match the lookup tables.
    norm = " ".join(s.replace("_", " ").split())
    if norm in _BRAND_TYPE_TRUE_TOKENS:
        return True
    if norm in _BRAND_TYPE_FALSE_TOKENS:
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


def _bad_number_msg(row: dict[str, Any], *fields: str) -> str:
    """Build a clear 'expected a number' message including the offending raw value."""
    for f in fields:
        val = row.get(f)
        if val is not None and str(val).strip() != "":
            return f"{fields[0]}: expected a number, got {str(val).strip()!r}"
    return f"{fields[0]}: expected a number"


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
    # Phase 33J: external_product_id is no longer required. When the
    # retailer omits the column or leaves it blank, generate a stable
    # internal ID so traceability still works. The ``AUTO-`` prefix
    # marks the value as generated for UI/audit purposes.
    if not external_product_id:
        external_product_id = f"AUTO-{upload_id.hex[:8]}-{row_number:04d}"
        warnings.append(
            ValidationWarning(
                row_number=row_number,
                field="external_product_id",
                code="auto_generated",
                message=(
                    "external_product_id not provided; Altera generated an "
                    f"internal identifier: {external_product_id}"
                ),
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
        # Phase 33J — when the user mapped a column to weight_per_item_kg
        # but the value clearly looks like grams (e.g. "133"), the
        # generic "too large" error is unhelpful. Detect this case and
        # surface an actionable, French-aware message instead.
        raw_kg_value = _coerce_decimal(row.get("weight_per_item_kg"))
        looks_like_grams = (
            weight_err == "weight_too_large"
            and raw_kg_value is not None
            and not raw_kg_value.is_nan()
            and Decimal("50") <= raw_kg_value <= Decimal("5000")
        )
        if looks_like_grams:
            errors.append(
                ValidationError(
                    row_number=row_number,
                    field="weight_per_item_kg",
                    code="weight_unit_likely_grams",
                    message=(
                        f"weight_per_item_kg: value {raw_kg_value} looks like grams, "
                        "not kilograms. Map this column to 'Poids unitaire (g)' "
                        "instead of 'Poids unitaire (kg)'."
                    ),
                )
            )
        else:
            _weight_messages = {
                "invalid_type": _bad_number_msg(row, "weight_per_item_kg", "weight_per_item_g", "weight_per_item_lb", "weight_per_item_oz"),
                "weight_non_positive": "weight_per_item_kg: must be greater than 0",
                "weight_too_large": "weight_per_item_kg: value too large (maximum is 50 kg)",
                "mixed_weight_units": "weight_per_item_kg: multiple weight columns populated — use only one",
            }
            errors.append(
                ValidationError(
                    row_number=row_number,
                    field="weight_per_item_kg",
                    code=weight_err,
                    message=_weight_messages.get(weight_err, f"weight_per_item_kg: {weight_err}"),
                )
            )
    else:
        # Phase 33J — soft scale warnings (do NOT auto-convert):
        #   * mapped to weight_per_item_kg but value >= 5 kg (heavy item)
        #     → values may actually be grams.
        #   * mapped to weight_per_item_g but resulting kg < 0.005 (<5 g)
        #     → values may actually be kilograms.
        raw_kg = _coerce_decimal(row.get("weight_per_item_kg"))
        raw_g = _coerce_decimal(row.get("weight_per_item_g"))
        if raw_kg is not None and not raw_kg.is_nan() and raw_kg >= Decimal("5"):
            warnings.append(
                ValidationWarning(
                    row_number=row_number,
                    field="weight_per_item_kg",
                    code="weight_unit_likely_grams_warning",
                    message=(
                        f"weight_per_item_kg: value {raw_kg} is unusually heavy for "
                        "a single unit; it may actually be in grams. If so, map this "
                        "column to 'Poids unitaire (g)' instead of 'Poids unitaire (kg)'."
                    ),
                )
            )
        elif (
            raw_g is not None
            and not raw_g.is_nan()
            and weight_kg is not None
            and weight_kg < Decimal("0.005")
            and raw_g < Decimal("5")
        ):
            warnings.append(
                ValidationWarning(
                    row_number=row_number,
                    field="weight_per_item_g",
                    code="weight_unit_likely_kg_warning",
                    message=(
                        f"weight_per_item_g: value {raw_g} is unusually light for grams; "
                        "it may actually be in kilograms. If so, map this column to "
                        "'Poids unitaire (kg)' instead of 'Poids unitaire (g)'."
                    ),
                )
            )

    # --- Protein (PT-only field; missing is allowed at parse time) ---
    protein_pct, protein_err = normalise_protein_pct(row)
    if protein_err is not None:
        _protein_messages = {
            "invalid_type": _bad_number_msg(row, "protein_pct", "protein_g_per_100g"),
            "energy_not_protein": "protein_pct: energy units (kJ/kcal) are not accepted; provide g per 100 g instead",
            "mixed_protein_inputs": "protein_pct: multiple protein columns populated — use only one",
            "missing_density": "protein_pct: protein_g_per_100ml requires density_g_per_ml",
            "missing_serving_g": "protein_pct: protein_g_per_serving requires serving_g (> 0)",
            "protein_out_of_range": "protein_pct: must be between 0 and 100",
        }
        errors.append(
            ValidationError(
                row_number=row_number,
                field="protein_pct",
                code=protein_err,
                message=_protein_messages.get(protein_err, f"protein_pct: {protein_err}"),
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
                message=_bad_number_msg(row, "items_purchased"),
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
                message=_bad_number_msg(row, "items_sold"),
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
        raw_bool = str(row.get("is_own_brand", "")).strip()
        errors.append(
            ValidationError(
                row_number=row_number,
                field="is_own_brand",
                code="invalid_type",
                message=f"is_own_brand: expected true/false, yes/no, oui/non, or 1/0, got {raw_bool!r}",
            )
        )

    retail_channel = _retail_channel_or_error(
        row.get("retail_channel"), errors, row_number
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
        raw = str(value).strip() if value is not None else ""
        errors.append(
            ValidationError(
                row_number=row_number,
                field=field,
                code="invalid_type",
                message=f"{field}: expected a number, got {raw!r}",
            )
        )
        return None, True
    return coerced, False


#: Phase WWF-I-hotfix2 — alias map for retail_channel free-text
#: values that real retailer CSVs ship (English + French + slash and
#: hyphen variants). The canonical RetailChannel enum stays as
#: {fresh, grocery_ambient, frozen}; this map is the parser's
#: tolerant projection.
_RETAIL_CHANNEL_ALIASES: dict[str, RetailChannel] = {
    # fresh
    "fresh": RetailChannel.FRESH,
    "frais": RetailChannel.FRESH,
    "frais & traiteur": RetailChannel.FRESH,
    "produits frais": RetailChannel.FRESH,
    "chilled": RetailChannel.FRESH,
    "refrigerated": RetailChannel.FRESH,
    "refrigere": RetailChannel.FRESH,
    # grocery_ambient
    "grocery_ambient": RetailChannel.GROCERY_AMBIENT,
    "grocery ambient": RetailChannel.GROCERY_AMBIENT,
    "grocery/ambient": RetailChannel.GROCERY_AMBIENT,
    "grocery-ambient": RetailChannel.GROCERY_AMBIENT,
    "ambient": RetailChannel.GROCERY_AMBIENT,
    "ambient grocery": RetailChannel.GROCERY_AMBIENT,
    "grocery": RetailChannel.GROCERY_AMBIENT,
    "epicerie": RetailChannel.GROCERY_AMBIENT,
    "epicerie seche": RetailChannel.GROCERY_AMBIENT,
    "sec": RetailChannel.GROCERY_AMBIENT,
    "ambiant": RetailChannel.GROCERY_AMBIENT,
    "shelf-stable": RetailChannel.GROCERY_AMBIENT,
    "shelf stable": RetailChannel.GROCERY_AMBIENT,
    "dry goods": RetailChannel.GROCERY_AMBIENT,
    # frozen
    "frozen": RetailChannel.FROZEN,
    "frozen food": RetailChannel.FROZEN,
    "surgele": RetailChannel.FROZEN,
    "surgeles": RetailChannel.FROZEN,
    "produits surgeles": RetailChannel.FROZEN,
    "congele": RetailChannel.FROZEN,
}


def _retail_channel_or_error(
    value: Any,
    errors: list[ValidationError],
    row_number: int,
) -> RetailChannel | None:
    """Phase WWF-I-hotfix2 — accept the wide variety of free-text
    retail-channel values real retailer CSVs ship. The canonical enum
    values still pass through; the alias map handles "Grocery/Ambient",
    "Fresh", "Surgelé", etc. before we ever try ``RetailChannel(raw)``.

    Falls back to the original ``_enum_or_error`` for unrecognised
    values so the user still gets a structured ``invalid_enum`` error
    rather than a silent ``None``.
    """
    raw = _coerce_str_or_none(value)
    if raw is None:
        return None
    # Try the canonical lookup first (covers "fresh" / "grocery_ambient"
    # / "frozen" literals).
    try:
        return RetailChannel(raw)
    except ValueError:
        pass
    # Then the alias map. Lowercase + collapse whitespace + strip the
    # ASCII-folded version so "Surgelé" matches "surgele".
    import unicodedata
    nf = unicodedata.normalize("NFKD", raw.lower())
    folded = "".join(c for c in nf if not unicodedata.combining(c))
    norm = " ".join(folded.split())
    if norm in _RETAIL_CHANNEL_ALIASES:
        return _RETAIL_CHANNEL_ALIASES[norm]
    # Last-chance: unrecognised value → structured error so the user
    # sees exactly which row + which value failed.
    errors.append(
        ValidationError(
            row_number=row_number,
            field="retail_channel",
            code="invalid_enum",
            message=(
                f"retail_channel={raw!r} is not a valid RetailChannel "
                f"(accepted: fresh, grocery_ambient, frozen — or aliases "
                f"like 'Grocery/Ambient', 'Fresh', 'Frozen', 'Frais', "
                f"'Surgelé', 'Épicerie')"
            ),
        )
    )
    return None


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

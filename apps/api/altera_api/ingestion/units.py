"""Unit conversion for ingestion.

Implements docs/data/unit-conversion.md:

* `weight_per_item_kg` is canonical. `_g`, `_lb`, `_oz` are accepted
  variants and converted.
* `protein_pct` is canonical. `protein_g_per_100g` (synonym),
  `protein_g_per_100ml + density_g_per_ml`, `protein_g_per_serving +
  serving_g` are accepted variants. `protein_kj` / `protein_kcal` are
  explicitly rejected.

These helpers return ``(value | None, error_code | None)`` tuples.
A returned `value` is always a `Decimal`; the caller is responsible for
range checking it against the methodology bounds.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

G_TO_KG = Decimal("0.001")
LB_TO_KG = Decimal("0.45359237")
OZ_TO_KG = Decimal("0.028349523125")


def _coerce_decimal(value: Any) -> Decimal | None:
    """Convert a CSV cell to ``Decimal`` or ``None`` if blank.

    Returns ``None`` for blank strings and ``None`` inputs. Returns the
    sentinel ``Decimal("NaN")`` for unparseable input — the caller maps
    that to an `invalid_type` error code.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    s = str(value).strip()
    if s == "":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("NaN")


_WEIGHT_VARIANTS: tuple[tuple[str, Decimal | None], ...] = (
    ("weight_per_item_kg", None),
    ("weight_per_item_g", G_TO_KG),
    ("weight_per_item_lb", LB_TO_KG),
    ("weight_per_item_oz", OZ_TO_KG),
)


def normalise_weight_kg(row: dict[str, object]) -> tuple[Decimal | None, str | None]:
    """Resolve a single `weight_per_item_kg` value from a row.

    Errors (returned as ``error_code``):

    * ``mixed_weight_units`` — more than one weight variant populated.
    * ``invalid_type`` — value present but not numeric.
    * ``weight_non_positive`` — value <= 0.
    * ``weight_too_large`` — value > 50 kg.

    Returns ``(None, None)`` when no weight column is populated. The
    caller decides whether that is fatal (it is, for both methodologies
    at MVP).
    """
    populated: list[tuple[str, Decimal, Decimal | None]] = []
    for column, factor in _WEIGHT_VARIANTS:
        raw = row.get(column)
        value = _coerce_decimal(raw)
        if value is None:
            continue
        if value.is_nan():
            return None, "invalid_type"
        populated.append((column, value, factor))

    if not populated:
        return None, None
    if len(populated) > 1:
        return None, "mixed_weight_units"

    _, value, factor = populated[0]
    kg = value if factor is None else value * factor
    if kg <= 0:
        return None, "weight_non_positive"
    if kg > Decimal("50"):
        return None, "weight_too_large"
    return kg, None


def normalise_protein_pct(row: dict[str, object]) -> tuple[Decimal | None, str | None]:
    """Resolve a single `protein_pct` (% by mass) value from a row.

    Errors:

    * ``energy_not_protein`` — `protein_kj` or `protein_kcal` provided.
    * ``mixed_protein_inputs`` — more than one source populated.
    * ``invalid_type`` — value present but not numeric.
    * ``protein_out_of_range`` — value outside [0, 100].
    * ``missing_density`` / ``missing_serving_g`` — partial pair given.

    Missing `protein_pct` returns ``(None, None)``. PT treats missing
    protein as "row classifiable but excluded from totals"; the caller
    decides whether to emit a warning.
    """
    # Explicit rejection of energy units.
    for energy_key in ("protein_kj", "protein_kcal"):
        if _coerce_decimal(row.get(energy_key)) is not None:
            return None, "energy_not_protein"

    direct = _coerce_decimal(row.get("protein_pct"))
    synonym = _coerce_decimal(row.get("protein_g_per_100g"))
    g_per_100ml = _coerce_decimal(row.get("protein_g_per_100ml"))
    density = _coerce_decimal(row.get("density_g_per_ml"))
    g_per_serving = _coerce_decimal(row.get("protein_g_per_serving"))
    serving_g = _coerce_decimal(row.get("serving_g"))

    for v in (direct, synonym, g_per_100ml, density, g_per_serving, serving_g):
        if v is not None and v.is_nan():
            return None, "invalid_type"

    candidates: list[Decimal] = []
    if direct is not None:
        candidates.append(direct)
    if synonym is not None:
        candidates.append(synonym)
    if g_per_100ml is not None:
        if density is None:
            return None, "missing_density"
        candidates.append(g_per_100ml / density)
    if g_per_serving is not None:
        if serving_g is None or serving_g == 0:
            return None, "missing_serving_g"
        candidates.append(g_per_serving / serving_g * Decimal("100"))

    if not candidates:
        return None, None
    if len(candidates) > 1:
        return None, "mixed_protein_inputs"

    pct = candidates[0]
    if pct < 0 or pct > Decimal("100"):
        return None, "protein_out_of_range"
    return pct, None

"""Phase Product-UX-C — Top-N product contributors for the report.

Derives, from a run's already-stored per-product rows
(``RunRecord.rows_payload``), the products that most improve or most
hurt the headline figures:

* **PT** — products contributing the most *plant* protein (positive) vs
  the most *animal* protein (watch-out). The per-product plant/animal
  attribution mirrors exactly what the calculation engine does for the
  headline split (composite rows use their per-product split when
  present, otherwise the 50/50 default; single-group rows are wholly
  plant or wholly animal). **No formula is changed here** — we only
  read values the engine already computed and attribute them the same
  way the summary does, for ranking purposes.

* **WWF** — products in plant-forward / target groups (positive) vs
  products in watch-out groups (watch-out), ranked by sold weight.

The extraction is intentionally defensive: malformed, partial, or
older rows are skipped rather than raising, so the report always
renders. Both lists are capped at ``_LIMIT`` entries.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from altera_api.domain.product import NormalizedProduct
from altera_api.domain.report import PTProductContributor, WWFProductContributor
from altera_api.exports.common import format_decimal

_LIMIT = 10
_ZERO = Decimal("0")
_TWO = Decimal("2")

# FG1 subgroup labels that the methodology treats as animal protein.
_FG1_ANIMAL_LABELS = frozenset(
    {"red_meat", "poultry", "processed_meats_alternatives", "seafood", "eggs"}
)


def _dec(value: object) -> Decimal | None:
    """Parse a stored numeric (str / float / int / Decimal) defensively."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _product_lookup(
    products: list[NormalizedProduct],
) -> dict[str, NormalizedProduct]:
    return {str(p.id): p for p in products}


def _name_and_category(
    products_by_id: dict[str, NormalizedProduct], product_id: str
) -> tuple[str, str | None]:
    product = products_by_id.get(product_id)
    if product is None:
        # Defensive fallback — never blank, never crash.
        return (f"Produit {product_id[:8]}", None)
    return (product.product_name, product.retailer_category)


# ---------------------------------------------------------------------------
# Protein Tracker
# ---------------------------------------------------------------------------

_PT_RATIONALE = {
    "plant_based_core": "Cœur végétal",
    "plant_based_non_core": "Végétal (hors cœur)",
    "animal_core": "Cœur animal",
}


def pt_contributors(
    rows: list[dict],
    products: list[NormalizedProduct],
) -> tuple[list[PTProductContributor], list[PTProductContributor]]:
    """Return ``(top_positive, top_watchout)`` PT contributors.

    Positive = most plant protein (kg), descending.
    Watch-out = most animal protein (kg), descending.
    """
    products_by_id = _product_lookup(products)
    positive: list[tuple[Decimal, PTProductContributor]] = []
    watchout: list[tuple[Decimal, PTProductContributor]] = []

    for row in rows:
        if not row.get("in_scope"):
            continue
        protein = _dec(row.get("protein_kg"))
        if protein is None or protein <= _ZERO:
            continue
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        group = str(row.get("pt_group") or "")

        used_split = bool(row.get("used_per_product_split"))
        plant = _dec(row.get("plant_protein_kg"))
        animal = _dec(row.get("animal_protein_kg"))

        if used_split and plant is not None and animal is not None:
            rationale = "Composite — répartition par produit"
        elif group == "composite_products":
            half = protein / _TWO
            plant, animal = half, half
            rationale = "Composite — estimation 50/50"
        elif group in ("plant_based_core", "plant_based_non_core"):
            plant, animal = protein, _ZERO
            rationale = _PT_RATIONALE[group]
        elif group == "animal_core":
            plant, animal = _ZERO, protein
            rationale = _PT_RATIONALE[group]
        else:
            # out_of_scope / unknown — not a methodology group.
            continue

        plant = plant if plant is not None else _ZERO
        animal = animal if animal is not None else _ZERO
        name, category = _name_and_category(products_by_id, product_id)

        contributor = PTProductContributor(
            product_id=product_id,
            product_name=name,
            retailer_category=category,
            pt_group=group,
            plant_protein_kg=format_decimal(plant),
            animal_protein_kg=format_decimal(animal),
            total_protein_kg=format_decimal(protein),
            rationale=rationale,
        )
        if plant > _ZERO:
            positive.append((plant, contributor))
        if animal > _ZERO:
            watchout.append((animal, contributor))

    positive.sort(key=lambda t: t[0], reverse=True)
    watchout.sort(key=lambda t: t[0], reverse=True)
    return (
        [c for _, c in positive[:_LIMIT]],
        [c for _, c in watchout[:_LIMIT]],
    )


# ---------------------------------------------------------------------------
# WWF
# ---------------------------------------------------------------------------

_COMPOSITE_RATIONALE = {
    "vegan": ("positive", "Composite végan"),
    "vegetarian": ("positive", "Composite végétarien"),
    "meat_based": ("watchout", "Composite à base de viande"),
    "seafood_based": ("watchout", "Composite à base de poisson"),
}


def _wwf_polarity(
    food_group: str, is_composite: bool, bucket: str | None, label: str | None
) -> tuple[str, str] | None:
    """Classify a WWF row as ``("positive"|"watchout", rationale)`` or None.

    ``None`` means the row is neither clearly aligned nor a watch-out
    (e.g. dairy, fats, FG6) and is excluded from both Top lists — a
    deliberately conservative choice.
    """
    if is_composite and bucket:
        return _COMPOSITE_RATIONALE.get(bucket)
    if food_group == "FG1":
        if not label:
            return None  # subgroup unknown — don't guess plant vs animal
        if label in _FG1_ANIMAL_LABELS:
            return ("watchout", "Protéines animales (FG1)")
        return ("positive", "Protéines végétales (FG1)")
    if food_group == "FG4":
        return ("positive", "Fruits et légumes (FG4)")
    if food_group == "FG5":
        if label == "whole_grain":
            return ("positive", "Céréales complètes (FG5)")
        return ("positive", "Céréales (FG5)")
    if food_group == "FG7":
        return ("watchout", "Snacks (FG7)")
    return None


def wwf_contributors(
    rows: list[dict],
    products: list[NormalizedProduct],
) -> tuple[list[WWFProductContributor], list[WWFProductContributor]]:
    """Return ``(top_positive, top_watchout)`` WWF contributors, by weight."""
    products_by_id = _product_lookup(products)
    positive: list[tuple[Decimal, WWFProductContributor]] = []
    watchout: list[tuple[Decimal, WWFProductContributor]] = []

    for row in rows:
        if not row.get("in_scope"):
            continue
        weight = _dec(row.get("weight_kg"))
        if weight is None or weight <= _ZERO:
            continue
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        food_group = str(row.get("wwf_food_group") or "")
        is_composite = bool(row.get("wwf_is_composite"))
        bucket_raw = row.get("wwf_composite_step1_bucket")
        bucket = str(bucket_raw) if bucket_raw else None
        label_raw = row.get("wwf_subgroup_label")
        label = str(label_raw) if label_raw else None

        polarity = _wwf_polarity(food_group, is_composite, bucket, label)
        if polarity is None:
            continue
        bucket_class, rationale = polarity
        name, category = _name_and_category(products_by_id, product_id)

        contributor = WWFProductContributor(
            product_id=product_id,
            product_name=name,
            retailer_category=category,
            wwf_group=food_group,
            wwf_bucket=bucket,
            weight_kg=format_decimal(weight),
            rationale=rationale,
        )
        if bucket_class == "positive":
            positive.append((weight, contributor))
        else:
            watchout.append((weight, contributor))

    positive.sort(key=lambda t: t[0], reverse=True)
    watchout.sort(key=lambda t: t[0], reverse=True)
    return (
        [c for _, c in positive[:_LIMIT]],
        [c for _, c in watchout[:_LIMIT]],
    )

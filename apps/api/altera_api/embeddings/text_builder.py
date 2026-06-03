"""Phase Quality-V2-A — embedding text builders.

Build the plain-text descriptors that get embedded for classification
retrieval + NEVO candidate retrieval. CRITICAL PRIVACY RULE: these
texts contain product *descriptors only* — never commercial fields
(items_purchased, items_sold, weight, sales, margin, price …). The
builders accept a dict and raise if a forbidden field is present, so a
caller can't accidentally leak a commercial value into an embedding.
"""

from __future__ import annotations

# Forbidden commercial/physical fields — mirrors ``ai/policy.py`` so
# the embedding text path has the same guarantee as the AI prompt path.
_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "items_purchased",
        "items_sold",
        "weight_per_item_kg",
        "weight_per_item_g",
        "weight_per_item_lb",
        "weight_per_item_oz",
        "pack_weight_g",
        "sales_value",
        "sales_volume",
        "margin",
        "cost_price",
        "unit_price",
        "price",
        "revenue",
    }
)
_FORBIDDEN_PREFIXES = ("price", "sales", "margin", "cost", "weight", "volume")


class ForbiddenEmbeddingField(ValueError):
    """Raised when a commercial/physical field reaches a text builder."""


def _assert_no_commercial(data: dict[str, object]) -> None:
    for key in data:
        k = key.lower()
        if k in _FORBIDDEN_KEYS or any(k.startswith(p) for p in _FORBIDDEN_PREFIXES):
            raise ForbiddenEmbeddingField(
                f"Commercial/physical field {key!r} must never be embedded."
            )


def _line(label: str, value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return f"{label}: {text}" if text else None


def build_product_text(data: dict[str, object]) -> str:
    """Product descriptor for classification retrieval.

    Allowed keys: product_name, retailer_category, ingredients_text,
    labels. Anything commercial raises.
    """
    _assert_no_commercial(data)
    lines = [
        _line("Name", data.get("product_name")),
        _line("Retailer category", data.get("retailer_category")),
        _line("Ingredients", data.get("ingredients_text")),
        _line("Labels", data.get("labels")),
    ]
    return "\n".join(line for line in lines if line)


def build_pt_example_text(data: dict[str, object]) -> str:
    """A labelled Protein Tracker training example (descriptor + the
    PT group it belongs to)."""
    _assert_no_commercial(data)
    base = build_product_text(data)
    group = _line("Protein Tracker group", data.get("pt_group"))
    return "\n".join(x for x in (base, group) if x)


def build_wwf_example_text(data: dict[str, object]) -> str:
    """A labelled WWF training example (descriptor + food group /
    subgroup / composite bucket)."""
    _assert_no_commercial(data)
    base = build_product_text(data)
    extra = [
        _line("WWF food group", data.get("wwf_food_group")),
        _line("WWF subgroup", data.get("wwf_subgroup")),
        _line("WWF composite bucket", data.get("wwf_composite_step1_bucket")),
    ]
    return "\n".join(x for x in [base, *extra] if x)


def build_nevo_reference_text(data: dict[str, object]) -> str:
    """A NEVO reference food descriptor for candidate retrieval.

    Includes the English name plus, when present, the Dutch name
    (``food_name_nl``), a French name (``food_name_fr``), aliases /
    synonyms, and the food group — richer reference text improves
    cross-language semantic retrieval (Phase Quality-V2-D)."""
    _assert_no_commercial(data)
    lines = [
        _line("Food", data.get("food_name_en")),
        _line("Food (NL)", data.get("food_name_nl")),
        _line("Food (FR)", data.get("food_name_fr")),
        _line("Aliases", data.get("aliases") or data.get("synonym")),
        _line("Group", data.get("food_group")),
        _line("NEVO code", data.get("nevo_code")),
    ]
    return "\n".join(line for line in lines if line)

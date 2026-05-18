"""AI inputs policy enforcement (layer 4 — outbound HTTP guard).

This module is the last line of defence before bytes leave the process
and hit an external LLM provider. It inspects an outbound payload (a
dict of field name → value) against the allow-list and raises
:class:`CommercialDataBlockError` if any forbidden field name appears.

The earlier layers (Pydantic strict ``ClassifierPromptInput``, the
prompt builder, the ``products`` table itself) should already prevent
this from firing. If this guard ever fires in production, that is a
code bug, not a transient error.
"""

from __future__ import annotations

from typing import Any

#: The exact set of field names the AI is allowed to receive. See
#: docs/classification/ai-inputs-policy.md. Every other field is
#: forbidden, including item counts and per-item weight (physical
#: methodology quantities that live in the database but never reach
#: a prompt).
ALLOWED_PROMPT_FIELDS: frozenset[str] = frozenset(
    {
        "product_name",
        "retailer_category",
        "retailer_subcategory",
        "brand",
        "ingredients_text",
        "labels",
        "language",
        "country",
    }
)

#: Forbidden field prefixes. Any payload field name starting with one of
#: these is rejected even if not in the explicit forbidden list — this
#: catches new commercial columns added by future retailers without us
#: needing to update the explicit list first.
_FORBIDDEN_PROMPT_PREFIXES: tuple[str, ...] = (
    "promotion_",
    "confidential_",
    "internal_",
    "store_",
    "supplier_",
)

#: Explicit forbidden names. Belt-and-braces with the prefix list.
_FORBIDDEN_PROMPT_NAMES: frozenset[str] = frozenset(
    {
        "sales_value",
        "revenue",
        "margin",
        "cost_price",
        "contract_terms",
        # Physical methodology quantities — live in the DB but never in a prompt.
        "items_purchased",
        "items_sold",
        "weight_per_item_kg",
        "weight_per_item_g",
        "weight_per_item_lb",
        "weight_per_item_oz",
        # Per-product PT composite split (also methodology arithmetic, not classification).
        "plant_protein_pct",
        "animal_protein_pct",
        "protein_pct",
        "protein_g_per_100g",
        "protein_g_per_100ml",
        "protein_g_per_serving",
        "serving_g",
        "density_g_per_ml",
    }
)


class CommercialDataBlockError(RuntimeError):
    """Raised when an outbound payload contains a forbidden field name.

    The error message names the offending field but **never** the
    offending value, because the value is by definition commercially
    sensitive and we do not want it in logs.
    """

    def __init__(self, field_name: str, *, reason: str) -> None:
        super().__init__(f"commercial_data_block: field={field_name!r} ({reason})")
        self.field_name = field_name
        self.reason = reason


def _is_forbidden(name: str) -> tuple[bool, str]:
    if name in _FORBIDDEN_PROMPT_NAMES:
        return True, "explicit forbidden name"
    if any(name.startswith(p) for p in _FORBIDDEN_PROMPT_PREFIXES):
        return True, "forbidden prefix"
    if name not in ALLOWED_PROMPT_FIELDS:
        return True, "not in allow-list"
    return False, ""


def assert_payload_allowed(payload: dict[str, Any]) -> None:
    """Raise ``CommercialDataBlockError`` if any field name is forbidden.

    The check is *allow-list*-based: unknown fields are forbidden. This
    is deliberately stricter than a blocklist — a payload field is
    allowed only if it appears in ``ALLOWED_PROMPT_FIELDS``.
    """
    for key in payload:
        forbidden, reason = _is_forbidden(key)
        if forbidden:
            raise CommercialDataBlockError(key, reason=reason)

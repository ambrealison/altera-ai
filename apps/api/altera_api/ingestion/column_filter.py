"""Commercial-column ingestion filter.

The single most important rule in Altera AI (see
docs/classification/ai-inputs-policy.md): commercial data is never sent
to an external LLM. A subset of commercial fields are also forbidden
from the database entirely; they are dropped at the ingestion boundary
here.

This filter runs *before* parsing into RawProduct, so types do not
matter — the filter operates on column names only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Exact-match column names to drop.
_EXACT_FORBIDDEN: frozenset[str] = frozenset(
    {
        "sales_value",
        "revenue",
        "margin",
        "cost_price",
        "supplier_id",
        "supplier_name",
        "contract_terms",
        "store_id",
        "store_name",
        "store_region",
    }
)

#: Prefix-match column names to drop. Any header starting with these
#: prefixes is treated as commercial and dropped.
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "promotion_",
    "confidential_",
    "internal_",
    "store_",
    "supplier_",
)

#: Public regex set so callers (audit, docs) can introspect the policy.
FORBIDDEN_COLUMN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"^{re.escape(name)}$") for name in sorted(_EXACT_FORBIDDEN)
) + tuple(re.compile(rf"^{re.escape(prefix)}.+$") for prefix in _FORBIDDEN_PREFIXES)


def _is_forbidden(name: str) -> bool:
    if name in _EXACT_FORBIDDEN:
        return True
    return any(name.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES)


def filter_commercial_columns(
    row: dict[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Drop commercial columns from a row.

    Returns the filtered row and a tuple of dropped column names (sorted
    for deterministic audit metadata).
    """
    kept: dict[str, object] = {}
    dropped: list[str] = []
    for key, value in row.items():
        if _is_forbidden(key):
            dropped.append(key)
        else:
            kept[key] = value
    return kept, tuple(sorted(dropped))


def detect_forbidden_columns(headers: Iterable[str]) -> tuple[str, ...]:
    """Find all forbidden columns in a header list. Pure inspection."""
    return tuple(sorted(h for h in headers if _is_forbidden(h)))

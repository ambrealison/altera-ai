"""Header normalisation.

Per docs/data/input-formats.md the rules are: strip surrounding
whitespace, lowercase, convert internal whitespace and hyphens to
underscores. This makes `"Items Purchased"`, `"items-purchased"`, and
`"items_purchased"` all map to `items_purchased`.
"""

from __future__ import annotations

import re

_WHITESPACE_OR_HYPHEN = re.compile(r"[\s\-]+")


def normalise_header(header: str) -> str:
    """Normalise one header cell."""
    return _WHITESPACE_OR_HYPHEN.sub("_", header.strip().lower())


def normalise_row_headers(row: dict[str, object]) -> dict[str, object]:
    """Return a new dict with header keys normalised.

    If two raw headers normalise to the same key (e.g. ``Items Purchased``
    and ``items_purchased``), the last one wins. The caller is expected
    to detect duplicate headers upstream — `csv_reader` raises in that case.
    """
    return {normalise_header(k): v for k, v in row.items()}

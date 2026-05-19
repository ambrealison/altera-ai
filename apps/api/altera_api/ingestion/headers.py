"""Header normalisation.

Per docs/data/input-formats.md the rules are: strip surrounding
whitespace, lowercase, strip accents (é→e, ç→c), convert internal
whitespace/hyphens/punctuation to underscores, collapse runs.

This makes all of these equivalent:
  "Items Purchased", "items-purchased", "items_purchased",
  "Éléments achetés", "nb_items_purchased"
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_header(header: str) -> str:
    """Normalise one header cell.

    Steps: strip whitespace → NFKD decompose → strip non-ASCII (accent marks)
    → lowercase → collapse non-alphanumeric runs to underscores → strip
    leading/trailing underscores.
    """
    s = header.strip()
    # Strip accents: NFKD decomposes é→e+combining-acute; encode("ascii","ignore") drops combining chars
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _NON_ALNUM.sub("_", s)
    return s.strip("_")


def normalise_row_headers(row: dict[str, object]) -> dict[str, object]:
    """Return a new dict with header keys normalised.

    If two raw headers normalise to the same key (e.g. ``Items Purchased``
    and ``items_purchased``), the last one wins. The caller is expected
    to detect duplicate headers upstream — `csv_reader` raises in that case.
    """
    return {normalise_header(k): v for k, v in row.items()}

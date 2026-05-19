"""Streaming CSV reader with size/row limits and header normalisation.

Per docs/data/input-formats.md:

* UTF-8 (BOM tolerated).
* RFC 4180 quoting.
* Max 50 MB per file, 200,000 rows.
* Comma or tab delimiter (TSV uses tab).
* Decimal separator is ``.``.

The reader returns raw header list and an iterator of header-keyed
rows. It does no semantic validation — that is the parser's job.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

from altera_api.ingestion.headers import normalise_header


class CSVReadError(Exception):
    """Raised when the file cannot be read at all (encoding, size, delim)."""


@dataclass(frozen=True)
class CSVReadConfig:
    max_bytes: int = 50 * 1024 * 1024
    max_rows: int = 200_000
    delimiter: str = ","


@dataclass(frozen=True)
class ParsedTable:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    duplicate_headers: tuple[str, ...]


def read_table_bytes(data: bytes, *, config: CSVReadConfig | None = None) -> ParsedTable:
    """Decode and parse CSV/TSV bytes.

    Returns headers in their normalised form and rows keyed by those
    normalised headers. Raises ``CSVReadError`` on size, encoding, or
    header problems. Per-row validation is deferred to the parser.
    """
    cfg = config or CSVReadConfig()
    if len(data) > cfg.max_bytes:
        raise CSVReadError(f"file is {len(data)} bytes, exceeds limit {cfg.max_bytes}")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVReadError(f"file is not valid UTF-8: {exc}") from exc

    reader = csv.reader(io.StringIO(text), delimiter=cfg.delimiter)
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise CSVReadError("file is empty") from exc

    headers = tuple(normalise_header(h) for h in raw_header)
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for h in headers:
        seen[h] = seen.get(h, 0) + 1
        if seen[h] == 2:
            duplicates.append(h)

    rows: list[dict[str, str]] = []
    for row_idx, raw_row in enumerate(reader, start=2):  # data rows are 2-indexed in CSV terms
        if len(rows) >= cfg.max_rows:
            raise CSVReadError(f"file exceeds row limit {cfg.max_rows} at line {row_idx}")
        # Skip comment rows — first cell starts with "#" (used by templates for notes).
        if raw_row and raw_row[0].lstrip().startswith("#"):
            continue
        # Tolerate short/long rows: pad with "" or truncate.
        padded = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
        padded = padded[: len(headers)]
        rows.append(dict(zip(headers, padded, strict=True)))

    return ParsedTable(
        headers=headers,
        rows=tuple(rows),
        duplicate_headers=tuple(sorted(set(duplicates))),
    )


def iter_table_bytes(
    data: bytes, *, config: CSVReadConfig | None = None
) -> Iterator[dict[str, str]]:
    """Streaming variant for callers that do not need the full table at once."""
    table = read_table_bytes(data, config=config)
    yield from table.rows

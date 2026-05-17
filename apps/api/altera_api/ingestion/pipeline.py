"""End-to-end ingestion orchestrator.

Bytes → (`ValidationReport`, tuple of `NormalizedProduct`, dropped columns).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.validation import (
    ValidationError,
    ValidationReport,
    ValidationWarning,
)
from altera_api.ingestion.column_filter import filter_commercial_columns
from altera_api.ingestion.csv_reader import (
    CSVReadConfig,
    CSVReadError,
    read_table_bytes,
)
from altera_api.ingestion.normalizer import normalize_product
from altera_api.ingestion.parser import parse_row


@dataclass(frozen=True)
class IngestResult:
    report: ValidationReport
    products: tuple[NormalizedProduct, ...]
    dropped_columns: tuple[str, ...]
    duplicate_headers: tuple[str, ...]
    read_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.read_error is None and not self.report.is_blocking


def ingest_csv_bytes(
    data: bytes,
    *,
    upload_id: UUID,
    project_id: UUID,
    organisation_id: UUID,
    methodologies_enabled: frozenset[Methodology],
    config: CSVReadConfig | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Run the full ingestion pipeline on raw CSV bytes."""
    timestamp = now or datetime.now(UTC)

    try:
        table = read_table_bytes(data, config=config)
    except CSVReadError as exc:
        empty = ValidationReport(upload_id=upload_id, total_rows=0)
        return IngestResult(
            report=empty,
            products=(),
            dropped_columns=(),
            duplicate_headers=(),
            read_error=str(exc),
        )

    all_dropped: set[str] = set()
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    products: list[NormalizedProduct] = []
    total_rows = len(table.rows)

    for offset, row in enumerate(table.rows):
        row_number = offset + 1  # 1-indexed across data rows
        kept, dropped = filter_commercial_columns(row)
        all_dropped.update(dropped)

        raw, parse_errors, parse_warnings = parse_row(
            kept, upload_id=upload_id, row_number=row_number
        )
        errors.extend(parse_errors)
        warnings.extend(parse_warnings)
        if raw is None:
            continue

        product, normalise_errors = normalize_product(
            raw,
            project_id=project_id,
            organisation_id=organisation_id,
            methodologies_enabled=methodologies_enabled,
            now=timestamp,
        )
        errors.extend(normalise_errors)
        if product is not None:
            products.append(product)

    report = ValidationReport(
        upload_id=upload_id,
        total_rows=total_rows,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )

    return IngestResult(
        report=report,
        products=tuple(products),
        dropped_columns=tuple(sorted(all_dropped)),
        duplicate_headers=table.duplicate_headers,
    )

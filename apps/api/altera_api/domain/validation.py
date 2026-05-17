"""Validation result types.

`ValidationError` is a row-level error that blocks the row from
becoming a `NormalizedProduct`. `ValidationWarning` is a row-level
concern that does not block. `ValidationReport` aggregates both, per
upload.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import DomainBase, NonEmptyStr


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class _ValidationEntryBase(DomainBase):
    row_number: int = Field(ge=1)
    field: str | None = None
    code: NonEmptyStr
    message: NonEmptyStr


class ValidationError(_ValidationEntryBase):
    severity: ValidationSeverity = ValidationSeverity.ERROR

    @model_validator(mode="after")
    def _severity_is_error(self) -> Self:
        if self.severity is not ValidationSeverity.ERROR:
            raise ValueError("ValidationError must have severity=error.")
        return self


class ValidationWarning(_ValidationEntryBase):
    severity: ValidationSeverity = ValidationSeverity.WARNING

    @model_validator(mode="after")
    def _severity_is_warning(self) -> Self:
        if self.severity is not ValidationSeverity.WARNING:
            raise ValueError("ValidationWarning must have severity=warning.")
        return self


class ValidationReport(DomainBase):
    """Aggregated validation outcome for one upload."""

    upload_id: UUID
    total_rows: int = Field(ge=0)
    errors: tuple[ValidationError, ...] = ()
    warnings: tuple[ValidationWarning, ...] = ()

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def rows_with_errors(self) -> int:
        return len({e.row_number for e in self.errors})

    @property
    def rows_with_warnings(self) -> int:
        return len({w.row_number for w in self.warnings})

    @property
    def is_blocking(self) -> bool:
        return self.error_count > 0

    @model_validator(mode="after")
    def _row_numbers_in_range(self) -> Self:
        for entry in (*self.errors, *self.warnings):
            if entry.row_number > self.total_rows:
                raise ValueError(
                    f"validation entry references row_number={entry.row_number} "
                    f"but total_rows={self.total_rows}."
                )
        return self

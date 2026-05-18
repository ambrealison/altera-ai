"""Shared export helpers — contexts, decimal formatting, file naming."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import RetailChannel

#: Encoding used for the CSV export. The BOM is the marker most
#: spreadsheets (Excel, Numbers) need to display UTF-8 correctly.
CSV_ENCODING = "utf-8-sig"

ExportFormat = Literal["csv", "json", "md"]


@dataclass(frozen=True)
class RunMetadata:
    """Run-level metadata propagated into every export.

    The run's methodology + versions live on the summary; we don't
    duplicate them here. ``project_slug`` is the only project-level
    field needed (for the filename); the rest stays in the summary.
    """

    run_id: UUID
    project_slug: str
    started_at: datetime
    finished_at: datetime | None = None
    triggered_by: UUID | None = None


@dataclass(frozen=True)
class ExportProductMaster:
    """Identity-only product fields that exports may render.

    This is a deliberately narrow projection of ``NormalizedProduct``.
    No commercial field can land here because the class doesn't declare
    one. Renderers that loop over a mapping of these will never
    accidentally emit revenue, margin, etc. — that is precisely the
    point of the projection.
    """

    product_id: UUID
    external_product_id: str
    product_name: str
    brand: str | None = None
    is_own_brand: bool | None = None  # WWF only
    retail_channel: RetailChannel | None = None  # WWF only


@dataclass(frozen=True)
class ExportClassificationMeta:
    """Classification audit-trail fields that appear in CSV per row.

    Mirrors the columns documented in ``docs/outputs/formats.md`` —
    ``source``, ``confidence``, plus the source-specific provenance
    field (``rule_id`` for deterministic, ``ai_model`` for AI,
    ``reviewer_user_id`` for manual review).
    """

    source: ClassificationSource
    confidence: Decimal
    rule_id: str | None = None
    ai_model: str | None = None
    reviewer_user_id: UUID | None = None


def format_decimal(value: Decimal | None) -> str:
    """Render a ``Decimal`` for CSV / JSON.

    * ``None`` → empty string (CSV) or empty JSON string at the call
      site as needed.
    * Otherwise full ``Decimal`` precision via ``str(value)``.

    We keep this stable across the codebase so two renderings of the
    same value match byte-for-byte.
    """
    if value is None:
        return ""
    # Strip the noisy ``0E-8`` form that Decimal emits when an exponent
    # is preserved. ``f"{v:.8f}"`` is overkill (forces 8 dp on values
    # already at higher precision), so we normalise via the format spec
    # used elsewhere in the calc layer.
    return f"{value:f}"


def export_filename(
    *,
    project_slug: str,
    methodology: Methodology,
    run_id: UUID,
    fmt: ExportFormat,
    today: date,
) -> str:
    """Canonical filename per ``docs/outputs/formats.md``.

    ``altera_<project_slug>_<methodology>_<run_id_short>_<yyyymmdd>.{csv,json,md}``
    """
    short_id = run_id.hex[:8]
    yyyymmdd = today.strftime("%Y%m%d")
    return f"altera_{project_slug}_{methodology.value}_{short_id}_{yyyymmdd}.{fmt}"

"""Export rendering.

Pure functions that take a calculation run + its products and produce
CSV bytes, JSON string, or Markdown string. No I/O — the persistence
layer (Supabase Storage) sits above this module.

For MVP we emit three formats per methodology, following
``docs/outputs/formats.md`` exactly:

* CSV — per-row dump joined to identity fields, UTF-8 with BOM, full
  ``Decimal`` precision.
* JSON — full structured result with versions and metadata, numbers as
  strings to preserve precision.
* Markdown — human-readable summary (no per-row data).

The exporters never emit commercial fields. The product master only
carries identity fields (no sales/revenue/margin) by construction —
see :class:`ExportProductMaster`.
"""

from __future__ import annotations

from altera_api.exports.common import (
    ExportClassificationMeta,
    ExportProductMaster,
    RunMetadata,
    export_filename,
    format_decimal,
)
from altera_api.exports.protein_tracker import (
    PTExportContext,
    render_pt_csv,
    render_pt_json,
    render_pt_markdown,
)
from altera_api.exports.report import build_report_document
from altera_api.exports.wwf import (
    WWFExportContext,
    render_wwf_csv,
    render_wwf_json,
    render_wwf_markdown,
)

__all__ = [
    "ExportClassificationMeta",
    "ExportProductMaster",
    "PTExportContext",
    "RunMetadata",
    "WWFExportContext",
    "build_report_document",
    "export_filename",
    "format_decimal",
    "render_pt_csv",
    "render_pt_json",
    "render_pt_markdown",
    "render_wwf_csv",
    "render_wwf_json",
    "render_wwf_markdown",
]

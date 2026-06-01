"""Robust deserialization of persisted run summary payloads (hotfix).

``RunRecord.summary_payload`` is persisted as JSON (Postgres ``jsonb``),
so on read every value is a JSON primitive: UUIDs and Decimals come back
as strings, tuples as lists, enums as their string values. The domain
summary models are **strict** (``DomainBase`` sets ``strict=True``), so a
plain ``Model.model_validate(payload)`` of that JSON-shaped dict raises
(``Input should be an instance of UUID``, ``... of Decimal``, ``Input
should be a valid tuple``, etc.).

These helpers validate in **non-strict** mode for this single
deserialization step, which restores the domain types (UUID, Decimal,
tuple, Enum) from their JSON forms. This does **not** loosen the domain
model definitions — ``strict=True`` still governs every other place a
summary is constructed; only reads of an already-serialized payload are
parsed leniently here.

In-memory tests construct ``summary_payload`` via ``model_dump()`` in
python mode (native types), which is why the strict path passed in tests
but failed in production against JSON-shaped rows.
"""

from __future__ import annotations

from typing import Any

from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary
from altera_api.domain.wwf import WWFCalculationSummary


def parse_pt_summary_payload(payload: Any) -> ProteinTrackerCalculationSummary:
    """Parse a persisted PT summary payload into the domain model.

    Accepts either an already-constructed summary (returned as-is) or a
    JSON-shaped dict (validated leniently so strings/lists are coerced
    back to UUID/Decimal/tuple/enum).
    """
    if isinstance(payload, ProteinTrackerCalculationSummary):
        return payload
    return ProteinTrackerCalculationSummary.model_validate(payload, strict=False)


def parse_wwf_summary_payload(payload: Any) -> WWFCalculationSummary:
    """Parse a persisted WWF summary payload into the domain model.

    Accepts either an already-constructed summary (returned as-is) or a
    JSON-shaped dict (validated leniently so strings/lists are coerced
    back to UUID/Decimal/tuple/enum).
    """
    if isinstance(payload, WWFCalculationSummary):
        return payload
    return WWFCalculationSummary.model_validate(payload, strict=False)

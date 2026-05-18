"""Calculation modules.

Per-methodology calculators that take ``NormalizedProduct`` + final
classifications and produce calculation rows + a summary stamped with
the full version set. PT lands in this phase; WWF in Phase 10.

The calculators are pure — no I/O, no clock, no randomness. The
orchestration layer above passes pre-classified products in and
persists what the calculator returns.
"""

from __future__ import annotations

from altera_api.calculation.protein_tracker import (
    DEFAULT_SPLIT_TOLERANCE,
    PTRunResult,
    PTRunVersions,
    calculate_pt_run,
)
from altera_api.calculation.wwf import (
    PHD_REFERENCE_SHARES,
    WWFRunResult,
    WWFRunVersions,
    calculate_wwf_run,
)

__all__ = [
    "DEFAULT_SPLIT_TOLERANCE",
    "PHD_REFERENCE_SHARES",
    "PTRunResult",
    "PTRunVersions",
    "WWFRunResult",
    "WWFRunVersions",
    "calculate_pt_run",
    "calculate_wwf_run",
]

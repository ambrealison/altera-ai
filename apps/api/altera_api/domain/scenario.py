"""Scenario domain models (Phase 26A).

Scenarios are deterministic, read-only projections — they never mutate
actual run data. Only Protein Tracker is supported in Phase 26A; WWF
scenario modelling is deferred to a future phase.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ScenarioOperationType(StrEnum):
    #: Move X kg from one PT methodology group to another.
    SHIFT_PROTEIN_BETWEEN_GROUPS = "shift_protein_between_groups"
    #: Add X kg to PLANT_BASED_CORE; total increases.
    INCREASE_PLANT_CORE_PROTEIN = "increase_plant_core_protein"
    #: Subtract X kg from ANIMAL_CORE; total decreases.
    REDUCE_ANIMAL_CORE_PROTEIN = "reduce_animal_core_protein"
    #: Re-attribute COMPOSITE_PRODUCTS protein between plant/animal by changing
    #: the default 50/50 split percentage. Does not alter composite protein_kg.
    IMPROVE_COMPOSITE_SPLIT = "improve_composite_split"


class ScenarioOperation(BaseModel):
    """A single parameterised transformation applied to a base run projection.

    Operations are executed in ascending ``order`` sequence. Parameters are
    free-form dicts validated at projection time by the engine.
    """

    id: UUID
    scenario_id: UUID
    operation_type: ScenarioOperationType
    #: Free-form parameters; validated at projection time, not here.
    parameters: dict[str, Any]
    rationale: str = ""
    #: Ascending execution order; lower values run first.
    order: int = 0


class Scenario(BaseModel):
    """A named what-if scenario attached to a base calculation run.

    Deliberately NOT a DomainBase: scenarios are mutable during authoring
    (status transitions, description edits) and do not require the strict
    coercion rules applied to immutable calculation artefacts.
    """

    id: UUID
    organisation_id: UUID
    project_id: UUID
    base_run_id: UUID
    name: str
    description: str = ""
    status: ScenarioStatus = ScenarioStatus.DRAFT
    #: Methodology tag. Only "protein_tracker" is supported in Phase 26A.
    methodology: str = "protein_tracker"
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class PTProjectedGroupAggregate(BaseModel):
    """Per-group comparison between base and projected protein figures."""

    pt_group: str
    base_protein_kg: Decimal
    projected_protein_kg: Decimal
    delta_protein_kg: Decimal


class PTProjectedSummary(BaseModel):
    """Headline before/after figures for a single PT scenario projection."""

    base_plant_protein_kg: Decimal
    base_animal_protein_kg: Decimal
    base_total_protein_kg: Decimal
    base_plant_share_pct: Decimal | None

    projected_plant_protein_kg: Decimal
    projected_animal_protein_kg: Decimal
    projected_total_protein_kg: Decimal
    projected_plant_share_pct: Decimal | None
    projected_animal_share_pct: Decimal | None

    delta_plant_protein_kg: Decimal
    delta_animal_protein_kg: Decimal
    #: Can be negative (plant share fell) or None when total is zero.
    delta_plant_share_pct: Decimal | None

    per_group: list[PTProjectedGroupAggregate]


class ScenarioResult(BaseModel):
    """The output of running a scenario projection engine against a base run."""

    scenario_id: UUID
    base_run_id: UUID
    methodology: str
    pt_projected: PTProjectedSummary | None = None
    warnings: list[str]
    created_at: datetime

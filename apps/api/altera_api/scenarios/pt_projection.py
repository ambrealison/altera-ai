"""Protein Tracker scenario projection engine (Phase 26A).

Takes a base ProteinTrackerCalculationSummary and a list of ScenarioOperations
and returns a ScenarioResult with projected values and deltas.

Guarantees:
- Base run data is never mutated.
- All operations are applied deterministically in `order` sequence.
- Negative projected values are clamped to zero with a warning emitted.
- Composite split operations are purely attributional — they move protein
  between plant/animal attribution within COMPOSITE_PRODUCTS only.
- No LLM, no randomness, no external calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
)
from altera_api.domain.scenario import (
    PTProjectedGroupAggregate,
    PTProjectedSummary,
    ScenarioOperation,
    ScenarioOperationType,
    ScenarioResult,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_HALF = Decimal("0.5")
_FOUR_DP = Decimal("0.0001")

#: The four methodology groups (excludes OUT_OF_SCOPE and UNKNOWN).
_METHODOLOGY_GROUPS: frozenset[str] = frozenset(
    g.value
    for g in (
        ProteinTrackerGroup.PLANT_BASED_CORE,
        ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        ProteinTrackerGroup.ANIMAL_CORE,
    )
)

#: Groups that contribute directly to plant protein (before composite split).
_PLANT_GROUPS: frozenset[str] = frozenset(
    g.value
    for g in (
        ProteinTrackerGroup.PLANT_BASED_CORE,
        ProteinTrackerGroup.PLANT_BASED_NON_CORE,
    )
)

_ANIMAL_GROUPS: frozenset[str] = frozenset({ProteinTrackerGroup.ANIMAL_CORE.value})
_COMPOSITE_GROUP: str = ProteinTrackerGroup.COMPOSITE_PRODUCTS.value


def _round4(value: Decimal) -> Decimal:
    """Round to 4 decimal places using ROUND_HALF_UP."""
    return value.quantize(_FOUR_DP, rounding=ROUND_HALF_UP)


def _parse_decimal(raw: object, field_name: str) -> tuple[Decimal | None, str | None]:
    """Attempt to parse *raw* as a Decimal.

    Returns ``(value, None)`` on success or ``(None, warning_message)`` on
    failure.
    """
    try:
        return Decimal(str(raw)), None
    except Exception:
        return None, f"Could not parse '{field_name}' as Decimal (got {raw!r}); operation skipped."


def project_pt_scenario(
    base: ProteinTrackerCalculationSummary,
    operations: list[ScenarioOperation],
    *,
    scenario_id: UUID,
) -> ScenarioResult:
    """Apply *operations* to *base* and return a fully-populated ScenarioResult.

    Parameter contracts per operation type
    ----------------------------------------
    SHIFT_PROTEIN_BETWEEN_GROUPS
        params: ``{"from_group": str, "to_group": str, "amount_kg": str}``
        - ``from_group`` and ``to_group`` must both be valid methodology group
          names (plant_based_core, plant_based_non_core, composite_products,
          animal_core).
        - ``amount_kg`` must be a Decimal-parseable non-negative string.
        - Moving from a group to the same group is a no-op (no warning).
        - Subtracts ``amount_kg`` from ``from_group`` and adds to ``to_group``.

    INCREASE_PLANT_CORE_PROTEIN
        params: ``{"amount_kg": str}``
        - Adds ``amount_kg`` to PLANT_BASED_CORE.
        - Total in-scope protein increases by the same amount.

    REDUCE_ANIMAL_CORE_PROTEIN
        params: ``{"amount_kg": str}``
        - Subtracts ``amount_kg`` from ANIMAL_CORE.
        - Total in-scope protein decreases by the same amount.

    IMPROVE_COMPOSITE_SPLIT
        params: ``{"plant_pct": str, "animal_pct": str}``
        - ``plant_pct`` and ``animal_pct`` must sum to exactly 100.
        - Re-attributes COMPOSITE_PRODUCTS protein between the plant and animal
          buckets using the supplied percentages instead of the default 50/50.
        - Does NOT change ``group_protein[composite_products]``; only the
          plant/animal headline split is affected.
        - Multiple IMPROVE_COMPOSITE_SPLIT operations are cumulative: each
          successive one replaces the current composite plant fraction.

    Clamping
    ---------
    After all operations any group protein value below zero is clamped to zero
    and a warning is emitted. Plant and animal headline figures are re-derived
    after clamping so they remain self-consistent.
    """
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Build mutable working state from base (no mutation of base objects)
    # ------------------------------------------------------------------
    # Per-group protein kg, keyed by ProteinTrackerGroup.value string.
    group_protein: dict[str, Decimal] = {}
    for agg in base.per_group:
        group_protein[agg.pt_group.value] = agg.protein_kg

    # Ensure all four methodology groups are present even if base omitted them.
    for g in _METHODOLOGY_GROUPS:
        group_protein.setdefault(g, _ZERO)

    # Composite plant attribution fraction (default 50 %).
    # Represents the fraction of composite_products protein that is counted
    # as plant protein.  Ranges [0, 1].
    composite_plant_fraction: Decimal = _HALF

    # ------------------------------------------------------------------
    # Apply operations in ascending order
    # ------------------------------------------------------------------
    sorted_ops = sorted(operations, key=lambda op: op.order)

    for op in sorted_ops:
        params = op.parameters

        if op.operation_type is ScenarioOperationType.SHIFT_PROTEIN_BETWEEN_GROUPS:
            # ----------------------------------------------------------
            # SHIFT_PROTEIN_BETWEEN_GROUPS
            # ----------------------------------------------------------
            from_group = params.get("from_group")
            to_group = params.get("to_group")
            amount_raw = params.get("amount_kg")

            if from_group not in _METHODOLOGY_GROUPS:
                warnings.append(
                    f"Operation {op.id} (shift_protein_between_groups): "
                    f"'from_group' {from_group!r} is not a valid methodology group; skipped."
                )
                continue
            if to_group not in _METHODOLOGY_GROUPS:
                warnings.append(
                    f"Operation {op.id} (shift_protein_between_groups): "
                    f"'to_group' {to_group!r} is not a valid methodology group; skipped."
                )
                continue

            amount, err = _parse_decimal(amount_raw, "amount_kg")
            if err:
                warnings.append(f"Operation {op.id} (shift_protein_between_groups): {err}")
                continue
            assert amount is not None  # narrowing for type checker

            if amount < _ZERO:
                warnings.append(
                    f"Operation {op.id} (shift_protein_between_groups): "
                    f"'amount_kg' must be non-negative (got {amount}); skipped."
                )
                continue

            if from_group == to_group:
                # Same-group shift is a no-op; no warning needed.
                continue

            group_protein[from_group] = group_protein[from_group] - amount
            group_protein[to_group] = group_protein[to_group] + amount

        elif op.operation_type is ScenarioOperationType.INCREASE_PLANT_CORE_PROTEIN:
            # ----------------------------------------------------------
            # INCREASE_PLANT_CORE_PROTEIN
            # ----------------------------------------------------------
            amount_raw = params.get("amount_kg")
            amount, err = _parse_decimal(amount_raw, "amount_kg")
            if err:
                warnings.append(f"Operation {op.id} (increase_plant_core_protein): {err}")
                continue
            assert amount is not None

            if amount < _ZERO:
                warnings.append(
                    f"Operation {op.id} (increase_plant_core_protein): "
                    f"'amount_kg' must be non-negative (got {amount}); skipped."
                )
                continue

            group_protein[ProteinTrackerGroup.PLANT_BASED_CORE.value] = (
                group_protein[ProteinTrackerGroup.PLANT_BASED_CORE.value] + amount
            )

        elif op.operation_type is ScenarioOperationType.REDUCE_ANIMAL_CORE_PROTEIN:
            # ----------------------------------------------------------
            # REDUCE_ANIMAL_CORE_PROTEIN
            # ----------------------------------------------------------
            amount_raw = params.get("amount_kg")
            amount, err = _parse_decimal(amount_raw, "amount_kg")
            if err:
                warnings.append(f"Operation {op.id} (reduce_animal_core_protein): {err}")
                continue
            assert amount is not None

            if amount < _ZERO:
                warnings.append(
                    f"Operation {op.id} (reduce_animal_core_protein): "
                    f"'amount_kg' must be non-negative (got {amount}); skipped."
                )
                continue

            group_protein[ProteinTrackerGroup.ANIMAL_CORE.value] = (
                group_protein[ProteinTrackerGroup.ANIMAL_CORE.value] - amount
            )

        elif op.operation_type is ScenarioOperationType.IMPROVE_COMPOSITE_SPLIT:
            # ----------------------------------------------------------
            # IMPROVE_COMPOSITE_SPLIT
            # ----------------------------------------------------------
            plant_pct_raw = params.get("plant_pct")
            animal_pct_raw = params.get("animal_pct")

            plant_pct, err = _parse_decimal(plant_pct_raw, "plant_pct")
            if err:
                warnings.append(f"Operation {op.id} (improve_composite_split): {err}")
                continue
            assert plant_pct is not None

            animal_pct, err = _parse_decimal(animal_pct_raw, "animal_pct")
            if err:
                warnings.append(f"Operation {op.id} (improve_composite_split): {err}")
                continue
            assert animal_pct is not None

            if plant_pct < _ZERO or animal_pct < _ZERO:
                warnings.append(
                    f"Operation {op.id} (improve_composite_split): "
                    "plant_pct and animal_pct must be non-negative; skipped."
                )
                continue

            if (plant_pct + animal_pct) != _HUNDRED:
                warnings.append(
                    f"Operation {op.id} (improve_composite_split): "
                    f"plant_pct ({plant_pct}) + animal_pct ({animal_pct}) must equal 100; skipped."
                )
                continue

            composite_plant_fraction = plant_pct / _HUNDRED

        else:
            warnings.append(
                f"Operation {op.id}: unknown operation_type "
                f"{op.operation_type!r}; skipped."
            )

    # ------------------------------------------------------------------
    # Clamp negatives to zero
    # ------------------------------------------------------------------
    for group_name in list(group_protein.keys()):
        if group_protein[group_name] < _ZERO:
            warnings.append(
                f"Group '{group_name}' projected protein_kg "
                f"({group_protein[group_name]}) clamped to 0."
            )
            group_protein[group_name] = _ZERO

    # ------------------------------------------------------------------
    # Derive projected headline plant / animal figures
    # ------------------------------------------------------------------
    composite_kg = group_protein[_COMPOSITE_GROUP]
    composite_plant_kg = composite_kg * composite_plant_fraction
    composite_animal_kg = composite_kg * (1 - composite_plant_fraction)

    projected_plant_protein_kg = (
        group_protein[ProteinTrackerGroup.PLANT_BASED_CORE.value]
        + group_protein[ProteinTrackerGroup.PLANT_BASED_NON_CORE.value]
        + composite_plant_kg
    )
    projected_animal_protein_kg = (
        group_protein[ProteinTrackerGroup.ANIMAL_CORE.value]
        + composite_animal_kg
    )
    projected_total_protein_kg = projected_plant_protein_kg + projected_animal_protein_kg

    if projected_total_protein_kg > _ZERO:
        projected_plant_share_pct: Decimal | None = _round4(
            projected_plant_protein_kg * _HUNDRED / projected_total_protein_kg
        )
        projected_animal_share_pct: Decimal | None = _round4(
            projected_animal_protein_kg * _HUNDRED / projected_total_protein_kg
        )
    else:
        projected_plant_share_pct = None
        projected_animal_share_pct = None

    # ------------------------------------------------------------------
    # Deltas
    # ------------------------------------------------------------------
    base_plant = base.plant_protein_kg
    base_animal = base.animal_protein_kg
    base_total = base.total_in_scope_protein_kg
    base_plant_share = base.plant_share_pct

    delta_plant_protein_kg = projected_plant_protein_kg - base_plant
    delta_animal_protein_kg = projected_animal_protein_kg - base_animal

    if projected_plant_share_pct is not None and base_plant_share is not None:
        delta_plant_share_pct: Decimal | None = _round4(
            projected_plant_share_pct - base_plant_share
        )
    else:
        delta_plant_share_pct = None

    # ------------------------------------------------------------------
    # Per-group comparison (only the four methodology groups)
    # ------------------------------------------------------------------
    # Base per-group lookup keyed by group value string.
    base_group_protein: dict[str, Decimal] = {
        agg.pt_group.value: agg.protein_kg for agg in base.per_group
    }
    for g in _METHODOLOGY_GROUPS:
        base_group_protein.setdefault(g, _ZERO)

    per_group_result: list[PTProjectedGroupAggregate] = [
        PTProjectedGroupAggregate(
            pt_group=g,
            base_protein_kg=base_group_protein[g],
            projected_protein_kg=group_protein[g],
            delta_protein_kg=group_protein[g] - base_group_protein[g],
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE.value,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE.value,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS.value,
            ProteinTrackerGroup.ANIMAL_CORE.value,
        )
    ]

    # ------------------------------------------------------------------
    # Assemble result
    # ------------------------------------------------------------------
    pt_projected = PTProjectedSummary(
        base_plant_protein_kg=base_plant,
        base_animal_protein_kg=base_animal,
        base_total_protein_kg=base_total,
        base_plant_share_pct=base_plant_share,
        projected_plant_protein_kg=projected_plant_protein_kg,
        projected_animal_protein_kg=projected_animal_protein_kg,
        projected_total_protein_kg=projected_total_protein_kg,
        projected_plant_share_pct=projected_plant_share_pct,
        projected_animal_share_pct=projected_animal_share_pct,
        delta_plant_protein_kg=delta_plant_protein_kg,
        delta_animal_protein_kg=delta_animal_protein_kg,
        delta_plant_share_pct=delta_plant_share_pct,
        per_group=per_group_result,
    )

    return ScenarioResult(
        scenario_id=scenario_id,
        base_run_id=base.run_id,
        methodology="protein_tracker",
        pt_projected=pt_projected,
        warnings=warnings,
        created_at=datetime.now(tz=UTC),
    )


"""Protein Tracker calculation.

Implements docs/calculation/protein-tracker-calculation.md verbatim:

* per-product ``volume_kg = weight_per_item_kg * items_purchased``
* per-product ``protein_kg = volume_kg * (protein_pct / 100)``
* per-group totals, headline ``plant_protein_kg`` / ``animal_protein_kg``,
  ``plant_share_pct`` / ``animal_share_pct``
* the optional per-product composite split extension (when
  ``plant_protein_pct + animal_protein_pct == protein_pct`` within
  tolerance), which removes the row's contribution from the composite
  pool before applying the 50/50 default to the remainder

All arithmetic uses ``Decimal`` quantised to 8 decimal places. Rounding
for display is the report layer's job; the calculator preserves
8-dp precision throughout.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct, ProteinSource
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationRow,
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
    ProteinTrackerProductClassification,
)

#: Quantisation step. 8 decimal places per docs/data/unit-conversion.md.
_EIGHT_DP = Decimal("0.00000001")
_ZERO = Decimal("0")
_HALF = Decimal("0.5")
_ONE_HUNDRED = Decimal("100")

#: How much ``plant_protein_pct + animal_protein_pct`` may diverge from
#: ``protein_pct`` and still be accepted as a per-product split. The
#: tolerance is small — these values are user-supplied product data, not
#: floating-point artefacts.
DEFAULT_SPLIT_TOLERANCE: Decimal = Decimal("0.0001")


def _q8(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_DP)


@dataclass(frozen=True)
class PTRunVersions:
    """Version stamps placed on every calculation row and on the summary.

    All four are mandatory: a calculation that cannot record its full
    provenance is not allowed to be persisted (see ADR-0004 and
    docs/calculation/versions-and-audit.md).
    """

    methodology_version: str
    methodology_source_edition: str
    taxonomy_version: str
    rules_version: str


@dataclass(frozen=True)
class PTRunResult:
    rows: tuple[ProteinTrackerCalculationRow, ...]
    summary: ProteinTrackerCalculationSummary


def calculate_pt_run(
    products: Sequence[NormalizedProduct],
    classifications: Mapping[UUID, ProteinTrackerProductClassification],
    *,
    run_id: UUID,
    reporting_period_label: str,
    versions: PTRunVersions,
    enable_per_product_split: bool = True,
    split_tolerance: Decimal = DEFAULT_SPLIT_TOLERANCE,
) -> PTRunResult:
    """Compute one Protein Tracker run.

    Every product passed in must have a corresponding entry in
    ``classifications`` keyed by ``product.id``. Products whose project
    does not enable PT (no ``pt_fields``) are skipped silently — the
    caller is responsible for filtering, but this guard is defensive.

    The result is a ``PTRunResult`` with per-row figures and a single
    aggregated ``ProteinTrackerCalculationSummary``.
    """
    rows: list[ProteinTrackerCalculationRow] = []

    group_volume: dict[ProteinTrackerGroup, Decimal] = {}
    group_protein: dict[ProteinTrackerGroup, Decimal] = {}
    group_count: dict[ProteinTrackerGroup, int] = {}

    direct_split_plant_kg = _ZERO
    direct_split_animal_kg = _ZERO
    composite_pool_protein_kg = _ZERO

    rows_with_per_product_split = 0
    rows_label = 0
    rows_reference_db = 0
    out_of_scope_count = 0
    unknown_count = 0

    for product in products:
        if product.pt_fields is None:
            # PT not enabled for this product on this project — caller
            # filtering missed; skip rather than blow up.
            continue
        if Methodology.PROTEIN_TRACKER not in product.methodologies_enabled:
            continue

        classification = classifications.get(product.id)
        if classification is None:
            raise ValueError(
                f"product {product.id} has no PT classification; classification "
                "must precede calculation."
            )

        pt_fields = product.pt_fields
        volume_kg = _q8(pt_fields.items_purchased * product.weight_per_item_kg)
        full_protein_kg = _q8(volume_kg * pt_fields.protein_pct / _ONE_HUNDRED)

        # Data-quality counters
        if pt_fields.protein_source is ProteinSource.LABEL:
            rows_label += 1
        else:
            rows_reference_db += 1

        pt_group = classification.pt_group
        in_scope = pt_group.is_methodology_group
        if pt_group is ProteinTrackerGroup.OUT_OF_SCOPE:
            out_of_scope_count += 1
        elif pt_group is ProteinTrackerGroup.UNKNOWN:
            unknown_count += 1

        # Per-product split detection (composites only)
        used_split = False
        row_plant_kg: Decimal | None = None
        row_animal_kg: Decimal | None = None
        if (
            in_scope
            and pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
            and enable_per_product_split
            and pt_fields.plant_protein_pct is not None
            and pt_fields.animal_protein_pct is not None
        ):
            split_sum = pt_fields.plant_protein_pct + pt_fields.animal_protein_pct
            if abs(split_sum - pt_fields.protein_pct) <= split_tolerance:
                row_plant_kg = _q8(volume_kg * pt_fields.plant_protein_pct / _ONE_HUNDRED)
                row_animal_kg = _q8(volume_kg * pt_fields.animal_protein_pct / _ONE_HUNDRED)
                used_split = True
                rows_with_per_product_split += 1
                direct_split_plant_kg += row_plant_kg
                direct_split_animal_kg += row_animal_kg

        # Aggregators (only in-scope rows contribute)
        if in_scope:
            group_volume[pt_group] = group_volume.get(pt_group, _ZERO) + volume_kg
            group_protein[pt_group] = group_protein.get(pt_group, _ZERO) + full_protein_kg
            group_count[pt_group] = group_count.get(pt_group, 0) + 1
            if pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS and not used_split:
                composite_pool_protein_kg += full_protein_kg

        # The domain model requires protein_kg == 0 for out-of-scope rows.
        # Volume is allowed to be non-zero; only protein has the zero rule.
        row_protein_kg = full_protein_kg if in_scope else _ZERO

        rows.append(
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product.id,
                in_scope=in_scope,
                pt_group=pt_group,
                volume_kg=_q8(volume_kg),
                protein_pct=_q8(pt_fields.protein_pct),
                protein_kg=_q8(row_protein_kg),
                used_per_product_split=used_split,
                plant_protein_kg=row_plant_kg,
                animal_protein_kg=row_animal_kg,
                methodology_version=versions.methodology_version,
                methodology_source_edition=versions.methodology_source_edition,
                taxonomy_version=versions.taxonomy_version,
                rules_version=versions.rules_version,
            )
        )

    # Always emit all four real groups, even when zero — the report
    # layer renders them either way and downstream code shouldn't have
    # to handle a missing key.
    per_group = tuple(
        ProteinTrackerGroupAggregate(
            pt_group=g,
            volume_kg=_q8(group_volume.get(g, _ZERO)),
            protein_kg=_q8(group_protein.get(g, _ZERO)),
            item_count=group_count.get(g, 0),
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    )

    # Headline plant / animal split. Composite pool (non-split rows
    # only) is divided 50/50.
    pool_half = _q8(composite_pool_protein_kg * _HALF)
    plant_50_50 = pool_half
    animal_50_50 = pool_half

    plant_protein_kg = _q8(
        group_protein.get(ProteinTrackerGroup.PLANT_BASED_CORE, _ZERO)
        + group_protein.get(ProteinTrackerGroup.PLANT_BASED_NON_CORE, _ZERO)
        + direct_split_plant_kg
        + plant_50_50
    )
    animal_protein_kg = _q8(
        group_protein.get(ProteinTrackerGroup.ANIMAL_CORE, _ZERO)
        + direct_split_animal_kg
        + animal_50_50
    )
    total_in_scope_protein_kg = _q8(plant_protein_kg + animal_protein_kg)

    if total_in_scope_protein_kg > _ZERO:
        plant_share_pct: Decimal | None = _q8(
            plant_protein_kg * _ONE_HUNDRED / total_in_scope_protein_kg
        )
        animal_share_pct: Decimal | None = _q8(
            animal_protein_kg * _ONE_HUNDRED / total_in_scope_protein_kg
        )
    else:
        plant_share_pct = None
        animal_share_pct = None

    summary = ProteinTrackerCalculationSummary(
        run_id=run_id,
        reporting_period_label=reporting_period_label,
        per_group=per_group,
        plant_protein_kg=plant_protein_kg,
        animal_protein_kg=animal_protein_kg,
        total_in_scope_protein_kg=total_in_scope_protein_kg,
        plant_share_pct=plant_share_pct,
        animal_share_pct=animal_share_pct,
        rows_with_per_product_split=rows_with_per_product_split,
        rows_protein_source_label=rows_label,
        rows_protein_source_reference_db=rows_reference_db,
        out_of_scope_count=out_of_scope_count,
        unknown_count=unknown_count,
        methodology_version=versions.methodology_version,
        methodology_source_edition=versions.methodology_source_edition,
        taxonomy_version=versions.taxonomy_version,
        rules_version=versions.rules_version,
    )

    return PTRunResult(rows=tuple(rows), summary=summary)

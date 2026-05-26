"""RawProduct → NormalizedProduct.

Applies methodology-aware requirements to a parsed row and produces a
persistent ``NormalizedProduct`` (or row-level validation errors when
methodology-required fields are missing).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RawProduct,
    WWFProductFields,
)
from altera_api.domain.validation import ValidationError, ValidationWarning


def _warning_missing_for(
    row_number: int, field: str, methodology: Methodology
) -> ValidationWarning:
    return ValidationWarning(
        row_number=row_number,
        field=field,
        code="missing_for_methodology",
        message=(
            f"{field} is required for methodology={methodology.value}; "
            f"row will be ingested without the {methodology.value} block"
        ),
    )


def normalize_product(
    raw: RawProduct,
    *,
    project_id: UUID,
    organisation_id: UUID,
    methodologies_enabled: frozenset[Methodology],
    now: datetime,
    product_id: UUID | None = None,
) -> tuple[NormalizedProduct | None, tuple[ValidationError, ...], tuple[ValidationWarning, ...]]:
    """Build a ``NormalizedProduct`` from a ``RawProduct``.

    Phase WWF-I-hotfix — when ``methodologies_enabled`` contains both PT
    and WWF and the row only has the data for one of them, the row is
    still ingested with the SATISFIABLE subset (PT-only or WWF-only)
    plus a warning explaining which methodology was dropped. Previously
    a single missing WWF field would zero-out the entire upload for a
    PT+WWF project — see ``tests/ingestion/
    test_phase_wwf_i_hotfix_dual_methodology.py``.

    The row still fails (returns ``None``) when:

      * ``weight_per_item_kg`` is missing (PT and WWF both need it);
      * neither PT nor WWF block can be built from the row's data;
      * the underlying ``NormalizedProduct`` Pydantic constructor
        rejects the row for any other reason.

    Returns ``(product, errors, warnings)``.
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    if raw.weight_per_item_kg is None:
        errors.append(
            ValidationError(
                row_number=raw.row_number,
                field="weight_per_item_kg",
                code="missing_required",
                message="weight_per_item_kg is required",
            )
        )

    pt_fields: PTProductFields | None = None
    wwf_fields: WWFProductFields | None = None

    pt_eligible = Methodology.PROTEIN_TRACKER in methodologies_enabled
    wwf_eligible = Methodology.WWF in methodologies_enabled

    # ----- PT block (or PT downgrade) -----
    if pt_eligible:
        pt_missing: list[str] = []
        if raw.items_purchased is None:
            pt_missing.append("items_purchased")
        if pt_missing:
            for field in pt_missing:
                warnings.append(
                    _warning_missing_for(
                        raw.row_number, field, Methodology.PROTEIN_TRACKER
                    )
                )
            pt_eligible = False  # downgrade — row stays in the upload
        else:
            if raw.protein_pct is None:
                warnings.append(
                    ValidationWarning(
                        row_number=raw.row_number,
                        field="protein_pct",
                        code="enrichment_needed",
                        message=(
                            "protein_pct is missing; product will be excluded from PT totals "
                            "unless nutrition enrichment is applied"
                        ),
                    )
                )
            try:
                pt_fields = PTProductFields(
                    items_purchased=raw.items_purchased,
                    protein_pct=raw.protein_pct,
                    protein_source=raw.protein_source or ProteinSource.REFERENCE_DB,
                    plant_protein_pct=raw.plant_protein_pct,
                    animal_protein_pct=raw.animal_protein_pct,
                )
            except PydanticValidationError as exc:
                for err in exc.errors():
                    errors.append(
                        ValidationError(
                            row_number=raw.row_number,
                            field=".".join(str(p) for p in err.get("loc", ())) or None,
                            code=err["type"],
                            message=err["msg"],
                        )
                    )

    # ----- WWF block (or WWF downgrade) -----
    if wwf_eligible:
        wwf_missing: list[str] = []
        if raw.items_sold is None:
            wwf_missing.append("items_sold")
        if raw.retail_channel is None:
            wwf_missing.append("retail_channel")
        if raw.is_own_brand is None:
            wwf_missing.append("is_own_brand")
        if wwf_missing:
            for field in wwf_missing:
                warnings.append(
                    _warning_missing_for(raw.row_number, field, Methodology.WWF)
                )
            wwf_eligible = False  # downgrade — row stays in the upload
        else:
            wwf_fields = WWFProductFields(
                items_sold=raw.items_sold,
                retail_channel=raw.retail_channel,
                is_own_brand=raw.is_own_brand,
            )

    # If the project had at least one methodology enabled but the row
    # can't satisfy ANY of them, the row is invalid — emit a structured
    # error explaining what was missing rather than silently dropping
    # the row with zero context.
    project_had_methodology = (
        Methodology.PROTEIN_TRACKER in methodologies_enabled
        or Methodology.WWF in methodologies_enabled
    )
    if project_had_methodology and not pt_eligible and not wwf_eligible:
        errors.append(
            ValidationError(
                row_number=raw.row_number,
                field=None,
                code="no_methodology_satisfiable",
                message=(
                    "row has no methodology block — PT required "
                    "(items_purchased) and WWF required (items_sold, "
                    "retail_channel, is_own_brand) are both missing"
                ),
            )
        )

    if errors:
        return None, tuple(errors), tuple(warnings)

    assert raw.weight_per_item_kg is not None  # checked above

    # Per-row methodologies_enabled is the SUBSET the row actually
    # satisfies (Phase WWF-I-hotfix). A PT+WWF project that receives
    # a row with only PT fields ingests the row as PT-only.
    row_methodologies = frozenset(
        m
        for m, on in (
            (Methodology.PROTEIN_TRACKER, pt_eligible),
            (Methodology.WWF, wwf_eligible),
        )
        if on
    )

    try:
        product = NormalizedProduct(
            id=product_id or uuid4(),
            upload_id=raw.upload_id,
            project_id=project_id,
            organisation_id=organisation_id,
            row_number=raw.row_number,
            external_product_id=raw.external_product_id,
            product_name=raw.product_name,
            brand=raw.brand,
            is_own_brand=raw.is_own_brand,
            retailer_category=raw.retailer_category,
            retailer_subcategory=raw.retailer_subcategory,
            ingredients_text=raw.ingredients_text,
            labels=raw.labels,
            language=raw.language,
            country=raw.country,
            weight_per_item_kg=raw.weight_per_item_kg,
            methodologies_enabled=row_methodologies,
            pt_fields=pt_fields,
            wwf_fields=wwf_fields,
            created_at=now,
        )
    except PydanticValidationError as exc:
        for err in exc.errors():
            errors.append(
                ValidationError(
                    row_number=raw.row_number,
                    field=".".join(str(p) for p in err.get("loc", ())) or None,
                    code=err["type"],
                    message=err["msg"],
                )
            )
        return None, tuple(errors), tuple(warnings)

    return product, (), tuple(warnings)

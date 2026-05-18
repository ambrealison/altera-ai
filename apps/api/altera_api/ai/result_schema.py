"""Classifier result schemas + JSON parsing.

The LLM is required to return strict JSON. The exact field shape is
methodology-specific:

* PT: a single ``pt_group`` enum.
* WWF: ``wwf_food_group`` plus per-FG sub-fields (matching the contract
  in docs/classification/ai-classifier.md).

The WWF contract uses two separate fields (``wwf_fg2_kind`` and
``wwf_fg2_dairy_class``) where the domain model collapses to a single
``WWFFG2Subgroup``. The result schema mirrors the LLM contract; a
``to_classification()`` adapter maps to the domain shape.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)


# ---------------------------------------------------------------------------
# AI-contract-only enums (separate from the domain's collapsed FG2 subgroup)
# ---------------------------------------------------------------------------
class WWFFG2Kind(StrEnum):
    DAIRY_ANIMAL = "dairy_animal"
    DAIRY_ALTERNATIVE_PLANT = "dairy_alternative_plant"


class WWFFG2DairyClass(StrEnum):
    CHEESE = "cheese"
    OTHER = "other"


class ResultParseError(Exception):
    """Raised when the LLM response cannot be turned into a valid result.

    Covers both raw-text JSON parse failures and Pydantic schema
    failures. The retry policy in :mod:`altera_api.ai.classifier` treats
    these uniformly — one failure is allowed, a second routes to manual
    review with reason ``ai_parse_failed``.
    """


# ---------------------------------------------------------------------------
# Base config for AI result models
# ---------------------------------------------------------------------------
class _ResultBase(BaseModel):
    """Forbid-extras + frozen. Strict mode *off* so LLM-text enums
    coerce cleanly."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )


# ---------------------------------------------------------------------------
# PT result
# ---------------------------------------------------------------------------
class PTClassifierResult(_ResultBase):
    methodology: Literal["protein_tracker"]
    pt_group: ProteinTrackerGroup
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=240)

    def to_classification(
        self,
        *,
        product_id: UUID,
        ai_prompt_version: str,
        ai_model: str,
        now: datetime,
    ) -> ProteinTrackerProductClassification:
        return ProteinTrackerProductClassification(
            product_id=product_id,
            pt_group=self.pt_group,
            source=ClassificationSource.AI,
            confidence=Decimal(str(self.confidence)),
            ai_prompt_version=ai_prompt_version,
            ai_model=ai_model,
            review_reason=self.rationale or None,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# WWF result
# ---------------------------------------------------------------------------
class WWFClassifierResult(_ResultBase):
    methodology: Literal["wwf"]
    wwf_food_group: WWFFoodGroup
    wwf_is_composite: bool = False
    wwf_fg1_subgroup: WWFFG1Subgroup | None = None
    wwf_fg2_kind: WWFFG2Kind | None = None
    wwf_fg2_dairy_class: WWFFG2DairyClass | None = None
    wwf_fg3_kind: WWFFG3Subgroup | None = None
    wwf_fg5_grain_kind: WWFFG5GrainKind | None = None
    wwf_fg7_kind: WWFFG7SnackKind | None = None
    wwf_composite_step1_bucket: WWFCompositeStep1Bucket | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _subgroup_required_for_food_group(self) -> Self:
        required = {
            WWFFoodGroup.FG1: ("wwf_fg1_subgroup", self.wwf_fg1_subgroup),
            WWFFoodGroup.FG2: ("wwf_fg2_kind", self.wwf_fg2_kind),
            WWFFoodGroup.FG3: ("wwf_fg3_kind", self.wwf_fg3_kind),
            WWFFoodGroup.FG5: ("wwf_fg5_grain_kind", self.wwf_fg5_grain_kind),
            WWFFoodGroup.FG7: ("wwf_fg7_kind", self.wwf_fg7_kind),
        }
        if self.wwf_food_group in required:
            name, value = required[self.wwf_food_group]
            if value is None:
                raise ValueError(
                    f"{name} is required when wwf_food_group={self.wwf_food_group.value}."
                )
        return self

    @model_validator(mode="after")
    def _subgroup_forbidden_for_other_food_group(self) -> Self:
        pairs = (
            ("wwf_fg1_subgroup", self.wwf_fg1_subgroup, WWFFoodGroup.FG1),
            ("wwf_fg2_kind", self.wwf_fg2_kind, WWFFoodGroup.FG2),
            ("wwf_fg3_kind", self.wwf_fg3_kind, WWFFoodGroup.FG3),
            ("wwf_fg5_grain_kind", self.wwf_fg5_grain_kind, WWFFoodGroup.FG5),
            ("wwf_fg7_kind", self.wwf_fg7_kind, WWFFoodGroup.FG7),
        )
        for name, value, parent in pairs:
            if value is not None and self.wwf_food_group is not parent:
                raise ValueError(f"{name} must be null when wwf_food_group != {parent.value}.")
        return self

    @model_validator(mode="after")
    def _fg2_dairy_class_consistency(self) -> Self:
        if self.wwf_fg2_kind is WWFFG2Kind.DAIRY_ANIMAL:
            if self.wwf_fg2_dairy_class is None:
                raise ValueError("wwf_fg2_dairy_class is required when wwf_fg2_kind=dairy_animal.")
        elif self.wwf_fg2_dairy_class is not None:
            raise ValueError(
                "wwf_fg2_dairy_class must be null when wwf_fg2_kind is not dairy_animal."
            )
        return self

    @model_validator(mode="after")
    def _composite_bucket_consistency(self) -> Self:
        if self.wwf_is_composite and self.wwf_composite_step1_bucket is None:
            raise ValueError("wwf_composite_step1_bucket is required when wwf_is_composite=true.")
        if not self.wwf_is_composite and self.wwf_composite_step1_bucket is not None:
            raise ValueError("wwf_composite_step1_bucket must be null when wwf_is_composite=false.")
        return self

    @model_validator(mode="after")
    def _system_states_have_no_subgroups(self) -> Self:
        if not self.wwf_food_group.is_methodology_group:
            subgroups = (
                self.wwf_fg1_subgroup,
                self.wwf_fg2_kind,
                self.wwf_fg2_dairy_class,
                self.wwf_fg3_kind,
                self.wwf_fg5_grain_kind,
                self.wwf_fg7_kind,
            )
            if any(s is not None for s in subgroups):
                raise ValueError(
                    "system states (out_of_scope, unknown) must have no subgroup fields."
                )
            if self.wwf_is_composite:
                raise ValueError("wwf_is_composite must be false for system states.")
        return self

    def _fg2_subgroup(self) -> WWFFG2Subgroup | None:
        if self.wwf_fg2_kind is None:
            return None
        if self.wwf_fg2_kind is WWFFG2Kind.DAIRY_ALTERNATIVE_PLANT:
            return WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT
        # dairy_animal — dairy_class is guaranteed non-null here
        assert self.wwf_fg2_dairy_class is not None
        if self.wwf_fg2_dairy_class is WWFFG2DairyClass.CHEESE:
            return WWFFG2Subgroup.CHEESE
        return WWFFG2Subgroup.OTHER_DAIRY_ANIMAL

    def to_classification(
        self,
        *,
        product_id: UUID,
        ai_prompt_version: str,
        ai_model: str,
        now: datetime,
    ) -> WWFProductClassification:
        return WWFProductClassification(
            product_id=product_id,
            wwf_food_group=self.wwf_food_group,
            wwf_is_composite=self.wwf_is_composite,
            fg1_subgroup=self.wwf_fg1_subgroup,
            fg2_subgroup=self._fg2_subgroup(),
            fg3_subgroup=self.wwf_fg3_kind,
            fg5_grain_kind=self.wwf_fg5_grain_kind,
            fg7_snack_kind=self.wwf_fg7_kind,
            composite_step1_bucket=self.wwf_composite_step1_bucket,
            source=ClassificationSource.AI,
            confidence=Decimal(str(self.confidence)),
            ai_prompt_version=ai_prompt_version,
            ai_model=ai_model,
            review_reason=self.rationale or None,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _trim_to_outer_braces(text: str) -> str:
    """Return ``text[first '{' : last '}' + 1]``, or raise ``ResultParseError``.

    Permissive enough to tolerate markdown fences (```json … ```), but
    not permissive enough to assemble fragments — the strict Pydantic
    schema is the actual guarantee of safety.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ResultParseError("no JSON object found in response")
    return text[start : end + 1]


def parse_classifier_response(
    raw_text: str,
    methodology: Methodology,
) -> PTClassifierResult | WWFClassifierResult:
    """Parse and validate an LLM response.

    Raises :class:`ResultParseError` on any failure (JSON, schema,
    methodology mismatch). The classifier orchestrator interprets one
    failure as "retry", a second as ``ai_parse_failed``.
    """
    trimmed = _trim_to_outer_braces(raw_text)
    try:
        payload = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise ResultParseError(f"JSON decode failed: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ResultParseError("top-level JSON must be an object")

    declared = payload.get("methodology")
    if declared != methodology.value:
        raise ResultParseError(
            f"methodology={declared!r} does not match expected {methodology.value!r}"
        )

    try:
        if methodology is Methodology.PROTEIN_TRACKER:
            return PTClassifierResult.model_validate(payload)
        return WWFClassifierResult.model_validate(payload)
    except PydanticValidationError as exc:
        raise ResultParseError(f"schema validation failed: {exc.error_count()} errors") from exc

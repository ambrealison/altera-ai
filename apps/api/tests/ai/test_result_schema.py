from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from altera_api.ai.result_schema import (
    PTClassifierResult,
    ResultParseError,
    WWFClassifierResult,
    WWFFG2DairyClass,
    WWFFG2Kind,
    parse_classifier_response,
)
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG5GrainKind,
    WWFFoodGroup,
)


def _pt(**overrides) -> str:
    payload = {
        "methodology": "protein_tracker",
        "pt_group": "plant_based_core",
        "confidence": 0.92,
        "rationale": "red lentils are a pulse",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _wwf(**overrides) -> str:
    payload = {
        "methodology": "wwf",
        "wwf_food_group": "FG1",
        "wwf_is_composite": False,
        "wwf_fg1_subgroup": "red_meat",
        "confidence": 0.92,
        "rationale": "beef mince",
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestPTParsing:
    def test_parses_valid(self) -> None:
        result = parse_classifier_response(_pt(), Methodology.PROTEIN_TRACKER)
        assert isinstance(result, PTClassifierResult)
        assert result.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert result.confidence == 0.92

    def test_trims_markdown_fence(self) -> None:
        raw = f"```json\n{_pt()}\n```"
        result = parse_classifier_response(raw, Methodology.PROTEIN_TRACKER)
        assert isinstance(result, PTClassifierResult)

    def test_rejects_non_json(self) -> None:
        with pytest.raises(ResultParseError, match="no JSON object"):
            parse_classifier_response("plainly invalid", Methodology.PROTEIN_TRACKER)

    def test_rejects_wrong_methodology(self) -> None:
        wwf_payload = _wwf()
        with pytest.raises(ResultParseError, match="methodology"):
            parse_classifier_response(wwf_payload, Methodology.PROTEIN_TRACKER)

    def test_rejects_unknown_pt_group(self) -> None:
        with pytest.raises(ResultParseError, match="schema validation"):
            parse_classifier_response(
                _pt(pt_group="bogus"), Methodology.PROTEIN_TRACKER
            )

    def test_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ResultParseError):
            parse_classifier_response(
                _pt(confidence=1.5), Methodology.PROTEIN_TRACKER
            )

    def test_rationale_truncation_rejected(self) -> None:
        with pytest.raises(ResultParseError):
            parse_classifier_response(
                _pt(rationale="x" * 500), Methodology.PROTEIN_TRACKER
            )


class TestPTToClassification:
    def test_to_classification(self) -> None:
        now = datetime(2026, 5, 15)
        product_id = UUID("00000000-0000-0000-0000-000000000001")
        result = parse_classifier_response(
            _pt(confidence=0.91), Methodology.PROTEIN_TRACKER
        )
        assert isinstance(result, PTClassifierResult)
        c = result.to_classification(
            product_id=product_id,
            ai_prompt_version="classifier_v1",
            ai_model="fake-model-1",
            now=now,
        )
        assert c.source is ClassificationSource.AI
        assert c.confidence == Decimal("0.91")
        assert c.ai_prompt_version == "classifier_v1"
        assert c.ai_model == "fake-model-1"


class TestWWFParsing:
    def test_parses_valid_fg1(self) -> None:
        result = parse_classifier_response(_wwf(), Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        assert result.wwf_fg1_subgroup is WWFFG1Subgroup.RED_MEAT

    def test_parses_fg2_dairy_animal_cheese(self) -> None:
        raw = _wwf(
            wwf_food_group="FG2",
            wwf_fg1_subgroup=None,
            wwf_fg2_kind="dairy_animal",
            wwf_fg2_dairy_class="cheese",
        )
        result = parse_classifier_response(raw, Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        assert result.wwf_fg2_kind is WWFFG2Kind.DAIRY_ANIMAL
        assert result.wwf_fg2_dairy_class is WWFFG2DairyClass.CHEESE

    def test_fg1_requires_subgroup(self) -> None:
        raw = _wwf(wwf_fg1_subgroup=None)
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)

    def test_fg2_dairy_animal_requires_dairy_class(self) -> None:
        raw = _wwf(
            wwf_food_group="FG2",
            wwf_fg1_subgroup=None,
            wwf_fg2_kind="dairy_animal",
            # wwf_fg2_dairy_class missing
        )
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)

    def test_plant_alt_must_not_have_dairy_class(self) -> None:
        raw = _wwf(
            wwf_food_group="FG2",
            wwf_fg1_subgroup=None,
            wwf_fg2_kind="dairy_alternative_plant",
            wwf_fg2_dairy_class="cheese",
        )
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)

    def test_composite_requires_bucket(self) -> None:
        raw = _wwf(wwf_is_composite=True)
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)

    def test_composite_with_bucket_ok(self) -> None:
        raw = _wwf(
            wwf_is_composite=True,
            wwf_composite_step1_bucket="meat_based",
        )
        result = parse_classifier_response(raw, Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        assert result.wwf_composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED

    def test_subgroup_for_wrong_food_group_rejected(self) -> None:
        raw = _wwf(wwf_food_group="FG4", wwf_fg1_subgroup="red_meat")
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)

    def test_system_state_no_subgroups(self) -> None:
        raw = _wwf(wwf_food_group="out_of_scope", wwf_fg1_subgroup="red_meat")
        with pytest.raises(ResultParseError):
            parse_classifier_response(raw, Methodology.WWF)


class TestWWFToClassification:
    def test_fg2_cheese_collapses_to_fg2_subgroup_cheese(self) -> None:
        now = datetime(2026, 5, 15)
        product_id = UUID("00000000-0000-0000-0000-000000000002")
        raw = _wwf(
            wwf_food_group="FG2",
            wwf_fg1_subgroup=None,
            wwf_fg2_kind="dairy_animal",
            wwf_fg2_dairy_class="cheese",
        )
        result = parse_classifier_response(raw, Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        c = result.to_classification(
            product_id=product_id,
            ai_prompt_version="classifier_v1",
            ai_model="fake-model-2",
            now=now,
        )
        assert c.wwf_food_group is WWFFoodGroup.FG2
        assert c.fg2_subgroup is WWFFG2Subgroup.CHEESE

    def test_fg2_plant_alt_collapses_correctly(self) -> None:
        raw = _wwf(
            wwf_food_group="FG2",
            wwf_fg1_subgroup=None,
            wwf_fg2_kind="dairy_alternative_plant",
        )
        result = parse_classifier_response(raw, Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        c = result.to_classification(
            product_id=UUID("00000000-0000-0000-0000-000000000003"),
            ai_prompt_version="classifier_v1",
            ai_model="m",
            now=datetime(2026, 5, 15),
        )
        assert c.fg2_subgroup is WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT

    def test_fg5_whole_grain(self) -> None:
        raw = _wwf(
            wwf_food_group="FG5",
            wwf_fg1_subgroup=None,
            wwf_fg5_grain_kind="whole_grain",
        )
        result = parse_classifier_response(raw, Methodology.WWF)
        assert isinstance(result, WWFClassifierResult)
        c = result.to_classification(
            product_id=UUID("00000000-0000-0000-0000-000000000004"),
            ai_prompt_version="classifier_v1",
            ai_model="m",
            now=datetime(2026, 5, 15),
        )
        assert c.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

from __future__ import annotations

import pytest

from altera_api.ai.fakes import (
    EventuallyValidFakeProvider,
    FailingFakeProvider,
    KeywordFakeProvider,
    RaisingFakeProvider,
    ScriptedFakeProvider,
    StaticFakeProvider,
)
from altera_api.ai.prompt_builder import build_classifier_prompt
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ProviderError
from altera_api.domain.common import Methodology


def _prompt(name: str = "Mystery") -> object:
    return build_classifier_prompt(
        ClassifierPromptInput(product_name=name), Methodology.PROTEIN_TRACKER
    )


class TestStaticFake:
    def test_returns_configured_text(self) -> None:
        p = StaticFakeProvider(raw_text='{"hello": "world"}')
        r = p.classify(_prompt())  # type: ignore[arg-type]
        assert r.raw_text == '{"hello": "world"}'
        assert r.model == "fake-static-v1"


class TestKeywordFake:
    def test_picks_matching_rule(self) -> None:
        p = KeywordFakeProvider(
            rules={"lentil": '{"pt_group":"plant_based_core"}'},
            default='{"pt_group":"unknown"}',
        )
        r = p.classify(_prompt("Red Lentil Soup"))  # type: ignore[arg-type]
        assert "plant_based_core" in r.raw_text

    def test_falls_back_to_default(self) -> None:
        p = KeywordFakeProvider(
            rules={"beef": '{"pt_group":"animal_core"}'},
            default='{"pt_group":"unknown"}',
        )
        r = p.classify(_prompt("Tofu Block"))  # type: ignore[arg-type]
        assert "unknown" in r.raw_text


class TestFailingFake:
    def test_always_returns_invalid_json(self) -> None:
        p = FailingFakeProvider()
        r = p.classify(_prompt())  # type: ignore[arg-type]
        assert "not json" in r.raw_text


class TestEventuallyValidFake:
    def test_first_call_invalid_then_valid(self) -> None:
        p = EventuallyValidFakeProvider(
            valid_text='{"methodology":"protein_tracker"}',
            invalid_calls=1,
        )
        prompt = _prompt()
        assert "not json" in p.classify(prompt).raw_text  # type: ignore[arg-type]
        assert "protein_tracker" in p.classify(prompt).raw_text  # type: ignore[arg-type]


class TestRaisingFake:
    def test_raises_provider_error(self) -> None:
        p = RaisingFakeProvider(message="boom")
        with pytest.raises(ProviderError, match="boom"):
            p.classify(_prompt())  # type: ignore[arg-type]


class TestScriptedFake:
    def test_yields_in_order(self) -> None:
        p = ScriptedFakeProvider(responses=("a", "b", "c"))
        prompt = _prompt()
        assert p.classify(prompt).raw_text == "a"  # type: ignore[arg-type]
        assert p.classify(prompt).raw_text == "b"  # type: ignore[arg-type]
        assert p.classify(prompt).raw_text == "c"  # type: ignore[arg-type]

    def test_exhausted_raises_provider_error(self) -> None:
        p = ScriptedFakeProvider(responses=("a",))
        prompt = _prompt()
        p.classify(prompt)  # type: ignore[arg-type]
        with pytest.raises(ProviderError, match="exhausted"):
            p.classify(prompt)  # type: ignore[arg-type]

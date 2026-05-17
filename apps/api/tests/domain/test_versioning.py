from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology
from altera_api.domain.versioning import (
    MethodologySourceEdition,
    MethodologyVersion,
    RulesVersion,
    SemverVersion,
    TaxonomyVersion,
)


class TestSemverVersion:
    def test_creates_valid_version(self) -> None:
        v = SemverVersion(major=1, minor=2, patch=3)
        assert v.as_string() == "1.2.3"

    def test_parse_round_trip(self) -> None:
        v = SemverVersion.parse("1.0.0")
        assert (v.major, v.minor, v.patch) == (1, 0, 0)

    def test_parse_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            SemverVersion.parse("1.0")
        with pytest.raises(ValueError):
            SemverVersion.parse("v1.0.0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(PydanticValidationError):
            SemverVersion(major=-1, minor=0, patch=0)

    def test_no_string_coercion(self) -> None:
        # strict mode: ints required, not "1"
        with pytest.raises(PydanticValidationError):
            SemverVersion(major="1", minor=0, patch=0)  # type: ignore[arg-type]


class TestMethodologySourceEdition:
    def test_creates_pt_edition(self) -> None:
        e = MethodologySourceEdition(
            methodology=Methodology.PROTEIN_TRACKER,
            citation="GPA & ProVeg Foodservice 2024-08",
            year=2024,
        )
        assert e.methodology is Methodology.PROTEIN_TRACKER

    def test_creates_wwf_edition(self) -> None:
        e = MethodologySourceEdition(
            methodology=Methodology.WWF,
            citation="WWF Food Practice 2024 (Planet-Based Diets)",
            year=2024,
        )
        assert e.methodology is Methodology.WWF

    def test_rejects_year_out_of_range(self) -> None:
        with pytest.raises(PydanticValidationError):
            MethodologySourceEdition(
                methodology=Methodology.WWF, citation="WWF 1999", year=1999
            )


class TestMethodologyVersion:
    def test_creates_valid(self) -> None:
        mv = MethodologyVersion(
            methodology=Methodology.PROTEIN_TRACKER,
            version=SemverVersion(major=1, minor=0, patch=0),
            source_edition=MethodologySourceEdition(
                methodology=Methodology.PROTEIN_TRACKER,
                citation="GPA & ProVeg Foodservice 2024-08",
                year=2024,
            ),
        )
        assert mv.version.as_string() == "1.0.0"

    def test_rejects_mismatched_methodology(self) -> None:
        with pytest.raises(PydanticValidationError):
            MethodologyVersion(
                methodology=Methodology.PROTEIN_TRACKER,
                version=SemverVersion(major=1, minor=0, patch=0),
                source_edition=MethodologySourceEdition(
                    methodology=Methodology.WWF,
                    citation="WWF 2024",
                    year=2024,
                ),
            )


class TestTaxonomyAndRulesVersion:
    def test_taxonomy_version_creates(self) -> None:
        t = TaxonomyVersion(version=SemverVersion(major=1, minor=0, patch=0))
        assert t.version.major == 1

    def test_rules_version_rejects_zero_zero_zero(self) -> None:
        with pytest.raises(PydanticValidationError):
            RulesVersion(version=SemverVersion(major=0, minor=0, patch=0))

    def test_rules_version_accepts_zero_one_zero(self) -> None:
        r = RulesVersion(version=SemverVersion(major=0, minor=1, patch=0))
        assert r.version.as_string() == "0.1.0"


class TestImmutability:
    def test_models_are_frozen(self) -> None:
        v = SemverVersion(major=1, minor=0, patch=0)
        with pytest.raises(PydanticValidationError):
            v.major = 2  # type: ignore[misc]

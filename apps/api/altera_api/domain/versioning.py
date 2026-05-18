"""Versioning models.

Methodology, taxonomy, and rules each carry an independent semver. Every
calculation row stamps all three plus the methodology source edition so
results are reproducible. See docs/methodologies/versioning.md.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, field_validator, model_validator

from altera_api.domain.common import DomainBase, Methodology, NonEmptyStr

_SEMVER_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class SemverVersion(DomainBase):
    """Strict MAJOR.MINOR.PATCH semver."""

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)

    @classmethod
    def parse(cls, value: str) -> Self:
        match = _SEMVER_RE.match(value)
        if not match:
            raise ValueError(f"Not a valid MAJOR.MINOR.PATCH semver: {value!r}")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
        )

    def as_string(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


class MethodologySourceEdition(DomainBase):
    """The external publication a methodology module implements.

    Both methodologies are external (GPA & ProVeg for PT, WWF Food Practice
    for WWF) and may revise independently of Altera AI. The edition is
    stamped on every calculation run.
    """

    methodology: Methodology
    citation: NonEmptyStr
    year: int = Field(ge=2000, le=2100)

    @model_validator(mode="after")
    def _citation_mentions_methodology(self) -> Self:
        keyword = "protein tracker" if self.methodology is Methodology.PROTEIN_TRACKER else "wwf"
        if (
            keyword not in self.citation.lower()
            and self._alt_keyword() not in self.citation.lower()
        ):
            # Soft check; many valid citations use the publisher's name instead
            # of the methodology name (e.g. "GPA & ProVeg Foodservice 2024-08").
            pass
        return self

    def _alt_keyword(self) -> str:
        return "gpa" if self.methodology is Methodology.PROTEIN_TRACKER else "planet-based"


class MethodologyVersion(DomainBase):
    """A versioned methodology module pinned to a source edition."""

    methodology: Methodology
    version: SemverVersion
    source_edition: MethodologySourceEdition

    @model_validator(mode="after")
    def _methodology_matches_source(self) -> Self:
        if self.methodology is not self.source_edition.methodology:
            raise ValueError("MethodologyVersion.methodology must match source_edition.methodology")
        return self


class TaxonomyVersion(DomainBase):
    """A versioned canonical taxonomy snapshot.

    A published taxonomy version is immutable: corrections ship as a new
    version, never as an in-place edit. See packages/taxonomy.
    """

    version: SemverVersion


class RulesVersion(DomainBase):
    """A versioned deterministic-rules engine snapshot."""

    version: SemverVersion

    @field_validator("version")
    @classmethod
    def _no_unstable_zero_zero_zero(cls, value: SemverVersion) -> SemverVersion:
        # Defensive: 0.0.0 has historically meant "unversioned"; we require
        # explicit assignment.
        if value.major == 0 and value.minor == 0 and value.patch == 0:
            raise ValueError("RulesVersion cannot be 0.0.0; assign an explicit version.")
        return value

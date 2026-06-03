"""Phase Quality-V2-E — controlled NEVO matcher factory.

A single, explicit selection layer for the three NEVO matchers so we can
prepare V2 embeddings for *controlled* activation without changing any
production behaviour:

  * ``v1``            — the current PRODUCTION matcher (the AI
                        ``nutrition_matcher.propose_match`` path). This is
                        the default and the only matcher any route uses.
  * ``v2-rules``      — the offline precision-first rule gate
                        (``nevo_rules.gate_candidate``). No embeddings, no
                        network — usable anywhere.
  * ``v2-embeddings`` — V2 rules + the vector candidate index
                        (``nevo_pipeline.decide_with_embeddings``).

Hard safety contract
--------------------
* The factory DEFAULTS to ``v1``. With ``ALTERA_NEVO_MATCHER_VERSION``
  unset (or any unrecognised value) it returns the V1 production matcher.
* ``v2-embeddings`` requires ``ALTERA_ENABLE_EMBEDDINGS=true`` in normal
  (production) mode; otherwise the factory raises ``NevoMatcherError``
  instead of silently falling back to the fake provider. Offline
  evaluation/benchmarks pass ``evaluator_mode=True`` to opt into the
  deterministic fake when embeddings are disabled.
* NO production route imports this module today; selecting a non-V1
  matcher is therefore an explicit, opt-in dev/evaluator action.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from altera_api.classification_v2.nevo_index import NevoVectorIndex
from altera_api.classification_v2.nevo_pipeline import NevoDecision, decide_with_embeddings
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    NevoGateResult,
    gate_candidate,
)
from altera_api.quality_config import embeddings_enabled

_MATCHER_ENV = "ALTERA_NEVO_MATCHER_VERSION"


class NevoMatcherVersion(StrEnum):
    V1 = "v1"
    V2_RULES = "v2-rules"
    V2_EMBEDDINGS = "v2-embeddings"


class NevoMatcherError(RuntimeError):
    """Raised when a matcher is requested but its preconditions are unmet
    (e.g. ``v2-embeddings`` without embeddings enabled)."""


_ALIASES = {
    "v1": NevoMatcherVersion.V1,
    "v2-rules": NevoMatcherVersion.V2_RULES,
    "v2_rules": NevoMatcherVersion.V2_RULES,
    "v2rules": NevoMatcherVersion.V2_RULES,
    "v2-embeddings": NevoMatcherVersion.V2_EMBEDDINGS,
    "v2_embeddings": NevoMatcherVersion.V2_EMBEDDINGS,
    "v2embeddings": NevoMatcherVersion.V2_EMBEDDINGS,
    # Bare "v2" historically meant the rule engine (non-embedding).
    "v2": NevoMatcherVersion.V2_RULES,
}


def resolve_nevo_matcher_version(
    raw: str | NevoMatcherVersion | None = None,
) -> NevoMatcherVersion:
    """Resolve the configured matcher version. Defaults to ``v1``.

    With ``raw`` None it reads ``ALTERA_NEVO_MATCHER_VERSION``. Any
    unrecognised value falls back to ``v1`` — fail safe, never crash."""
    if isinstance(raw, NevoMatcherVersion):
        return raw
    value = (raw if raw is not None else os.environ.get(_MATCHER_ENV) or "").strip().lower()
    return _ALIASES.get(value, NevoMatcherVersion.V1)


# ---------------------------------------------------------------------------
# Matcher wrappers — a thin uniform surface over each pipeline.
# ---------------------------------------------------------------------------
@dataclass
class V1NevoMatcher:
    """The production NEVO matcher (AI candidate selection). Lazily
    delegates to ``altera_api.ai.nutrition_matcher.propose_match`` so this
    module imports with no AI/network side effects."""

    version: NevoMatcherVersion = NevoMatcherVersion.V1
    is_production_default: bool = True

    def propose_match(self, **kwargs: Any) -> Any:
        from altera_api.ai.nutrition_matcher import propose_match

        return propose_match(**kwargs)


@dataclass
class V2RulesNevoMatcher:
    """Offline precision-first rule gate (no embeddings)."""

    version: NevoMatcherVersion = NevoMatcherVersion.V2_RULES
    is_production_default: bool = False

    def gate(self, product_name: str, candidate: NevoCandidate) -> NevoGateResult:
        return gate_candidate(product_name, candidate)


@dataclass
class V2EmbeddingsNevoMatcher:
    """V2 rules + vector candidate index. Requires an attached
    :class:`NevoVectorIndex` to decide (the caller builds it with the
    chosen provider/references)."""

    index: NevoVectorIndex | None = None
    version: NevoMatcherVersion = NevoMatcherVersion.V2_EMBEDDINGS
    is_production_default: bool = False

    def attach_index(self, index: NevoVectorIndex) -> None:
        self.index = index

    def decide(self, product: dict[str, Any], *, top_k: int | None = None) -> NevoDecision:
        if self.index is None:
            raise NevoMatcherError(
                "v2-embeddings matcher has no vector index attached. Build a "
                "NevoVectorIndex (with the chosen provider + references) and "
                "pass it via get_nevo_matcher(index=...) or attach_index()."
            )
        return decide_with_embeddings(product, self.index, top_k=top_k)


def get_nevo_matcher(
    version: str | NevoMatcherVersion | None = None,
    *,
    index: NevoVectorIndex | None = None,
    evaluator_mode: bool = False,
) -> V1NevoMatcher | V2RulesNevoMatcher | V2EmbeddingsNevoMatcher:
    """Return the matcher for ``version`` (default: env / ``v1``).

    ``v2-embeddings`` requires ``ALTERA_ENABLE_EMBEDDINGS=true`` unless
    ``evaluator_mode=True`` (offline/dev), in which case the deterministic
    fake provider is acceptable. The factory never silently swaps a real
    provider for the fake in production — it raises instead.
    """
    resolved = resolve_nevo_matcher_version(version)
    if resolved is NevoMatcherVersion.V1:
        return V1NevoMatcher()
    if resolved is NevoMatcherVersion.V2_RULES:
        return V2RulesNevoMatcher()

    # v2-embeddings
    if not embeddings_enabled() and not evaluator_mode:
        raise NevoMatcherError(
            "v2-embeddings requires ALTERA_ENABLE_EMBEDDINGS=true. It is not "
            "active in production; for offline/dev evaluation call "
            "get_nevo_matcher('v2-embeddings', evaluator_mode=True)."
        )
    return V2EmbeddingsNevoMatcher(index=index)

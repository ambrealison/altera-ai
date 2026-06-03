"""Phase Quality-V2-A — V1/V2 pipeline feature flags.

We are building a second-generation categorization + NEVO-matching
stack (rule engine + embeddings + evaluation harness) WITHOUT touching
the production V1 behaviour. Demos run on V1; V2 is opt-in.

Safe defaults
-------------
- ``ALTERA_CLASSIFICATION_PIPELINE_VERSION`` → ``v1`` (default).
- ``ALTERA_NEVO_MATCHER_VERSION``            → ``v1`` (default).
- ``ALTERA_ENABLE_EMBEDDINGS``               → ``false`` (default).
- ``ALTERA_ENABLE_V2_EVALUATION``            → ``false`` (default).

Coexistence contract
--------------------
- Production routes read NOTHING from this module yet — they keep
  calling the V1 guards directly. V1 output is unchanged.
- V2 code (rule engine, embeddings) is reachable only from evaluator
  scripts and tests, or from code that explicitly checks these flags.
- A misbehaving V2 can never affect a demo: unless an operator sets
  the env var to ``v2``, every getter returns V1.

This module performs no I/O and has no side effects; it just reads
``os.environ`` on demand so tests can monkeypatch the environment.
"""

from __future__ import annotations

import os
from enum import StrEnum


class PipelineVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


class MatcherVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


_PIPELINE_ENV = "ALTERA_CLASSIFICATION_PIPELINE_VERSION"
_MATCHER_ENV = "ALTERA_NEVO_MATCHER_VERSION"
_EMBEDDINGS_ENV = "ALTERA_ENABLE_EMBEDDINGS"
_V2_EVAL_ENV = "ALTERA_ENABLE_V2_EVALUATION"
# Phase Quality-V2-C — embedding provider selection.
_EMBEDDING_PROVIDER_ENV = "ALTERA_EMBEDDING_PROVIDER"
_EMBEDDING_MODEL_ENV = "ALTERA_EMBEDDING_MODEL"
_EMBEDDING_DIMENSIONS_ENV = "ALTERA_EMBEDDING_DIMENSIONS"

#: Default Voyage model when the voyage provider is selected.
#: Phase Quality-V2-E: voyage-4-lite matched voyage-4 on the fixture
#: benchmark (top1/top5/top20 = 100%, 0 high-conf FP, 100% forbidden
#: rejection) at a lower price, so it is the recommended default. Override
#: with ALTERA_EMBEDDING_MODEL=voyage-4 only if the full-NEVO benchmark
#: shows voyage-4 is materially better. (Embeddings remain disabled by
#: default; this model name is only consulted when the voyage provider is
#: explicitly enabled.)
DEFAULT_EMBEDDING_MODEL = "voyage-4-lite"


def _parse_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def classification_pipeline_version() -> PipelineVersion:
    """Resolve the configured classification pipeline. Defaults to V1.

    Any unrecognised value falls back to V1 — fail safe, never crash a
    demo because of a typo in an env var.
    """
    raw = (os.environ.get(_PIPELINE_ENV) or "").strip().lower()
    return PipelineVersion.V2 if raw == "v2" else PipelineVersion.V1


def nevo_matcher_version() -> MatcherVersion:
    """Resolve the configured NEVO matcher. Defaults to V1."""
    raw = (os.environ.get(_MATCHER_ENV) or "").strip().lower()
    return MatcherVersion.V2 if raw == "v2" else MatcherVersion.V1


def embeddings_enabled() -> bool:
    """True only when embeddings are explicitly enabled. Default False
    so the normal test suite + production never make network calls."""
    return _parse_bool(os.environ.get(_EMBEDDINGS_ENV), default=False)


def v2_evaluation_enabled() -> bool:
    """Gate for V2 evaluation in non-script contexts. Default False."""
    return _parse_bool(os.environ.get(_V2_EVAL_ENV), default=False)


def embedding_provider_name() -> str:
    """Selected embedding provider. Defaults to ``fake`` (offline,
    deterministic, no network). An unrecognised value falls back to
    ``fake`` — fail safe."""
    raw = (os.environ.get(_EMBEDDING_PROVIDER_ENV) or "").strip().lower()
    return raw if raw in {"fake", "voyage"} else "fake"


def embedding_model() -> str:
    """Configured embedding model (default: the Voyage lite model).
    Only consulted by the voyage provider; the fake provider ignores it."""
    raw = (os.environ.get(_EMBEDDING_MODEL_ENV) or "").strip()
    return raw or DEFAULT_EMBEDDING_MODEL


def embedding_dimensions() -> int | None:
    """Optional output dimension override (Voyage supports a few sizes).
    ``None`` → the model's native dimension."""
    raw = (os.environ.get(_EMBEDDING_DIMENSIONS_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def quality_config_summary() -> dict[str, str | bool]:
    """Diagnostic snapshot — handy for an admin endpoint or log line."""
    return {
        "classification_pipeline_version": classification_pipeline_version().value,
        "nevo_matcher_version": nevo_matcher_version().value,
        "embeddings_enabled": embeddings_enabled(),
        "v2_evaluation_enabled": v2_evaluation_enabled(),
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model(),
    }

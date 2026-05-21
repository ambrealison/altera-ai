"""Phase 34J — Pydantic schemas the OpenAI .parse() API uses for
batched classification.

The OpenAI SDK 1.x+ exposes ``client.beta.chat.completions.parse()``
which accepts a Pydantic model as ``response_format`` and returns
``response.choices[0].message.parsed`` already validated. Using this
path eliminates the "missing comma between fields" failure mode that
free-text JSON output occasionally produced — the SDK enforces the
JSON-schema strict mode that backs Structured Outputs and parses the
response into Python objects directly.

We keep the row fields as plain strings (not enum) so the model can
return French wizard labels too; the orchestrator runs
``_normalize_pt_category`` on the way out. Strict-mode enum unions
would otherwise inflate the schema and reject perfectly recoverable
outputs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BatchClassificationRow(BaseModel):
    """One row in the batched classification response.

    ``rationale`` is hard-capped at 64 characters to keep the per-row
    output token budget tight; long rationales were the dominant cause
    of malformed JSON in production (the model would skip commas
    between adjacent fields when running out of breath).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    pt_group: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=64)


class BatchClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    results: list[BatchClassificationRow] = Field(default_factory=list)

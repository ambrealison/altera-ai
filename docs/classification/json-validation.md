# AI JSON validation

The AI classifier is required to return a JSON object that matches a
strict schema. This document specifies the schema, the validation
procedure, and the retry behaviour.

## Schema

The classifier output schemas are methodology-specific because the two
methodologies have structurally different category systems.

### Protein Tracker

```python
class PTClassifierResult(BaseModel):
    methodology: Literal["protein_tracker"]
    pt_group: PTGroup  # enum: plant_based_core | plant_based_non_core
                      #       | composite_products | animal_core
                      #       | out_of_scope | unknown
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=240)
```

### WWF

```python
class WWFClassifierResult(BaseModel):
    methodology: Literal["wwf"]
    wwf_food_group: WWFFoodGroup  # FG1..FG7 | out_of_scope | unknown
    wwf_is_composite: bool
    wwf_fg1_subgroup: WWFFG1Subgroup | None = None
    wwf_fg2_kind: WWFFG2Kind | None = None
    wwf_fg2_dairy_class: WWFFG2DairyClass | None = None
    wwf_fg3_kind: WWFFG3Kind | None = None
    wwf_fg5_grain_kind: WWFFG5GrainKind | None = None
    wwf_fg7_kind: WWFFG7Kind | None = None
    wwf_composite_step1_bucket: WWFCompositeStep1Bucket | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=240)

    @model_validator(mode="after")
    def cross_field_constraints(self) -> "WWFClassifierResult":
        # FG1 must have an FG1 subgroup; FG2 must have an FG2 kind
        # (and dairy_class if dairy_animal); FG3/FG5/FG7 must have
        # their respective kind; composite must have a Step 1 bucket
        # and is not allowed when wwf_food_group is out_of_scope/unknown.
        ...
```

The allowed values per enum are enumerated in
[../methodologies/protein-tracker.md](../methodologies/protein-tracker.md)
and [../methodologies/wwf.md](../methodologies/wwf.md). The Pydantic
enums reference the methodology modules' canonical enum types; the
modules are the single source of truth.

## Validation procedure

For each AI call:

1. Read the LLM response body as text.
2. Locate the first `{` and the matching last `}`. Trim everything
   outside that span.
3. Attempt to parse the trimmed text as JSON.
4. If parse succeeds, validate against `ClassifierResult`.
5. If both succeed, the result is accepted.

A failure at step 3 (parse) or step 4 (schema) is a **parse failure**
and triggers the retry policy.

The "first `{` and last `}`" trim step is deliberately permissive: some
LLMs surround JSON with Markdown fences or stray whitespace. The trim
allows that without inviting partial-object injection, because the
strict Pydantic schema is the actual guarantee.

## Retry policy

- On a first parse failure: retry exactly once. The retry uses the same
  prompt with the temperature ceiling configured in the provider
  (default `0.2`).
- On a second parse failure: route to manual review with
  `reason='ai_parse_failed'`. Do not retry beyond once.

The retry budget for parse failures is **independent** of the retry
budget for provider-level errors (network, rate limit, 5xx). A 429 from
the provider can be retried up to three times with exponential backoff
without consuming the parse-failure retry.

## Logging

Every parse failure (both first and second) is logged with:

- The prompt version.
- The model id.
- The model's raw response (truncated to 2 KB).
- The Pydantic error if validation was the failure mode.

Logs are subject to the same access controls as audit logs; they are
not shipped to any external observability service that is not approved
for retailer data.

## What never happens

- The validator never coerces an invalid category to a "best guess". A
  malformed category routes to manual review.
- The validator never lowers the confidence on an otherwise valid
  result. The model's confidence is recorded as-is.
- The validator never logs the prompt itself in operational logs (the
  prompt contains product names and ingredients which, while not
  commercially sensitive, are still customer data). Prompts are
  reconstructable from the prompt version plus the stored product row.

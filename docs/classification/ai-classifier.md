# AI classifier

The AI classifier is invoked only for products the deterministic engine
could not classify. Its job is **classification**, not calculation or
recommendation. It returns a category, a confidence, and a brief
rationale, as strict JSON.

## Provider abstraction

The classifier sits behind a provider abstraction:

```
apps/api/altera_api/ai/
  provider.py        # ClassifierProvider ABC, ProviderResponse, ProviderError
  openai_provider.py # OpenAIProvider — lazy openai import, temperature=0
  config.py          # get_ai_provider() factory + AISettings (pydantic-settings)
  classifier.py      # classify_pt(), classify_wwf(), DEFAULT_CONFIDENCE_THRESHOLD
  prompt_builder.py  # build_classifier_prompt()
  prompt_input.py    # ClassifierPromptInput — strict allow-list dataclass
  policy.py          # assert_payload_allowed(), ALLOWED_PROMPT_FIELDS
  fakes.py           # StaticFakeProvider, RaisingFakeProvider, FailingFakeProvider
```

`ClassifierProvider.classify(prompt: ClassifierPrompt) -> ProviderResponse`
is the only entry point. Providers must not maintain mutable state
between calls.

Provider selection is controlled by environment variables — no code change required:

| `ALTERA_AI_PROVIDER` | Behaviour |
|---------------------|-----------|
| `disabled` (default) | AI classifier skipped; pass-through → manual review |
| `openai` | `OpenAIProvider` — requires `OPENAI_API_KEY` |
| `mock` | `_MockProvider` — deterministic fake for dev/CI |

`ALTERA_AI_CLASSIFIER_ENABLED=false` (default) also disables AI regardless
of the provider setting.

## Prompt structure

Prompts are assembled by `build_classifier_prompt()` in `prompt_builder.py`.
A prompt has three sections:

1. **System instructions** — describe the methodology, the allowed
   categories, the output schema, and the rule that the model must not
   ask for clarification but must instead use the `unknown` category
   with low confidence.
2. **Methodology card** — a small, pre-baked summary of the active
   methodology's category definitions.
3. **Product card** — only the allowed input fields for this product.
   No commercial data.

The system instructions and methodology card are static for a given
prompt version. The product card is the only per-product input.

## Inputs allowed in the prompt

`product_name`, `retailer_category`, `retailer_subcategory`, `brand`,
`ingredients_text`, `labels`, `language`, `country`.

See [ai-inputs-policy.md](ai-inputs-policy.md) for the enforcement
mechanism.

## Output schema

The model is required to return JSON. The required fields depend on
the methodology, because PT and WWF have very different category
structures. The validation logic is described in
[json-validation.md](json-validation.md).

### Protein Tracker

```json
{
  "methodology": "protein_tracker",
  "pt_group": "plant_based_core | plant_based_non_core | composite_products | animal_core | out_of_scope | unknown",
  "confidence": 0.0,
  "rationale": "<one short sentence>"
}
```

### WWF

```json
{
  "methodology": "wwf",
  "wwf_food_group": "FG1 | FG2 | FG3 | FG4 | FG5 | FG6 | FG7 | out_of_scope | unknown",
  "wwf_is_composite": false,
  "wwf_fg1_subgroup":          null | "red_meat" | "poultry" | "processed_meats_alternatives" | "seafood" | "eggs" | "nuts_seeds" | "legumes" | "alternative_protein_sources" | "meat_egg_seafood_alternatives",
  "wwf_fg2_kind":              null | "dairy_animal" | "dairy_alternative_plant",
  "wwf_fg2_dairy_class":       null | "cheese" | "other",
  "wwf_fg3_kind":              null | "plant_based_fat" | "animal_based_fat",
  "wwf_fg5_grain_kind":        null | "whole_grain" | "refined_grain",
  "wwf_fg7_kind":              null | "plant_based_snack" | "animal_based_snack",
  "wwf_composite_step1_bucket": null | "meat_based" | "seafood_based" | "vegetarian" | "vegan",
  "confidence": 0.0,
  "rationale": "<one short sentence>"
}
```

Sub-field rules:

- `wwf_fg1_subgroup` is required when `wwf_food_group=FG1`,
  otherwise null.
- `wwf_fg2_kind` is required when `wwf_food_group=FG2`; if
  `dairy_animal`, `wwf_fg2_dairy_class` is required.
- `wwf_fg3_kind` is required when `wwf_food_group=FG3`.
- `wwf_fg5_grain_kind` is required when `wwf_food_group=FG5`.
- `wwf_fg7_kind` is required when `wwf_food_group=FG7`.
- `wwf_composite_step1_bucket` is required when `wwf_is_composite=true`.

The AI does **not** produce Step 2 ingredient breakdowns. Step 2 data
is supplied by the user via a companion JSON file; see
[../data/input-formats.md](../data/input-formats.md).

## Confidence calibration

`confidence` is the model's self-reported confidence on a `0.0`–`1.0`
scale. It is interpreted, not trusted absolutely:

- `confidence >= project_threshold` (default `0.8`) → accept.
- `confidence < project_threshold` → route to manual review with
  `reason='low_confidence'`.

We track per-prompt-version calibration in an internal table
(`ai_calibration_samples`) populated by reviewer overrides. If
calibration drifts substantially from intent, the team bumps the prompt
version and re-validates.

## Retry policy

- On JSON parse failure: retry exactly once with an unchanged prompt and
  a slightly higher temperature ceiling (configurable, default `0.2`).
- On a second JSON parse failure: route the product to manual review
  with `reason='ai_parse_failed'`. Do **not** retry beyond once; a
  second failure is treated as a signal, not as a transient error.
- On provider-level error (network, rate limit): exponential backoff up
  to three attempts, separate from the parse-failure retry. Provider
  errors do not consume the parse-failure retry budget.

## Determinism

The provider is called with `temperature=0` by default. This does not
guarantee bit-for-bit deterministic outputs across model versions, so:

- The `ai_model` and `ai_prompt_version` are stored on every
  AI-sourced classification.
- A re-run on the same upload may invoke the AI again for the same
  products if the model or prompt version has changed; if not, the
  prior result is reused.

## What the AI classifier never does

- It never calculates the protein-source ratio or any other figure.
- It never receives commercial data.
- It never produces a hybrid / blended methodology output. One call,
  one methodology, one category.

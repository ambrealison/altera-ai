"""Prompt builder.

A prompt has three sections:

1. **System instructions** — static for a prompt version.
2. **Methodology card** — a pre-baked summary of the active methodology
   (PT or WWF). Static for a prompt version + methodology.
3. **Product card** — only fields from :class:`ClassifierPromptInput`.

The output of :func:`build_classifier_prompt` is a structured
:class:`ClassifierPrompt`. Providers translate it into their own wire
format. The product-card section is validated by the outbound-payload
guard (:mod:`altera_api.ai.policy`) before any HTTP request leaves the
process.
"""
from __future__ import annotations

from dataclasses import dataclass

from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.common import Methodology

#: System-level instructions. Same for both methodologies — the
#: methodology card differs.
_SYSTEM_INSTRUCTIONS = """\
You are a product-classification assistant for a retailer-analytics
platform. Classify the product into the methodology categories given in
the methodology card.

Rules:
- Respond with **JSON only**. No prose, no markdown fences.
- Do not request clarification. If you are not confident, return the
  `unknown` category with a low confidence.
- `confidence` is your self-rated confidence on a 0.0 to 1.0 scale.
- `rationale` is one short sentence explaining your category choice.
"""

_PT_METHODOLOGY_CARD = """\
Methodology: Protein Tracker (GPA & ProVeg Foodservice 2024).

Categories (return one in `pt_group`):
- `plant_based_core`: pulses, tofu/tempeh/seitan, edamame, soy/pea
  protein, hummus, plant-based protein products.
- `plant_based_non_core`: plant milks, plant yoghurts; products that
  contribute some plant protein but are not the primary protein source.
- `composite_products`: multi-ingredient meals where animal and plant
  protein both contribute (lasagna, curry, pizza, sandwich, ready meal).
- `animal_core`: meat, poultry, seafood, eggs, dairy products.
- `out_of_scope`: condiments, water, alcohol, salt, spices.
- `unknown`: insufficient information.

Output schema:
{
  "methodology": "protein_tracker",
  "pt_group": "<one of the above>",
  "confidence": <0.0-1.0>,
  "rationale": "<one short sentence>"
}
"""

_WWF_METHODOLOGY_CARD = """\
Methodology: WWF Planet-Based Diets Retailer Methodology (2024).

Food groups (return one in `wwf_food_group`):
- FG1 Protein sources (with subgroup: red_meat, poultry,
  processed_meats_alternatives, seafood, eggs, nuts_seeds, legumes,
  alternative_protein_sources, meat_egg_seafood_alternatives).
- FG2 Dairy and dairy alternatives (with `wwf_fg2_kind`:
  dairy_animal | dairy_alternative_plant; if dairy_animal, also
  `wwf_fg2_dairy_class`: cheese | other).
- FG3 Fats and oils (with `wwf_fg3_kind`: plant_based_fat |
  animal_based_fat).
- FG4 Fruits and vegetables.
- FG5 Grains and cereals (with `wwf_fg5_grain_kind`: whole_grain |
  refined_grain).
- FG6 Tubers and other starchy foods.
- FG7 Snacks (with `wwf_fg7_kind`: plant_based_snack | animal_based_snack).
- `out_of_scope`: alcohol, water, condiments, salt, spices, baby food,
  vitamin supplements, novel proteins.
- `unknown`: insufficient information.

Composite products (multi-ingredient meals) set `wwf_is_composite=true`
and provide `wwf_composite_step1_bucket`: meat_based | seafood_based |
vegetarian | vegan.

Output schema: see docs/classification/ai-classifier.md.
"""

#: Identifier stamped on every AI-sourced classification. Bumping this
#: invalidates AI calibration samples (see ai-classifier.md).
CLASSIFIER_PROMPT_VERSION = "classifier_v1"


@dataclass(frozen=True)
class ClassifierPrompt:
    """Structured prompt ready to be flattened for a provider."""

    methodology: Methodology
    prompt_version: str
    system_instructions: str
    methodology_card: str
    product_card: dict[str, object]


def build_classifier_prompt(
    prompt_input: ClassifierPromptInput,
    methodology: Methodology,
    *,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
) -> ClassifierPrompt:
    """Assemble a prompt for the given methodology.

    The product-card payload is run through
    :func:`assert_payload_allowed` to make the policy violation
    impossible to miss in tests — the type system already prevents it,
    but layered enforcement is the point.
    """
    payload = prompt_input.to_payload()
    assert_payload_allowed(payload)

    methodology_card = (
        _PT_METHODOLOGY_CARD
        if methodology is Methodology.PROTEIN_TRACKER
        else _WWF_METHODOLOGY_CARD
    )

    return ClassifierPrompt(
        methodology=methodology,
        prompt_version=prompt_version,
        system_instructions=_SYSTEM_INSTRUCTIONS,
        methodology_card=methodology_card,
        product_card=payload,
    )

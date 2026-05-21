"""Phase 34F — Batched AI classification prompts.

The single-product prompt path (``prompt_builder.py`` + ``classifier.py``)
makes one OpenAI call per product, which:

* does not scale beyond ~hundreds of products (1 product = 1 HTTP RTT);
* gives the model too little context to anchor on (no examples in the
  user message, no JSON-mode); and
* produces a high parse-failure rate at gpt-4o-mini's quality tier.

The batched path here packs N products (default 50) into one call and
asks the model to return an array of results. We use JSON mode so the
response is guaranteed-valid JSON, and we include concrete French
examples in the system message so the model recognises the typical
retailer naming patterns.

Privacy rules are unchanged: the per-product payload is still validated
by ``assert_payload_allowed`` before any bytes leave the process. Only
fields in ``ALLOWED_PROMPT_FIELDS`` are sent — commercial fields
(volume, weight, items_purchased, prices, margins, etc.) never appear
in the batch payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.common import Methodology

#: Bumped when the prompt body, instructions, or examples change in a
#: way that should invalidate stored AI provenance / calibration.
BATCH_CLASSIFIER_PROMPT_VERSION = "batch_classifier_v1"

#: Default batch size. Chosen so a typical batch (~50 short French
#: product names) fits comfortably under gpt-4o-mini's 16k context and
#: leaves >2k tokens for the response.
DEFAULT_BATCH_SIZE = 50

_PT_SYSTEM = """\
You are a precise product-classification assistant for a French/European
retailer-analytics platform. Classify each product below into the
Protein Tracker methodology categories.

Allowed `pt_group` values (return EXACTLY one):
- plant_based_core: fruits, vegetables, plain legumes (lentilles, pois \
chiches, haricots, fèves), nuts (amandes, noix), tofu, tempeh, seitan, \
edamame, plain cereals (riz, pâtes, quinoa, avoine), plain bread, plant \
milks not heavily processed, soy/pea protein.
- plant_based_non_core: plant-based meat/cheese/yoghurt substitutes \
(steak végétal, burger végétal, mock chicken, vegan cheese, plant \
yoghurt, plant cream), refined plant oils (olive, sunflower, rapeseed).
- composite_products: prepared meals or products mixing animal AND plant \
ingredients (lasagnes, pizza, quiche, sandwich, salade composée, sushi, \
plats préparés, gratin, brioche, viennoiserie au beurre).
- animal_core: meat (boeuf, porc, veau, agneau, mouton, gibier), poultry \
(poulet, dinde, canard, pintade), processed meat (jambon, saucisse, \
charcuterie, paté), fish/seafood (saumon, thon, cabillaud, crevettes, \
moules, huîtres), eggs (oeufs), dairy (lait, fromage, yaourt, beurre, \
crème, glace).
- out_of_scope: water, alcohol (vin, bière, spiritueux), coffee/tea, \
soft drinks, condiments without protein (vinaigre, moutarde, ketchup, \
sel, épices, herbes), pure sugar/honey/jam, candy/confiserie without \
dairy or nuts, pet food, non-food.
- unknown: ONLY when the product name is too ambiguous to decide \
(e.g. just an SKU code, "Promotion", "Lot 3", a brand-only label).

Confidence guidance:
- 0.95+ when the product name unambiguously names a single food.
- 0.85–0.94 when one obvious word disambiguates the kind.
- 0.70–0.84 for composites where the dominant kind is clear.
- below 0.70 only when genuinely uncertain — DO NOT default to "unknown" \
for ordinary food products.

Concrete examples (French retailer names):
- "Pommes Golden 1.5kg" → plant_based_core (0.98)
- "Carottes Sachet 1kg" → plant_based_core (0.98)
- "Lentilles Vertes du Puy IGP" → plant_based_core (0.97)
- "Pois Chiches Cuits en Conserve" → plant_based_core (0.97)
- "Tofu Nature Bio" → plant_based_core (0.97)
- "Pain Complet Bio" → plant_based_core (0.92)
- "Riz Basmati" → plant_based_core (0.95)
- "Blanc de Poulet Rôti Tranché" → animal_core (0.98)
- "Filets de Saumon Atlantique" → animal_core (0.97)
- "Côte de Bœuf Maturée" → animal_core (0.98)
- "Jambon Blanc Supérieur" → animal_core (0.95)
- "Oeufs Plein Air x12" → animal_core (0.97)
- "Yaourt Nature 0% MG" → animal_core (0.95)
- "Camembert au Lait Cru" → animal_core (0.96)
- "Beurre Doux Demi-Sel" → animal_core (0.95)
- "Steak Végétal Soja & Blé" → plant_based_non_core (0.92)
- "Burger Végétal au Pois" → plant_based_non_core (0.90)
- "Lait d'Amande Bio" → plant_based_non_core (0.90)
- "Yaourt au Soja Vanille" → plant_based_non_core (0.90)
- "Huile d'Olive Vierge Extra" → plant_based_non_core (0.85)
- "Salade Poulet César" → composite_products (0.92)
- "Pizza Royale Jambon Champignons" → composite_products (0.94)
- "Lasagnes Bolognaise" → composite_products (0.93)
- "Quiche Lorraine" → composite_products (0.94)
- "Sandwich Poulet Crudités" → composite_products (0.92)
- "Eau Minérale Naturelle 1.5L" → out_of_scope (0.97)
- "Vin Rouge Bordeaux" → out_of_scope (0.96)
- "Café Moulu Arabica" → out_of_scope (0.94)
- "Sel Fin de Guérande" → out_of_scope (0.96)
- "Vinaigre Balsamique" → out_of_scope (0.94)

Response format — RETURN EXACTLY this JSON object, nothing else:
{
  "results": [
    {"id": "<the id you were given>", "pt_group": "<one allowed value>", \
"confidence": <0.0-1.0>, "rationale": "<one short sentence>"}
  ]
}

Rules:
- Every input id MUST appear exactly once in `results`.
- `pt_group` MUST be one of the allowed values, lower-snake-case.
- DO NOT add fields beyond {id, pt_group, confidence, rationale}.
- DO NOT wrap the JSON in markdown fences or prose.
"""

_WWF_SYSTEM = """\
You are a precise product-classification assistant for a French/European
retailer-analytics platform. Classify each product below into the WWF
Planet-Based Diets food groups.

Allowed `wwf_food_group` values (return EXACTLY one):
- FG1 — Protein sources (meat, poultry, seafood, eggs, nuts/seeds, \
legumes, alternative proteins).
- FG2 — Dairy and dairy alternatives.
- FG3 — Fats and oils.
- FG4 — Fruits and vegetables.
- FG5 — Grains and cereals.
- FG6 — Tubers and other starchy foods.
- FG7 — Snacks.
- out_of_scope: alcohol, water, condiments, salt, spices, baby food.
- unknown: ONLY when the product name is too ambiguous.

Composite multi-ingredient meals (lasagne, pizza, salade composée,
sandwich) should still pick the dominant food group and set
`wwf_is_composite: true`.

Response format — RETURN EXACTLY this JSON object:
{
  "results": [
    {"id": "<the id>", "wwf_food_group": "<one allowed>", \
"wwf_is_composite": <true|false>, "confidence": <0.0-1.0>, "rationale": "..."}
  ]
}

Rules:
- Every input id MUST appear exactly once in `results`.
- DO NOT add fields beyond {id, wwf_food_group, wwf_is_composite, \
confidence, rationale}.
- DO NOT wrap the JSON in markdown fences.
"""


@dataclass(frozen=True)
class BatchClassifierItem:
    """One product entry in a batched prompt.

    ``id`` is the value the model will echo back so we can re-associate
    the result with the source product. We use the product's UUID as a
    string — short enough not to waste tokens, unique within a batch.
    """

    id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class BatchClassifierPrompt:
    methodology: Methodology
    prompt_version: str
    system_message: str
    user_message: str
    item_ids: tuple[str, ...]


def _prune_payload(payload: dict[str, object]) -> dict[str, object]:
    """Drop null / empty fields from a product payload so the batch
    user-message is compact. The allowlist guard still runs on the
    pruned dict, so privacy is unchanged."""
    out: dict[str, object] = {}
    for k, v in payload.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, tuple)) and len(v) == 0:
            continue
        out[k] = v
    return out


def build_batch_classifier_prompt(
    items: list[tuple[str, ClassifierPromptInput]],
    methodology: Methodology,
    *,
    prompt_version: str = BATCH_CLASSIFIER_PROMPT_VERSION,
) -> BatchClassifierPrompt:
    """Assemble a batched prompt for N products.

    ``items`` is a list of ``(id, prompt_input)`` pairs. The id is what
    the model echoes back in its response; the orchestrator uses it to
    re-associate the LLM verdict with the source ``NormalizedProduct``.
    """
    if not items:
        raise ValueError("batch must contain at least one item")

    # Layered privacy: validate every product payload BEFORE assembling
    # the batched user message. This makes the policy violation
    # impossible to miss in tests.
    compacted: list[BatchClassifierItem] = []
    for item_id, prompt_input in items:
        payload = prompt_input.to_payload()
        assert_payload_allowed(payload)
        compacted.append(
            BatchClassifierItem(id=item_id, payload=_prune_payload(payload))
        )

    system_message = (
        _PT_SYSTEM
        if methodology is Methodology.PROTEIN_TRACKER
        else _WWF_SYSTEM
    )

    # JSONL-style body — easier for the model to scan than a single
    # giant JSON array, and uses fewer tokens than pretty-printed JSON.
    lines: list[str] = [
        "Classify each of the following products. Respond with strict JSON only.",
        "Input products:",
    ]
    import json as _json

    for c in compacted:
        line = _json.dumps(
            {"id": c.id, **c.payload}, ensure_ascii=False, separators=(",", ":")
        )
        lines.append(line)
    user_message = "\n".join(lines)

    return BatchClassifierPrompt(
        methodology=methodology,
        prompt_version=prompt_version,
        system_message=system_message,
        user_message=user_message,
        item_ids=tuple(c.id for c in compacted),
    )

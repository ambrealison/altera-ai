"""Phase 34F — Batched AI classification orchestrator.

Replaces the per-product OpenAI call with a single batched call per N
products (default 50). Returns one verdict per input product in the
same order so the upstream orchestrator can apply them by id.

Verdict types and the threshold semantics are intentionally identical
to ``classifier.py`` so existing storage and review-queue logic do not
need to change.

Phase 34H — tolerant parsing + repair retry. The model occasionally:

* wraps its JSON in ``` ```json ``` ``` markdown fences;
* prefixes the JSON with prose like "Here is the result:";
* returns a bare JSON array instead of the documented ``{"results": [...]}``
  envelope;
* leaves out the ``id`` field, returns French category labels instead of
  the internal enums, or invents a new category entirely.

Before Phase 34H any of these failure modes turned the entire batch
into 14/14 parse failures. After Phase 34H:

1. ``extract_json_object`` strips markdown fences, BOM, zero-width
   chars, and leading/trailing prose, then finds the outermost JSON
   value. Bare arrays are wrapped as ``{"results": [...]}``.
2. ``_normalize_pt_category`` accepts common French and English labels
   ("Végétal — cœur", "plant", "animal core") and maps them to the
   stable internal enum values.
3. If the whole batch parse fails, the orchestrator runs ONE repair
   call with a stricter, very short system message. If THAT also
   fails, every product in the batch is marked parse_failed and the
   wizard surfaces the diagnostic.
4. Per-row failures (missing id, invalid category after normalisation)
   only fail that single row — the rest of the batch is still
   classified normally.

Privacy: diagnostic samples are truncated to the first 500 chars and
contain only what the provider sent back (no product data, no
commercial fields).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError as _PydanticValidationError

from altera_api.ai.batch_prompt import (
    BATCH_CLASSIFIER_PROMPT_VERSION,
    DEFAULT_BATCH_SIZE,
    RETRY_BATCH_SIZE,
    build_batch_classifier_prompt,
    build_repair_batch_classifier_prompt,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
    AIVerdict,
)
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ClassifierProvider, ProviderError
from altera_api.ai.result_schema import (
    PTClassifierResult,
    WWFClassifierResult,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct


@dataclass(frozen=True)
class BatchVerdictBundle:
    """Per-batch aggregate diagnostics surfaced to the orchestrator.

    The route layer flattens these into the ClassifyResponse so the
    wizard's Step 4 can show full diagnostic counts and never look
    silent.
    """

    verdicts: list[AIVerdict]
    batch_count: int
    parse_failures: int
    provider_errors: int
    unsupported_category_failures: int
    sample_errors: list[str]
    # Phase 34P — diagnostics for the retry pass. ``retry_batches`` is
    # the number of small-batch retries we issued; ``recovered_rows`` is
    # how many of the originally-failing rows came back as a usable
    # verdict (Accepted or LowConfidence) after retry.
    retry_batches: int = 0
    recovered_rows: int = 0
    # Phase 36J — Phase-36I guard firings broken down by rule name.
    # ``""`` (empty dict) means no guard fired in this batch. The
    # orchestrator aggregates these across advance calls into the
    # ``classify.advance.timing`` log line so operators can see exactly
    # which AI taxonomy errors the guards are catching.
    guard_overrides_by_rule: dict[str, int] = field(default_factory=dict)
    # Phase 36J — unknown_safety_net firings (readable name → unknown
    # rerouted to needs_review).
    unknown_safety_net_total: int = 0


def _chunked(
    seq: list[NormalizedProduct], n: int
) -> Iterable[list[NormalizedProduct]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# ---------------------------------------------------------------------------
# Tolerant JSON extraction
# ---------------------------------------------------------------------------

_ZERO_WIDTH_RE = re.compile(r"[​-‍﻿]")
_FENCE_OPEN_RE = re.compile(r"^```(?:json|JSON)?\s*", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"\s*```\s*$", re.MULTILINE)


def _strip_invisibles(text: str) -> str:
    """Remove BOM and zero-width characters that occasionally appear at
    the start of LLM responses and break JSON parsing."""
    return _ZERO_WIDTH_RE.sub("", text.lstrip("﻿"))


def _strip_markdown_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence if present.

    Some models reliably wrap their JSON in ``` ```json ``` ``` even
    after being told not to. We accept that and unwrap it.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_OPEN_RE.sub("", stripped, count=1)
        stripped = _FENCE_CLOSE_RE.sub("", stripped, count=1)
    return stripped.strip()


def _find_outermost_json(text: str) -> str:
    """Return the slice from the first ``{``/``[`` to the matching
    ``}``/``]`` based on outermost brackets.

    Models sometimes prefix the JSON with prose ("Here is the
    result:..."). Slicing to the first opener and last matching closer
    lets the parser see the JSON without the prose. If both ``{`` and
    ``[`` appear, we pick whichever comes first.
    """
    first_brace = text.find("{")
    first_bracket = text.find("[")
    if first_brace == -1 and first_bracket == -1:
        raise ValueError("no JSON object or array found in response")
    starts_with_array = first_bracket != -1 and (
        first_brace == -1 or first_bracket < first_brace
    )
    if starts_with_array:
        end = text.rfind("]")
        if end == -1 or end < first_bracket:
            raise ValueError("array opens but does not close")
        return text[first_bracket : end + 1]
    end = text.rfind("}")
    if end == -1 or end < first_brace:
        raise ValueError("object opens but does not close")
    return text[first_brace : end + 1]


#: Phase 34J — targeted "missing comma between fields" repair. The
#: model occasionally produces output like ``"plant_based_core""confidence"``
#: (closing quote of one value immediately followed by opening quote
#: of the next key). The first regex inserts the missing comma between
#: adjacent strings; the second inserts it between a number and the
#: next key. Both are *post-hoc* repairs — applied only after a plain
#: ``json.loads`` fails.
_REPAIR_STRING_STRING = re.compile(r'"(\s*)"(?=[A-Za-z_])')
_REPAIR_NUMBER_STRING = re.compile(r'(\d)(\s*)"(?=[A-Za-z_])')
#: Missing comma between two adjacent objects in an array.
_REPAIR_OBJECT_OBJECT = re.compile(r"\}(\s*)\{")


def _repair_missing_commas(text: str) -> str:
    """Insert commas the model omitted between adjacent fields/objects.

    Applied conservatively only when the standard parse fails. The
    three patterns cover the observed production failure mode:

    * ``"value""key"`` → ``"value","key"``
    * ``42"key"`` → ``42,"key"`` (also catches ``0.9"rationale"``)
    * ``}{`` → ``},{`` (between objects in an array)

    The regexes do not touch strings that legitimately contain
    backslash-escaped quotes (``\\"``) because the closing ``"`` of a
    string with an escaped inner quote still matches and the next
    char would not be a key-start (alpha or underscore), so the
    pattern correctly skips it.
    """
    out = _REPAIR_STRING_STRING.sub(r'"\1,"', text)
    out = _REPAIR_NUMBER_STRING.sub(r'\1\2,"', out)
    out = _REPAIR_OBJECT_OBJECT.sub(r"},\1{", out)
    return out


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Recover ``{"results": [...]}`` from a possibly-messy LLM response.

    Tolerates:
    - leading BOM / zero-width characters,
    - markdown ``` ```json ``` ``` fences,
    - leading/trailing prose,
    - the model returning a bare JSON array instead of the envelope,
    - the model returning a single result dict instead of an array,
    - alternative envelope keys (``products``, ``items``),
    - Phase 34J: missing commas between adjacent fields/objects.

    Raises :class:`ValueError` only when nothing JSON-shaped survives
    the recovery. The caller treats that as a parse failure and may
    decide to run a repair retry or partial row extraction.
    """
    cleaned = _strip_markdown_fences(_strip_invisibles(raw_text))
    if not cleaned:
        raise ValueError("response was empty after stripping fences/whitespace")
    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        # Phase 34J — try the comma-repair pass before falling through
        # to the outermost-slice recovery.
        repaired = _repair_missing_commas(cleaned)
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            # Last-ditch: find the outermost JSON slice and try again
            # (repaired form first, original form as a tiebreaker).
            try:
                sliced = _find_outermost_json(repaired)
                parsed = json.loads(sliced)
            except (ValueError, json.JSONDecodeError):
                sliced = _find_outermost_json(cleaned)
                try:
                    parsed = json.loads(sliced)
                except json.JSONDecodeError as exc2:
                    raise ValueError(
                        f"JSON decode failed even after repair: {exc2.msg}"
                    ) from exc2

    # Case A: already a list — wrap as the documented envelope.
    if isinstance(parsed, list):
        return {"results": parsed}

    if not isinstance(parsed, dict):
        raise ValueError(
            f"top-level JSON must be an object or array, got {type(parsed).__name__}"
        )

    # Case B: documented envelope.
    if isinstance(parsed.get("results"), list):
        return parsed

    # Case C: alternative envelope keys.
    for alt_key in ("products", "items", "classifications", "rows"):
        v = parsed.get(alt_key)
        if isinstance(v, list):
            return {"results": v}

    # Case D: single row returned without an envelope.
    if "id" in parsed and ("pt_group" in parsed or "wwf_food_group" in parsed):
        return {"results": [parsed]}

    # Case E: dict whose first list-valued entry IS the results.
    for v in parsed.values():
        if isinstance(v, list):
            return {"results": v}

    raise ValueError("could not find a results array in the response")


# ---------------------------------------------------------------------------
# Phase 34J — per-row salvage when the envelope is unrecoverable
# ---------------------------------------------------------------------------

#: Find each plausible row dict in the raw text. Matches ``{...}``
#: chunks that look row-shaped (contain a quoted ``id`` field). Not a
#: full JSON parser — just enough to slice candidate rows out of a
#: response whose envelope is too broken to fix in one pass.
_ROW_RE = re.compile(
    r"\{[^{}]*?\"id\"\s*:[^{}]*?\}",
    re.DOTALL,
)


def extract_rows_partial(raw_text: str) -> list[dict[str, Any]]:
    """Salvage as many parseable rows as possible from a fully-broken
    batched response.

    Used only as a last resort, after :func:`extract_json_object`
    raises. Walks the raw text, slices out each ``{...}`` that looks
    row-shaped, applies the same missing-comma repair, and parses
    each row independently. Rows that still fail to parse are
    silently dropped — the caller will mark the corresponding
    products as parse_failed.

    Returns an empty list when nothing recoverable was found.
    """
    cleaned = _strip_markdown_fences(_strip_invisibles(raw_text))
    out: list[dict[str, Any]] = []
    for match in _ROW_RE.finditer(cleaned):
        chunk = match.group(0)
        for candidate in (chunk, _repair_missing_commas(chunk)):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "id" in parsed:
                out.append(parsed)
                break
    return out


# ---------------------------------------------------------------------------
# Category normalisation
# ---------------------------------------------------------------------------


def _slug(s: str) -> str:
    """Lower, accent-strip, collapse non-alphanum to single underscores."""
    normalized = unicodedata.normalize("NFKD", s)
    ascii_s = "".join(c for c in normalized if not unicodedata.combining(c))
    ascii_s = ascii_s.lower()
    ascii_s = re.sub(r"[^a-z0-9]+", "_", ascii_s)
    return ascii_s.strip("_")


#: French and English labels the model often returns mapped to the
#: stable internal enum values. Keys are the slugged form so we can
#: match accent-free.
_PT_CATEGORY_ALIASES: dict[str, str] = {
    # Canonical enum values pass through unchanged.
    "plant_based_core": "plant_based_core",
    "plant_based_non_core": "plant_based_non_core",
    "composite_products": "composite_products",
    "animal_core": "animal_core",
    "out_of_scope": "out_of_scope",
    "unknown": "unknown",
    # French wizard labels.
    "vegetal_coeur": "plant_based_core",
    "vegetal_c_ur": "plant_based_core",  # NFKD of "cœur"
    "plant_core": "plant_based_core",
    "plant": "plant_based_core",
    "vegetal_hors_coeur": "plant_based_non_core",
    "vegetal_hors_c_ur": "plant_based_non_core",
    "plant_non_core": "plant_based_non_core",
    "plant_based": "plant_based_core",
    "non_core_plant_based": "plant_based_non_core",
    "composite": "composite_products",
    "compose": "composite_products",
    "compound": "composite_products",
    "mixed": "composite_products",
    "animal": "animal_core",
    "animal_coeur": "animal_core",
    "animal_c_ur": "animal_core",
    "viande": "animal_core",
    "dairy": "animal_core",
    "hors_perimetre": "out_of_scope",
    "out_perimeter": "out_of_scope",
    "n_a": "out_of_scope",
    "na": "out_of_scope",
    "inconnu": "unknown",
}


def _normalize_pt_category(raw: str) -> str | None:
    """Map a raw category string (FR label, alias, enum value) to the
    canonical PT enum value, or return None if no mapping applies.

    The caller treats None as "unsupported category" and routes that
    single row to manual review with reason ``unsupported_category``.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    slug = _slug(raw)
    if not slug:
        return None
    direct = _PT_CATEGORY_ALIASES.get(slug)
    if direct is not None:
        return direct
    # Loose fallback: any slug starting with a known prefix.
    for needle, canonical in _PT_CATEGORY_ALIASES.items():
        if slug == needle or slug.startswith(needle + "_"):
            return canonical
    return None


#: WWF top-level groups. The batched prompt only asks for the top-level
#: food group (FG1..FG7) plus the out_of_scope/unknown system states.
_WWF_CATEGORY_ALIASES: dict[str, str] = {
    "fg1": "FG1",
    "fg2": "FG2",
    "fg3": "FG3",
    "fg4": "FG4",
    "fg5": "FG5",
    "fg6": "FG6",
    "fg7": "FG7",
    "out_of_scope": "out_of_scope",
    "unknown": "unknown",
}


def _normalize_wwf_category(raw: str) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    slug = _slug(raw)
    return _WWF_CATEGORY_ALIASES.get(slug)


# ---------------------------------------------------------------------------
# Per-row parsing
# ---------------------------------------------------------------------------


def _result_for_id(
    results: list[dict[str, object]], item_id: str
) -> dict[str, object] | None:
    for r in results:
        if r.get("id") == item_id:
            return r
    return None


def _coerce_pt_result(
    raw_row: dict[str, object], methodology_value: str
) -> PTClassifierResult:
    pt_group_raw = raw_row.get("pt_group")
    confidence = raw_row.get("confidence")
    rationale = raw_row.get("rationale") or ""
    if not isinstance(pt_group_raw, str):
        raise ValueError("pt_group missing or not a string")
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence missing or not numeric")
    pt_group = _normalize_pt_category(pt_group_raw)
    if pt_group is None:
        raise ValueError(f"unsupported pt_group {pt_group_raw!r}")
    return PTClassifierResult.model_validate(
        {
            "methodology": methodology_value,
            "pt_group": pt_group,
            "confidence": float(confidence),
            "rationale": str(rationale)[:240],
        }
    )


def _coerce_wwf_result(
    raw_row: dict[str, object], methodology_value: str
) -> WWFClassifierResult:
    wwf_food_group_raw = raw_row.get("wwf_food_group")
    confidence = raw_row.get("confidence")
    rationale = raw_row.get("rationale") or ""
    is_composite = bool(raw_row.get("wwf_is_composite", False))
    if not isinstance(wwf_food_group_raw, str):
        raise ValueError("wwf_food_group missing or not a string")
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence missing or not numeric")
    wwf_food_group = _normalize_wwf_category(wwf_food_group_raw)
    if wwf_food_group is None:
        raise ValueError(f"unsupported wwf_food_group {wwf_food_group_raw!r}")
    return WWFClassifierResult.model_validate(
        {
            "methodology": methodology_value,
            "wwf_food_group": wwf_food_group,
            "wwf_is_composite": is_composite,
            "confidence": float(confidence),
            "rationale": str(rationale)[:240],
        }
    )


# ---------------------------------------------------------------------------
# Top-level batch orchestrator
# ---------------------------------------------------------------------------


def _safe_diag(raw_text: str | None, max_chars: int = 500) -> str:
    """Truncate raw response for safe diagnostic logging.

    Strips zero-width characters so the diagnostic actually reflects
    what we saw, and caps length so a verbose model cannot blow up
    the wizard's sample_errors list.
    """
    if not raw_text:
        return ""
    cleaned = _strip_invisibles(raw_text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "…"


def _is_failed_verdict(v: AIVerdict) -> bool:
    """True when a verdict represents a parse or provider failure that
    the Phase 34P retry pass should attempt to recover."""
    return isinstance(v, (AINeedsReviewParseFailed, AIProviderError))


# ---------------------------------------------------------------------------
# Phase 34Q — food-term guard and quality-driven retry triggers
# ---------------------------------------------------------------------------

#: French + English food-name tokens. If a product name contains ANY of
#: these, the AI's `out_of_scope` verdict is overridden to a generic
#: in-scope fallback (low confidence, flagged for review) rather than
#: dropped from the run. This is the backend safety net for the Phase
#: 34Q prompt: even if the model fails to follow the new instructions,
#: an obvious food product cannot end up `out_of_scope`.
_FOOD_TERMS: frozenset[str] = frozenset(
    {
        # French
        "pomme", "poire", "banane", "fraise", "framboise", "orange",
        "raisin", "abricot", "peche", "kiwi", "mangue", "ananas",
        "carotte", "tomate", "courgette", "aubergine", "poivron",
        "legume", "salade", "epinard", "concombre", "oignon", "ail",
        "champignon", "haricot", "pois", "lentille", "feve",
        "poulet", "dinde", "canard", "boeuf", "bœuf", "porc",
        "agneau", "veau", "viande", "jambon", "lardon", "saucisse",
        "saucisson", "charcuterie", "pate", "paté",
        "poisson", "saumon", "thon", "cabillaud", "merlu", "crevette",
        "moule", "huitre", "huître", "sardine", "maquereau", "morue",
        "oeuf", "œuf", "ouef",
        "lait", "fromage", "yaourt", "beurre", "creme", "crème",
        "camembert", "brie", "comte", "comté", "gruyere", "gruyère",
        "emmental", "mozzarella", "feta", "chevre", "chèvre",
        "tofu", "tempeh", "seitan", "edamame",
        "pain", "baguette", "brioche", "viennoiserie", "croissant",
        "biscotte", "pâte", "pates", "pâtes", "spaghetti",
        "riz", "quinoa", "couscous", "boulgour", "ble", "blé",
        "avoine", "muesli", "cereale", "céréale",
        "pomme de terre", "patate", "frite", "puree",
        "chocolat", "chips", "biscuit", "gateau", "gâteau", "tarte",
        "glace", "creme glacee", "confiture", "miel", "sirop",
        "huile", "margarine", "vinaigrette",
        "pizza", "lasagne", "quiche", "sandwich", "burger", "wrap",
        "soupe", "veloute", "velouté", "potage", "gratin",
        "nuggets", "steak",
        # English / loanwords (strong nouns only — descriptors like
        # "nature" / "bio" / "frais" are intentionally excluded because
        # they appear in non-food products like "Eau Minérale Naturelle"
        # which the model correctly classifies as out_of_scope).
        "apple", "carrot", "tomato", "milk", "cheese", "yogurt", "yoghurt",
        "butter", "egg", "chicken", "beef", "pork", "fish", "salmon",
        "tuna", "shrimp", "bread", "pasta", "rice", "potato", "fruit",
        "vegetable", "meat", "snack", "chocolate", "oil",
    }
)


def _looks_like_food(product_name: str) -> bool:
    """True if the product name contains any token that strongly
    suggests it is a food product.

    Used to override a false ``out_of_scope`` verdict from the model on
    obviously-food products (e.g. "Chips Nature", "Huile d'Olive").
    """
    if not product_name:
        return False
    lowered = product_name.lower()
    # Cheap substring scan — the food vocabulary is small and the
    # product names are short, so this is well under a microsecond.
    return any(term in lowered for term in _FOOD_TERMS)


#: Phase 36H — placeholder / unusable product names. Only THESE are
#: legitimate ``unknown`` reasons. Anything readable but ambiguous
#: must be routed to ``needs_review`` with the model's best guess
#: instead of left as a final ``unknown``.
_UNUSABLE_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "produit",
        "produit divers",
        "divers",
        "article",
        "articles",
        "n/a",
        "na",
        "nan",
        "none",
        "null",
        "xxx",
        "test",
        "tbd",
        "?",
        "??",
        "???",
        "...",
        "-",
        "--",
        "---",
        "_",
        "tbc",
    }
)


def _is_unusable_name(product_name: str | None) -> bool:
    """True if the name is empty, too short, or a generic placeholder
    that doesn't carry enough signal for ANY classification (food OR
    non-food).

    Phase 36H — the new product rule says ``unknown`` is reserved for
    these unusable names only. A readable name like "Blinis Moelleux"
    or "Dentifrice Menthe" must NEVER end up as final ``unknown``;
    even when the model can't choose a category, we route to
    ``needs_review`` so a human can pick.
    """
    if product_name is None:
        return True
    stripped = product_name.strip()
    if not stripped:
        return True
    # Names shorter than 3 characters can't carry a meaningful
    # classification signal — "p", "a3", "x" all qualify as unusable.
    if len(stripped) < 3:
        return True
    lowered = stripped.lower()
    if lowered in _UNUSABLE_NAME_TOKENS:
        return True
    # Mostly-digit / mostly-symbol names ("123456", "###", "..."):
    # remove letters and check the remainder; if no alphabetic
    # characters at all, it's unusable.
    alphabetic = [c for c in lowered if c.isalpha()]
    if not alphabetic:
        return True
    return False


#: Phase 34Q — retry triggers for low-quality batches. If any rate
#: exceeds these thresholds the orchestrator schedules a second pass
#: at retry_batch_size with a stronger prompt to push the model toward
#: concrete food categories.
OUT_OF_SCOPE_RATE_TRIGGER = 0.10
UNKNOWN_RATE_TRIGGER = 0.10
REVIEW_RATE_TRIGGER = 0.40


def batch_classify(
    products: list[NormalizedProduct],
    provider: ClassifierProvider,
    methodology: Methodology,
    *,
    now: datetime,
    # Phase 34Q — auto-accept at confidence >= 0.70 (was 0.80). Lower
    # threshold = more "accepted" products, fewer "needs review". Rows
    # below 0.70 still get a category; they're routed to review with
    # their proposed pt_group so the user sees the AI's best guess.
    threshold: Decimal = Decimal("0.7"),
    batch_size: int = DEFAULT_BATCH_SIZE,
    prompt_version: str = BATCH_CLASSIFIER_PROMPT_VERSION,
    retry_batch_size: int = RETRY_BATCH_SIZE,
    enable_retry: bool = True,
) -> BatchVerdictBundle:
    """Classify ``products`` in batches and return ordered verdicts.

    Output verdicts are in the same order as ``products``. One
    AIProviderError or batched-response parse failure does NOT poison
    the whole input; only the affected batch (or per-product result
    within a batch) is marked failed.

    Phase 34H: if the initial response from the model cannot be parsed
    even after tolerant extraction, the orchestrator runs ONE repair
    call with a stricter prompt. If the repair also fails, all products
    in the batch are marked parse_failed and the diagnostic includes
    the first 500 chars of the bad response.
    """
    out: list[AIVerdict] = []
    batch_count = 0
    parse_failures = 0
    provider_errors = 0
    unsupported_category_failures = 0
    sample_errors: list[str] = []
    # Phase 36J — per-batch guard firing counters (Phase 36I).
    guard_overrides_by_rule: dict[str, int] = {}
    unknown_safety_net_total = 0

    def _maybe_sample(msg: str) -> None:
        if len(sample_errors) < 10:
            sample_errors.append(msg)

    for chunk in _chunked(products, batch_size):
        batch_count += 1
        items = [
            (str(p.id), ClassifierPromptInput.from_product(p)) for p in chunk
        ]
        prompt = build_batch_classifier_prompt(
            items, methodology, prompt_version=prompt_version
        )

        try:
            response = provider.batch_classify(prompt)
        except ProviderError as exc:
            provider_errors += len(chunk)
            _maybe_sample(f"provider_error: {exc}")
            for p in chunk:
                out.append(
                    AIProviderError(
                        product_id=p.id,
                        methodology=methodology,
                        message=str(exc),
                    )
                )
            continue
        except NotImplementedError:
            provider_errors += len(chunk)
            _maybe_sample("provider does not support batch classification")
            for p in chunk:
                out.append(
                    AIProviderError(
                        product_id=p.id,
                        methodology=methodology,
                        message="batch unsupported",
                    )
                )
            continue

        # Phase 34H — tolerant envelope extraction + optional repair retry.
        results: list[dict[str, object]] | None = None
        parse_error: str | None = None
        try:
            envelope = extract_json_object(response.raw_text)
            raw_results = envelope.get("results")
            assert isinstance(raw_results, list)
            results = [r for r in raw_results if isinstance(r, dict)]
        except ValueError as exc:
            parse_error = str(exc)

        # If the envelope was parseable but every input id is missing
        # from the results, also treat the response as repair-worthy.
        ids_present = (
            results is not None
            and any(
                _result_for_id(results, str(p.id)) is not None for p in chunk
            )
        )

        if results is None or not ids_present:
            # ONE repair retry per batch.
            diag = _safe_diag(response.raw_text)
            _maybe_sample(
                f"parse_failed: {parse_error or 'no input ids matched'} | "
                f"raw[:500]={diag!r}"
            )
            repair_prompt = build_repair_batch_classifier_prompt(
                items,
                methodology,
                bad_response=diag,
                prompt_version=prompt_version,
            )
            try:
                repair_response = provider.batch_classify(repair_prompt)
            except ProviderError as exc:
                provider_errors += len(chunk)
                _maybe_sample(f"repair_provider_error: {exc}")
                for p in chunk:
                    out.append(
                        AIProviderError(
                            product_id=p.id,
                            methodology=methodology,
                            message=str(exc),
                        )
                    )
                continue
            try:
                envelope = extract_json_object(repair_response.raw_text)
                raw_results = envelope.get("results")
                assert isinstance(raw_results, list)
                results = [r for r in raw_results if isinstance(r, dict)]
                response = repair_response
            except ValueError as exc:
                # Phase 34J — before declaring the whole batch
                # unparseable, try per-row salvage. If the model's
                # output is broken at the envelope level but each row
                # is independently recoverable (the dominant failure
                # mode in production: 33 missing-comma rows in one
                # response), we can still classify the rows that parse.
                salvaged_initial = extract_rows_partial(response.raw_text)
                salvaged_repair = extract_rows_partial(repair_response.raw_text)
                # Prefer the repair response when it surfaced more
                # rows; otherwise stick with the original.
                if len(salvaged_repair) >= len(salvaged_initial):
                    salvaged = salvaged_repair
                    response = repair_response
                else:
                    salvaged = salvaged_initial
                if salvaged:
                    results = salvaged
                    _maybe_sample(
                        f"partial_recovery: {len(salvaged)} row(s) salvaged"
                        f" from broken envelope ({exc})"
                    )
                else:
                    # Repair failed and no rows could be salvaged — every
                    # product in the batch becomes parse_failed.
                    parse_failures += len(chunk)
                    _maybe_sample(
                        f"repair_failed: {exc} | raw[:500]="
                        f"{_safe_diag(repair_response.raw_text)!r}"
                    )
                    for p in chunk:
                        out.append(
                            AINeedsReviewParseFailed(
                                product_id=p.id,
                                methodology=methodology,
                                first_error=parse_error or "",
                                second_error=str(exc),
                            )
                        )
                    continue

        assert results is not None  # for type checker

        # Per-row dispatch. Missing id / unsupported category only
        # fail that single row.
        for p in chunk:
            row = _result_for_id(results, str(p.id))
            if row is None:
                parse_failures += 1
                _maybe_sample(
                    f"parse_failed: id {p.id} missing from batched response"
                )
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error="id missing from batched response",
                        second_error="id missing from batched response",
                    )
                )
                continue
            try:
                if methodology is Methodology.PROTEIN_TRACKER:
                    result = _coerce_pt_result(row, methodology.value)
                else:
                    result = _coerce_wwf_result(row, methodology.value)
            except (ValueError, _PydanticValidationError) as exc:
                raw_cat = row.get("pt_group") or row.get("wwf_food_group")
                msg = str(exc)
                if isinstance(raw_cat, str):
                    unsupported_category_failures += 1
                    _maybe_sample(
                        f"unsupported_category: {raw_cat!r} ({msg})"
                    )
                else:
                    parse_failures += 1
                    _maybe_sample(f"parse_failed: {msg}")
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=msg,
                        second_error=msg,
                    )
                )
                continue

            classification = result.to_classification(
                product_id=p.id,
                ai_prompt_version=prompt_version,
                ai_model=response.model,
                now=now,
            )

            inferred_cat = (
                str(row.get("pt_group", "")) or str(row.get("wwf_food_group", ""))
            )

            # Phase 36K — readable-name fallback for PT ``unknown``.
            # Runs BEFORE the food-term guard so a model ``unknown``
            # on a readable name (e.g. "Sorbet Framboise",
            # "Miel de Fleurs", "Cassoulet Provençale") gets a
            # best-guess category instead of being routed straight to
            # parse-failed by the food-term guard. The fallback only
            # fires on PT + readable + unknown; everything else
            # continues to the legacy guards below.
            if (
                methodology is Methodology.PROTEIN_TRACKER
                and inferred_cat == "unknown"
                and not _is_unusable_name(p.product_name)
            ):
                from altera_api.ai.pt_guards import (
                    classify_readable_fallback,
                )

                fallback = classify_readable_fallback(p.product_name)
                if fallback is not None:
                    fallback_group, fallback_rule = fallback
                    _maybe_sample(
                        f"readable_fallback: rule={fallback_rule} "
                        f"name={p.product_name!r}"
                    )
                    guard_overrides_by_rule[fallback_rule] = (
                        guard_overrides_by_rule.get(fallback_rule, 0) + 1
                    )
                    fallback_classification = classification.model_copy(
                        update={
                            "pt_group": fallback_group,
                            "confidence": Decimal("0.5"),
                        }
                    )
                    out.append(
                        AINeedsReviewLowConfidence(
                            classification=fallback_classification,
                            raw_text=response.raw_text,
                            threshold=threshold,
                        )
                    )
                    continue

            # Phase 34Q — food-term guard. If the model said
            # ``out_of_scope`` (or ``unknown``) but the product name
            # contains obvious food tokens ("poulet", "huile", "chips",
            # "yaourt", ...), flag the verdict as retry-worthy. The
            # existing retry pass will re-run it at a smaller batch
            # with the same now-stricter prompt. If the retry still
            # comes back out_of_scope the verdict stands.
            #
            # Phase 36K — the readable-fallback branch above already
            # caught most ``unknown`` cases on PT-tracked food-looking
            # names; what remains here is mostly ``out_of_scope`` from
            # the model on food-looking names.
            if (
                inferred_cat in {"out_of_scope", "unknown"}
                and _looks_like_food(p.product_name)
            ):
                _maybe_sample(
                    f"food_guard_override: {p.product_name!r} returned "
                    f"as {inferred_cat!r} but looks like food"
                )
                parse_failures += 1
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=(
                            f"model returned {inferred_cat} on food-looking "
                            f"product {p.product_name!r}"
                        ),
                        second_error=(
                            f"model returned {inferred_cat} on food-looking "
                            f"product"
                        ),
                    )
                )
                continue

            # Phase 36I — deterministic taxonomy guards for the
            # Protein Tracker. The 150-product audit surfaced four
            # systematic error classes; ``apply_pt_guards`` detects
            # them and substitutes a corrected category. The guard
            # ALSO clamps confidence to 0.69 so the row routes to
            # ``needs_review`` — we never silently auto-accept a
            # guard-corrected verdict.
            #
            # Phase 36J — guards run BEFORE the unknown safety net so
            # a model ``unknown`` on a fruit-drink / smoothie name can
            # be promoted to plant_based_non_core via
            # ``fruit_drink_non_core`` instead of falling through to
            # needs_review_parse_failed.
            if methodology is Methodology.PROTEIN_TRACKER:
                from altera_api.ai.pt_guards import apply_pt_guards

                override = apply_pt_guards(
                    p.product_name, classification
                )
                if override is not None:
                    _maybe_sample(
                        f"pt_guard: rule={override.rule} "
                        f"name={p.product_name!r}"
                    )
                    guard_overrides_by_rule[override.rule] = (
                        guard_overrides_by_rule.get(override.rule, 0) + 1
                    )
                    classification = override.new_classification
                    # The guard already chose a category — skip the
                    # downstream unknown safety net entirely.
                    inferred_cat = classification.pt_group.value

            # Phase 36H — unknown safety net (legacy path).
            # Reaches this branch only if:
            #   * Phase 36K readable-fallback returned None, OR
            #   * methodology is WWF (not PT),
            # AND the model still says ``unknown`` AND the name is
            # readable. We route to ``AINeedsReviewParseFailed`` so
            # the wizard surfaces the row in review.
            if (
                inferred_cat == "unknown"
                and not _is_unusable_name(p.product_name)
            ):
                _maybe_sample(
                    f"unknown_safety_net: {p.product_name!r} returned "
                    f"as 'unknown' but name is readable; routed to review"
                )
                parse_failures += 1
                unknown_safety_net_total += 1
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=(
                            f"model returned unknown on readable product "
                            f"{p.product_name!r}; needs human review"
                        ),
                        second_error=(
                            "model returned unknown on readable product"
                        ),
                    )
                )
                continue

            if classification.confidence < threshold:
                out.append(
                    AINeedsReviewLowConfidence(
                        classification=classification,
                        raw_text=response.raw_text,
                        threshold=threshold,
                    )
                )
            else:
                out.append(
                    AIAccepted(
                        classification=classification,
                        raw_text=response.raw_text,
                        parse_failures=0,
                    )
                )

    # ---------------------------------------------------------------------
    # Phase 34Q — quality-driven retry. If the model returned a high
    # rate of out_of_scope / unknown / very-low-confidence verdicts on
    # this run, the AI was likely conservative. Flag those rows as
    # retry-worthy so the smaller-batch retry below has a chance to
    # re-classify them. The food-term guard above already catches the
    # most obvious cases per row; this is the batch-level fallback.
    # ---------------------------------------------------------------------
    if enable_retry and len(out) > 0:
        from altera_api.domain.protein_tracker import ProteinTrackerGroup
        from altera_api.domain.wwf import WWFFoodGroup

        def _ai_cat(v: AIVerdict) -> str | None:
            if isinstance(v, (AIAccepted, AINeedsReviewLowConfidence)):
                cls = v.classification
                if hasattr(cls, "pt_group"):
                    return cls.pt_group.value  # type: ignore[union-attr]
                if hasattr(cls, "wwf_food_group"):
                    return cls.wwf_food_group.value  # type: ignore[union-attr]
            return None

        oos_count = sum(1 for v in out if _ai_cat(v) == "out_of_scope")
        unk_count = sum(1 for v in out if _ai_cat(v) == "unknown")
        review_count = sum(
            1 for v in out if isinstance(v, AINeedsReviewLowConfidence)
        )
        total = len(out)
        oos_rate = oos_count / total
        unk_rate = unk_count / total
        review_rate = review_count / total
        if (
            oos_rate > OUT_OF_SCOPE_RATE_TRIGGER
            or unk_rate > UNKNOWN_RATE_TRIGGER
            or review_rate > REVIEW_RATE_TRIGGER
        ):
            _maybe_sample(
                f"quality_retry_trigger: oos={oos_rate:.0%} "
                f"unk={unk_rate:.0%} review={review_rate:.0%}"
            )
            # Convert out_of_scope / unknown verdicts to retry-worthy
            # failures. Low-confidence verdicts already carry a usable
            # category so we leave them alone — they should NOT be
            # treated as uncategorized; the wizard counts them as
            # "categorized + needs review".
            for i, v in enumerate(out):
                cat = _ai_cat(v)
                if cat in {"out_of_scope", "unknown"}:
                    p = products[i]
                    out[i] = AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=(
                            f"quality_retry: batch rate triggered "
                            f"(oos={oos_rate:.0%}, unk={unk_rate:.0%})"
                        ),
                        second_error="quality_retry",
                    )
                    parse_failures += 1
                    _ = ProteinTrackerGroup, WWFFoodGroup  # silence unused

    # ---------------------------------------------------------------------
    # Phase 34P — retry pass for failed rows at a smaller batch size.
    #
    # The dominant failure modes at batch_size=25 are (a) the model
    # truncating the JSON envelope mid-row when its completion budget
    # runs out, and (b) a transient provider error (rate limit, timeout)
    # that hit a single batch. Both recover well when the failing rows
    # are retried at a much smaller batch (default 5): the completion
    # is short enough to fit and the retry is on fresh state.
    #
    # We only retry verdicts of type AINeedsReviewParseFailed or
    # AIProviderError. AINeedsReviewLowConfidence and AIAccepted are
    # already usable. The retry replaces the verdict in-place; if the
    # retry still fails the original verdict stays so the wizard can
    # still route the row to manual review.
    # ---------------------------------------------------------------------
    retry_batches_used = 0
    recovered_rows = 0
    if enable_retry and retry_batch_size > 0 and retry_batch_size < batch_size:
        failed_indices = [i for i, v in enumerate(out) if _is_failed_verdict(v)]
        # Cap retry effort so a totally-broken provider can't blow up
        # latency. We retry at most one full batch_size worth of rows.
        max_retry_rows = max(batch_size, retry_batch_size * 10)
        retry_indices = failed_indices[:max_retry_rows]
        if retry_indices:
            failed_products = [products[i] for i in retry_indices]
            for chunk_start in range(0, len(failed_products), retry_batch_size):
                retry_products = failed_products[
                    chunk_start : chunk_start + retry_batch_size
                ]
                retry_indices_slice = retry_indices[
                    chunk_start : chunk_start + retry_batch_size
                ]
                retry_bundle = batch_classify(
                    retry_products,
                    provider,
                    methodology,
                    now=now,
                    threshold=threshold,
                    batch_size=retry_batch_size,
                    prompt_version=prompt_version,
                    enable_retry=False,  # no recursive retry
                )
                retry_batches_used += retry_bundle.batch_count
                for local_idx, new_verdict in enumerate(retry_bundle.verdicts):
                    target = retry_indices_slice[local_idx]
                    prior = out[target]
                    if _is_failed_verdict(prior) and not _is_failed_verdict(
                        new_verdict
                    ):
                        out[target] = new_verdict
                        recovered_rows += 1
                        # Decrement the matching counter so the wizard
                        # final number reflects the rows that came back.
                        if isinstance(prior, AINeedsReviewParseFailed):
                            parse_failures = max(0, parse_failures - 1)
                        elif isinstance(prior, AIProviderError):
                            provider_errors = max(0, provider_errors - 1)
            if recovered_rows > 0 and len(sample_errors) < 10:
                sample_errors.append(
                    f"retry_recovered: {recovered_rows} row(s) salvaged "
                    f"via {retry_batches_used} retry batch(es)"
                )

    return BatchVerdictBundle(
        verdicts=out,
        batch_count=batch_count,
        parse_failures=parse_failures,
        provider_errors=provider_errors,
        unsupported_category_failures=unsupported_category_failures,
        sample_errors=sample_errors,
        retry_batches=retry_batches_used,
        recovered_rows=recovered_rows,
        guard_overrides_by_rule=dict(guard_overrides_by_rule),
        unknown_safety_net_total=unknown_safety_net_total,
    )

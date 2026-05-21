"""Phase 33I-AI — deterministic candidate generation for nutrition matching.

Before invoking the LLM we compute a short shortlist of likely NEVO and
CIQUAL reference rows for a product, using only token-level string
heuristics on the *reference table* side. This:

  * keeps the AI grounded — the matcher cannot return a code we did
    not show it, so it cannot invent codes or values;
  * caps token usage — only ~10 candidates per source are sent;
  * means the AI is opt-in: when no candidate can be generated at all,
    we never call the LLM (saves cost on hopeless lookups).

The candidate scoring is intentionally simple — it is not a search
engine, only a "did any meaningful word in the product name appear in
this reference's name?" filter ordered by overlap. Future phases can
swap in trigrams or vector similarity without changing the matcher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.nevo import NevoEntry


@dataclass(frozen=True)
class NutritionCandidate:
    """One reference row offered to the LLM as a possible match."""

    source: str          # "nevo" | "ciqual"
    reference_code: str  # nevo_code or source_food_code
    name: str            # English food name (NL fallback for NEVO if EN empty)
    food_group: str | None


_TOKEN_SPLIT = re.compile(r"[^a-z0-9À-ɏ]+")

#: Words that carry no matching signal — dropped before scoring.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "with",
        "without",
        "and",
        "the",
        "for",
        "of",
        "in",
        "on",
        "to",
        "a",
        "an",
        "fresh",
        "frozen",
        "raw",
        "cooked",
        "organic",
        "natural",
        "free",
        "range",
        "100g",
        "g",
        "kg",
        "ml",
        "l",
        "pack",
        "box",
        "bag",
        "pouch",
        # French stopwords
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "et",
        "en",
        "au",
        "aux",
        "un",
        "une",
        "cru",
        "crue",
        "cuit",
        "cuite",
        "nature",
        "frais",
        "fraiche",
        "bio",
        "pur",
        "pure",
    }
)

#: French food token → English equivalents for cross-language NEVO scoring.
_FR_TO_EN: dict[str, list[str]] = {
    "poulet": ["chicken"],
    "blanc": ["white", "breast"],
    "filet": ["fillet", "breast"],
    "tofu": ["tofu"],
    "soja": ["soy", "soya"],
    "soya": ["soya", "soy"],
    "boeuf": ["beef"],
    "veau": ["veal"],
    "porc": ["pork"],
    "agneau": ["lamb"],
    "dinde": ["turkey"],
    "saumon": ["salmon"],
    "thon": ["tuna"],
    "cabillaud": ["cod"],
    "crevettes": ["shrimp", "prawn"],
    "moules": ["mussels"],
    "oeuf": ["egg"],
    "oeufs": ["eggs"],
    "lait": ["milk"],
    "fromage": ["cheese"],
    "yaourt": ["yoghurt", "yogurt"],
    "beurre": ["butter"],
    "creme": ["cream"],
    "riz": ["rice"],
    "pates": ["pasta"],
    "pain": ["bread"],
    "blé": ["wheat"],
    "ble": ["wheat"],
    "mais": ["corn", "maize"],
    "avoine": ["oat", "oats"],
    "orge": ["barley"],
    "légumes": ["vegetables"],
    "legumes": ["vegetables"],
    "fruits": ["fruit"],
    "pomme": ["apple"],
    "tomate": ["tomato"],
    "carotte": ["carrot"],
    "oignon": ["onion"],
    "ail": ["garlic"],
    "lentilles": ["lentils"],
    "pois": ["peas"],
    "haricots": ["beans"],
    "cereales": ["cereals", "grain"],
    "salade": ["salad"],
    "viande": ["meat"],
    "poisson": ["fish"],
    "charcuterie": ["processed meat", "cured meat"],
    "jambon": ["ham"],
    "saucisse": ["sausage"],
    "amandes": ["almonds"],
    "noix": ["nuts", "walnut"],
    "noisettes": ["hazelnuts"],
    "cacahuetes": ["peanuts"],
    "huile": ["oil"],
    "olive": ["olive"],
    "sucre": ["sugar"],
    "chocolat": ["chocolate"],
    "laitue": ["lettuce"],
    "epinards": ["spinach"],
    "brocolis": ["broccoli"],
}


def _tokenize(s: str) -> set[str]:
    if not s:
        return set()
    # Normalize accented characters for matching (é→e, etc.)
    import unicodedata
    normalized = unicodedata.normalize("NFKD", s.lower())
    ascii_s = "".join(c for c in normalized if not unicodedata.combining(c))
    parts = _TOKEN_SPLIT.split(ascii_s)
    return {p for p in parts if len(p) >= 3 and p not in _STOPWORDS}


def _expand_fr(tokens: set[str]) -> set[str]:
    """Expand French food tokens to English equivalents for cross-language scoring."""
    expanded = set(tokens)
    for tok in tokens:
        for en in _FR_TO_EN.get(tok, []):
            expanded.update(_tokenize(en))
    return expanded


def _score(query_tokens: set[str], candidate_name: str) -> int:
    cand_tokens = _tokenize(candidate_name)
    if not cand_tokens or not query_tokens:
        return 0
    return len(query_tokens & cand_tokens)


def _name_for(e: NevoEntry) -> str:
    # Prefer EN when present, fall back to NL — both are indexed in the
    # provider lookup table.
    return e.food_name_en or e.food_name_nl


def candidates_for_product(
    *,
    product_name: str,
    retailer_category: str | None,
    nevo_entries: list[NevoEntry],
    ciqual_entries: list[CiqualEntry],
    max_per_source: int = 10,
) -> list[NutritionCandidate]:
    """Return up to ``max_per_source`` candidates per source, ordered by
    relevance. Returns an empty list when nothing in the product name
    overlaps any reference name — the caller should NOT call the LLM in
    that case (no shortlist to ground the answer).
    """
    query_tokens = _tokenize(product_name)
    if retailer_category:
        # Category provides extra anchoring (e.g. "Poultry" → "chicken").
        query_tokens |= _tokenize(retailer_category)
    if not query_tokens:
        return []

    # Expand French tokens to English equivalents so French products
    # can score against English/Dutch NEVO entries.
    query_tokens_expanded = _expand_fr(query_tokens)

    nevo_scored: list[tuple[int, NevoEntry]] = []
    for e in nevo_entries:
        if e.protein_g_per_100g is None:
            continue
        # Score with expanded tokens (FR→EN) against NEVO English/Dutch names.
        s = _score(query_tokens_expanded, _name_for(e))
        if s > 0:
            nevo_scored.append((s, e))
    nevo_scored.sort(key=lambda t: (-t[0], _name_for(t[1])))

    ciqual_scored: list[tuple[int, CiqualEntry]] = []
    for e in ciqual_entries:
        if e.protein_g_per_100g is None:
            continue
        # CIQUAL food_name_en stores French names (from alim_nom_fr column).
        # Score with original French tokens for direct matching, plus expanded
        # tokens for mixed-language CIQUAL entries.
        s = max(
            _score(query_tokens, e.food_name_en),
            _score(query_tokens_expanded, e.food_name_en),
        )
        if s > 0:
            ciqual_scored.append((s, e))
    ciqual_scored.sort(key=lambda t: (-t[0], t[1].food_name_en))

    out: list[NutritionCandidate] = []
    for _, e in nevo_scored[:max_per_source]:
        out.append(
            NutritionCandidate(
                source="nevo",
                reference_code=e.nevo_code,
                name=_name_for(e),
                food_group=e.food_group or None,
            )
        )
    for _, e in ciqual_scored[:max_per_source]:
        out.append(
            NutritionCandidate(
                source="ciqual",
                reference_code=e.source_food_code,
                name=e.food_name_en,
                food_group=e.food_group or None,
            )
        )
    return out

"""Phase 36E — product-head extraction and head-aware NEVO matching.

The pre-Phase-36E NEVO fuzzy matcher used a flat token-overlap score
with a 1-token minimum. That meant "Ratatouille à l'Huile d'Olive"
matched "Oil olive" (one token overlap: ``olive``) just as easily as
"Ratatouille prepared wo meat" (one token overlap: ``ratatouille``).
The shorter, secondary-ingredient candidate often won on tie-break.

A 75-product audit on the production data showed precision of
**50.8%** on matched lines — half the auto-accepted matches were
false positives (oil-only on ratatouille, alpro-cuisine on ratatouille,
herring-fillet on ratatouille, pasta-only on lasagne, potatoes-mashed-
with-milk on lait, apple-pie-without-butter on beurre, etc.).

This module introduces the **product head**: the principal noun of a
retailer product name. Once the head is known, we can:

  * **Require** the head to appear in the candidate (or in a small,
    explicit alias set) — drops the "Ratatouille → Oil olive" class
    of false positive entirely.
  * **Reject** "dish containing X" candidates when the product IS X
    (Lait should never match "Potatoes mashed with milk").
  * **Reject** simple-ingredient candidates when the product is a
    composite/prepared meal (Ratatouille should never match "Garlic
    raw").

The head list is curated and intentionally small — it covers the
classes of products that broke in the audit. Future phases extend it
without touching the scoring logic.

This module does NOT touch:
  * the nutrition / NEVO TABLE perf (Phase 36F-lite).
  * the AI classification prompt (Phase 34Q).
  * the PT taxonomy (Phase 34T/U/V).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Head kinds
# ---------------------------------------------------------------------------

#: The semantic family of a head. Used by the matcher to enforce
#: composite-vs-simple-food guards.
PREPARED_MEAL = "prepared_meal"
PLANT_SUBSTITUTE = "plant_substitute"
PLANT_DRINK = "plant_drink"
DAIRY_MILK = "dairy_milk"
DAIRY_YOGURT = "dairy_yogurt"
DAIRY_FRESH_CHEESE = "dairy_fresh_cheese"
DAIRY_CHEESE = "dairy_cheese"
DAIRY_FAT = "dairy_fat"
DAIRY_CREAM = "dairy_cream"
MEAT_POULTRY = "meat_poultry"
MEAT_RED = "meat_red"
MEAT_PROCESSED = "meat_processed"
FISH = "fish"
PLANT_PROTEIN = "plant_protein"
EGG = "egg"
LEGUME = "legume"
CEREAL = "cereal"
PASTA = "pasta"
BREAD = "bread"
SAUCE_CONDIMENT = "sauce_condiment"

#: Kinds where the product IS a simple ingredient. Candidates that
#: are *dishes containing* that ingredient should be rejected: a
#: "Lait" product must NEVER match "Potatoes mashed with milk", and a
#: "Beurre" product must NEVER match "Apple pie without butter".
SIMPLE_FOOD_KINDS: frozenset[str] = frozenset(
    {
        DAIRY_MILK,
        DAIRY_YOGURT,
        DAIRY_FRESH_CHEESE,
        DAIRY_FAT,
        DAIRY_CREAM,
        EGG,
    }
)

#: Kinds where the product is itself a multi-ingredient composite. A
#: "Ratatouille" product must NEVER match a single-ingredient candidate
#: like "Oil olive" or "Garlic raw" — the head is the dish, not its
#: components.
COMPOSITE_KINDS: frozenset[str] = frozenset(
    {
        PREPARED_MEAL,
        PLANT_SUBSTITUTE,
    }
)


# ---------------------------------------------------------------------------
# Head dictionary
# ---------------------------------------------------------------------------

#: Tuple of ``(head_pattern, kind, aliases)``.
#:
#: ``head_pattern`` is a normalised (lowercase, accent-folded) substring
#: searched in the cleaned product name. Multi-word heads (e.g.
#: "fromage blanc", "steak vegetal") MUST be listed before their
#: single-word components — the extractor walks the list in order
#: and returns the first match. Otherwise "Fromage Blanc" would be
#: detected as ``fromage`` (kind=cheese) when the analyst expects
#: ``fromage blanc`` (kind=dairy_fresh_cheese).
#:
#: ``aliases`` are the tokens we accept in candidate names — both
#: FR/EN/NL — when validating that the candidate is "about the same
#: food" as the head. The matcher requires AT LEAST ONE alias token
#: to appear in the candidate's tokenised name.
_HEADS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Multi-word heads first — they dominate single-word fallbacks.
    ("steak vegetal", PLANT_SUBSTITUTE, ("steak", "vegetal", "plant", "vegetarian", "vegan")),
    ("steak végétal", PLANT_SUBSTITUTE, ("steak", "vegetal", "plant", "vegetarian", "vegan")),
    ("burger vegetal", PLANT_SUBSTITUTE, ("burger", "vegetal", "plant", "vegetarian", "vegan")),
    ("burger végétal", PLANT_SUBSTITUTE, ("burger", "vegetal", "plant", "vegetarian", "vegan")),
    ("boisson avoine", PLANT_DRINK, ("oat", "avoine", "drink", "beverage", "plant", "boisson")),
    ("boisson amande", PLANT_DRINK, ("almond", "amande", "drink", "beverage", "plant", "boisson")),
    ("boisson soja", PLANT_DRINK, ("soy", "soja", "drink", "beverage", "plant", "boisson")),
    ("boisson riz", PLANT_DRINK, ("rice", "riz", "drink", "beverage", "plant", "boisson")),
    ("fromage blanc", DAIRY_FRESH_CHEESE, ("quark", "fromage", "fresh cheese", "fromage blanc")),
    ("fromage frais", DAIRY_FRESH_CHEESE, ("quark", "fromage", "fresh cheese", "fromage frais")),
    ("salade cesar", PREPARED_MEAL, ("caesar", "salad", "salade")),
    ("salade césar", PREPARED_MEAL, ("caesar", "salad", "salade")),
    ("cordon bleu", PREPARED_MEAL, ("cordon", "bleu")),
    # Composite / prepared meals.
    ("ratatouille", PREPARED_MEAL, ("ratatouille",)),
    ("lasagnes", PREPARED_MEAL, ("lasagne", "lasagnes", "lasagna")),
    ("lasagne", PREPARED_MEAL, ("lasagne", "lasagnes", "lasagna")),
    ("paella", PREPARED_MEAL, ("paella",)),
    ("risotto", PREPARED_MEAL, ("risotto",)),
    ("tajine", PREPARED_MEAL, ("tajine", "tagine")),
    ("tagine", PREPARED_MEAL, ("tajine", "tagine")),
    ("couscous", PREPARED_MEAL, ("couscous", "semolina")),
    ("hachis parmentier", PREPARED_MEAL, ("hachis", "parmentier", "shepherd")),
    ("blanquette", PREPARED_MEAL, ("blanquette",)),
    ("bourguignon", PREPARED_MEAL, ("bourguignon", "stew", "beef")),
    ("chili", PREPARED_MEAL, ("chili",)),
    ("curry", PREPARED_MEAL, ("curry",)),
    ("quiche", PREPARED_MEAL, ("quiche",)),
    ("pizza", PREPARED_MEAL, ("pizza",)),
    ("burrito", PREPARED_MEAL, ("burrito",)),
    ("taco", PREPARED_MEAL, ("taco",)),
    ("wrap", PREPARED_MEAL, ("wrap",)),
    ("sushi", PREPARED_MEAL, ("sushi",)),
    ("sandwich", PREPARED_MEAL, ("sandwich",)),
    ("gratin", PREPARED_MEAL, ("gratin",)),
    ("soupe", PREPARED_MEAL, ("soup", "soupe")),
    ("velouté", PREPARED_MEAL, ("soup", "veloute", "velouté")),
    ("veloute", PREPARED_MEAL, ("soup", "veloute", "velouté")),
    # Dairy single-food.
    ("lait", DAIRY_MILK, ("milk", "melk", "lait")),
    ("yaourt", DAIRY_YOGURT, ("yogurt", "yoghurt", "yaourt")),
    ("yogourt", DAIRY_YOGURT, ("yogurt", "yoghurt", "yogourt")),
    ("beurre", DAIRY_FAT, ("butter", "boter", "beurre")),
    ("creme", DAIRY_CREAM, ("cream", "room", "creme", "crème")),
    ("crème", DAIRY_CREAM, ("cream", "room", "creme", "crème")),
    # Plant protein single-food.
    ("tofu", PLANT_PROTEIN, ("tofu", "soy", "soja")),
    ("tempeh", PLANT_PROTEIN, ("tempeh", "soy")),
    ("seitan", PLANT_PROTEIN, ("seitan", "wheat", "gluten")),
    # Animal protein single-food.
    ("poulet", MEAT_POULTRY, ("chicken", "kip", "poulet")),
    ("dinde", MEAT_POULTRY, ("turkey", "kalkoen", "dinde")),
    ("canard", MEAT_POULTRY, ("duck", "eend", "canard")),
    ("boeuf", MEAT_RED, ("beef", "rundvlees", "rund", "boeuf")),
    ("bœuf", MEAT_RED, ("beef", "rundvlees", "rund", "boeuf")),
    ("veau", MEAT_RED, ("veal", "kalfsvlees", "veau")),
    ("porc", MEAT_RED, ("pork", "varkensvlees", "varken", "porc")),
    ("agneau", MEAT_RED, ("lamb", "lamsvlees", "agneau")),
    ("jambon", MEAT_PROCESSED, ("ham", "jambon")),
    ("saucisse", MEAT_PROCESSED, ("sausage", "worst", "saucisse")),
    # Fish single-food.
    ("saumon", FISH, ("salmon", "zalm", "saumon")),
    ("thon", FISH, ("tuna", "tonijn", "thon")),
    ("cabillaud", FISH, ("cod", "kabeljauw", "cabillaud")),
    ("merlu", FISH, ("hake", "merlu")),
    ("truite", FISH, ("trout", "forel", "truite")),
    ("sardine", FISH, ("sardine",)),
    ("maquereau", FISH, ("mackerel", "makreel", "maquereau")),
    ("hareng", FISH, ("herring", "haring", "hareng")),
    # Egg.
    ("oeuf", EGG, ("egg", "ei", "oeuf", "œuf")),
    ("œuf", EGG, ("egg", "ei", "oeuf", "œuf")),
    # Cheese (single-word) — runs AFTER fromage blanc / frais.
    ("fromage", DAIRY_CHEESE, ("cheese", "kaas", "fromage")),
    # Cereal / pasta / bread.
    ("pates", PASTA, ("pasta", "pâtes", "pates")),
    ("pâtes", PASTA, ("pasta", "pâtes", "pates")),
    ("riz", CEREAL, ("rice", "rijst", "riz")),
    ("pain", BREAD, ("bread", "brood", "pain")),
    # Common sauces / spreads (simple foods).
    ("mayonnaise", SAUCE_CONDIMENT, ("mayonnaise",)),
    ("ketchup", SAUCE_CONDIMENT, ("ketchup",)),
    ("moutarde", SAUCE_CONDIMENT, ("mustard", "moutarde")),
    ("pesto", SAUCE_CONDIMENT, ("pesto",)),
    ("houmous", SAUCE_CONDIMENT, ("hummus", "houmous")),
    ("hummus", SAUCE_CONDIMENT, ("hummus", "houmous")),
)


def _strip_accents(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nf if not unicodedata.combining(c))


@dataclass(frozen=True)
class ProductHead:
    """The principal noun of a retailer product name plus its semantic
    family (``kind``) and the alias tokens we accept in candidates."""

    raw: str            # the matched substring as it appears in the head dict
    kind: str           # one of the constants above
    aliases: tuple[str, ...]


def extract_product_head(product_name: str) -> ProductHead | None:
    """Find the first curated head that appears in ``product_name``.

    Multi-word heads are tried first (see ``_HEADS`` ordering) so
    "Fromage Blanc" → ``fromage blanc`` (DAIRY_FRESH_CHEESE), not
    ``fromage`` (DAIRY_CHEESE).

    Returns ``None`` when no curated head is found. The matcher then
    falls back to its prior token-overlap path with a tighter
    threshold (see ``nutrition_candidates.py`` and
    ``providers/nevo.py``).
    """
    if not product_name:
        return None
    lower = product_name.lower()
    folded = _strip_accents(lower)
    for raw, kind, aliases in _HEADS:
        # Boundary-aware match — \b around the head pattern avoids
        # "lait" matching "laitue" (lettuce).
        pattern = re.escape(_strip_accents(raw))
        if re.search(rf"\b{pattern}\b", folded):
            return ProductHead(raw=raw, kind=kind, aliases=aliases)
    return None


# ---------------------------------------------------------------------------
# Candidate-side guards
# ---------------------------------------------------------------------------

#: Phrases that signal the candidate is a "dish containing X" rather
#: than X itself. When the product head is a simple food (lait, beurre,
#: oeuf, fromage frais...), candidates matching any of these patterns
#: are rejected: "Lait" must not match "Potatoes mashed with milk".
#:
#: Matched case-insensitively on the candidate's English/Dutch name.
_COMPOSITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwith\b", re.IGNORECASE),
    re.compile(r"\bwithout\b", re.IGNORECASE),
    re.compile(r"\bcontaining\b", re.IGNORECASE),
    re.compile(r"\bin\b", re.IGNORECASE),
    re.compile(r"\bmet\b", re.IGNORECASE),  # NL "with"
    re.compile(r"\bzonder\b", re.IGNORECASE),  # NL "without"
    re.compile(r"\bsupplemented\b", re.IGNORECASE),
    re.compile(r"\benriched\b", re.IGNORECASE),
    re.compile(r"\bmixed\b", re.IGNORECASE),
)


def looks_like_composite(candidate_name: str) -> bool:
    """True if the candidate name reads like "X with/in/zonder Y" — a
    dish derived from the simple food, not the simple food itself.

    Used to reject "Lait" → "Potatoes mashed with milk", "Beurre" →
    "Apple pie without butter", etc.
    """
    if not candidate_name:
        return False
    return any(p.search(candidate_name) for p in _COMPOSITE_PATTERNS)


def candidate_contains_head_alias(
    head: ProductHead, candidate_tokens: set[str]
) -> bool:
    """True if any of the head's alias tokens appears in the
    candidate's tokenised name.

    The matcher requires this for every accepted candidate when a
    product head was detected. Without it, "Ratatouille à l'Huile
    d'Olive" can still tie with "Oil olive" on the ``olive`` token
    alone."""
    if not candidate_tokens:
        return False
    alias_tokens: set[str] = set()
    for alias in head.aliases:
        # Same tokenisation as nutrition_candidates._tokenize, inlined
        # to avoid the circular import.
        folded = _strip_accents(alias.lower())
        for part in re.split(r"[^a-z0-9]+", folded):
            if len(part) >= 3:
                alias_tokens.add(part)
    return bool(alias_tokens & candidate_tokens)

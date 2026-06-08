"""Phase Quality-V2-AI — persistent multilingual (FR/DE) NEVO reference layer.

Manual product-level review does not scale to ~30k retailer rows whose names are
mostly French/German while NEVO names are English/abbreviated. This module
*materializes* French + German NEVO names and search aliases ONCE into a
generated artifact (``nevo_reference_multilingual.csv``), which retrieval then
loads/caches like any other reference — translations are never generated per
search.

Strictly additive and offline: it never modifies the original NEVO name, code,
or nutrition values; it is used by V2 retrieval only behind an explicit CLI flag;
V1 stays default and embeddings stay off. No retailer commercial data is ever
sent to an LLM/embedding provider — only the public NEVO English food name is
translated.

This file is the shared library for the V2-AI CLIs:
- ``generate_nevo_multilingual_reference``  (Part C)
- ``validate_nevo_multilingual_reference``  (Part D)
- ``compare_nevo_multilingual_retrieval``   (Part G)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from altera_api.embeddings.text_builder import _assert_no_commercial

# --- Part B — schema -------------------------------------------------------
ALIAS_SEP = ";"
NUTRITION_COLUMN = "protein_g_per_100g"
ML_COLUMNS = [
    "nevo_code", "nevo_food_name", NUTRITION_COLUMN,
    "nevo_food_name_fr", "nevo_food_name_de",
    "search_aliases_fr", "search_aliases_de", "search_aliases_en",
    "translation_source", "translation_review_status", "translation_notes",
]
TRANSLATION_SOURCES = frozenset(
    {"generated_llm", "manual", "imported", "unavailable"})
REVIEW_STATUSES = frozenset(
    {"unreviewed", "auto_validated", "needs_review", "reviewed"})

#: Max embedding-text length (chars) so the multilingual text stays bounded.
MAX_REFERENCE_TEXT_CHARS = 700

# --- state / form preservation vocabulary (Part C #8, validated in Part D) --
#: High-risk state/form categories. Each: the English trigger tokens and the
#: markers that MUST survive into FR and DE (so a state is never collapsed).
STATE_CHECKS: dict[str, dict[str, frozenset[str]]] = {
    "drink": {
        "en": frozenset({"drink", "beverage"}),
        "fr": frozenset({"boisson", "drink"}),
        "de": frozenset({"drink", "getrank", "getraenk", "getränk"}),
    },
    "dried": {
        "en": frozenset({"dried"}),
        "fr": frozenset({"sec", "secs", "seche", "seches", "sèche", "sèches",
                         "seché", "séché", "déshydraté", "deshydrate"}),
        "de": frozenset({"getrocknet", "getrocknete", "trocken"}),
    },
    "cooked": {
        "en": frozenset({"cooked", "boiled"}),
        "fr": frozenset({"cuit", "cuite", "cuits", "cuites", "bouilli",
                         "bouillie"}),
        "de": frozenset({"gekocht", "gekochte", "gegart"}),
    },
    "instant": {
        "en": frozenset({"instant"}),
        "fr": frozenset({"instantane", "instantané", "soluble"}),
        "de": frozenset({"instant", "loslich", "löslich", "loeslich"}),
    },
    "powder": {
        "en": frozenset({"powder", "powdered"}),
        "fr": frozenset({"poudre"}),
        "de": frozenset({"pulver"}),
    },
}
#: Specific oil types: an oil product must keep its *type*, not just "oil".
OIL_TYPES: dict[str, dict[str, tuple[str, ...]]] = {
    "rapeseed": {"fr": ("colza",), "de": ("raps",)},
    "canola": {"fr": ("colza",), "de": ("raps",)},
    "olive": {"fr": ("olive",), "de": ("oliven",)},
    "sunflower": {"fr": ("tournesol",), "de": ("sonnenblumen",)},
    "vegetable": {"fr": ("vegetale", "végétale"), "de": ("pflanzen",)},
    "blend": {"fr": ("melange", "mélange"), "de": ("misch",)},
    "coconut": {"fr": ("coco",), "de": ("kokos",)},
    "palm": {"fr": ("palme",), "de": ("palm",)},
    "soy": {"fr": ("soja",), "de": ("soja",)},
    "soya": {"fr": ("soja",), "de": ("soja",)},
    "corn": {"fr": ("mais", "maïs"), "de": ("mais",)},
    "peanut": {"fr": ("arachide",), "de": ("erdnuss",)},
    "sesame": {"fr": ("sesame", "sésame"), "de": ("sesam",)},
}
#: Specific vinegar types: a vinegar product must keep its *type*.
VINEGAR_TYPES: dict[str, dict[str, tuple[str, ...]]] = {
    "balsamic": {"fr": ("balsamique",), "de": ("balsamico",)},
    "cider": {"fr": ("cidre",), "de": ("apfel",)},
    "apple": {"fr": ("cidre", "pomme"), "de": ("apfel",)},
    "wine": {"fr": ("vin",), "de": ("wein",)},
    "white": {"fr": ("blanc",), "de": ("weiss", "weiß")},
}

_STOPWORDS = frozenset({"of", "the", "a", "an", "and", "with", "in", "av",
                        "per", "to"})
_TOKEN_RE = re.compile(r"[a-zA-Zàâäéèêëïîôöùûüçœ]+", re.UNICODE)


def _tokens(name: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(name.lower()) if t not in _STOPWORDS]


# --- curated FR/DE translation table ---------------------------------------
# Keyed by a frozenset of significant English tokens; the most specific
# (largest) subset match wins so "rice drink" beats "rice". Each entry yields a
# concise FR name, DE name, and language aliases. Covers the critical safety
# examples plus common retailer foods; everything else falls to compositional
# translation or needs_review.
@dataclass(frozen=True)
class _Curated:
    fr: str
    de: str
    afr: tuple[str, ...] = ()
    ade: tuple[str, ...] = ()
    aen: tuple[str, ...] = ()


_CURATED: tuple[tuple[frozenset[str], _Curated], ...] = (
    # --- safety examples ---------------------------------------------------
    (frozenset({"lentils", "dried"}),
     _Curated("lentilles sèches", "getrocknete Linsen",
              ("lentilles sèches", "lentilles"),
              ("getrocknete Linsen", "Linsen"), ("dried lentils", "lentils"))),
    (frozenset({"lentils", "cooked"}),
     _Curated("lentilles cuites", "gekochte Linsen",
              ("lentilles cuites", "lentilles"),
              ("gekochte Linsen", "Linsen"), ("cooked lentils",))),
    (frozenset({"lentils"}),
     _Curated("lentilles", "Linsen", ("lentilles",), ("Linsen",),
              ("lentils",))),
    (frozenset({"rice", "drink"}),
     _Curated("boisson de riz", "Reisdrink",
              ("boisson de riz", "boisson au riz"), ("Reisdrink", "Reismilch"),
              ("rice drink", "rice beverage"))),
    (frozenset({"rice", "cooked"}),
     _Curated("riz cuit", "gekochter Reis", ("riz cuit",),
              ("gekochter Reis",), ("cooked rice",))),
    (frozenset({"rice"}),
     _Curated("riz", "Reis", ("riz",), ("Reis",), ("rice",))),
    (frozenset({"coffee", "instant", "powder"}),
     _Curated("café instantané en poudre", "löslicher Kaffee Pulver",
              ("café instantané en poudre", "café soluble", "café instantané"),
              ("löslicher Kaffee", "Instantkaffee", "Kaffeepulver"),
              ("instant coffee powder", "instant coffee"))),
    (frozenset({"coffee", "instant"}),
     _Curated("café instantané", "löslicher Kaffee",
              ("café instantané", "café soluble"),
              ("löslicher Kaffee", "Instantkaffee"), ("instant coffee",))),
    (frozenset({"coffee", "beans"}),
     _Curated("café en grains", "Kaffeebohnen",
              ("café en grains",), ("Kaffeebohnen",), ("coffee beans",))),
    (frozenset({"coffee"}),
     _Curated("café", "Kaffee", ("café",), ("Kaffee",), ("coffee",))),
    (frozenset({"oil", "rapeseed"}),
     _Curated("huile de colza", "Rapsöl", ("huile de colza",),
              ("Rapsöl", "Rapsoel"), ("rapeseed oil", "canola oil"))),
    (frozenset({"oil", "olive"}),
     _Curated("huile d'olive", "Olivenöl", ("huile d'olive", "huile olive"),
              ("Olivenöl", "Olivenoel"), ("olive oil",))),
    (frozenset({"oil", "sunflower"}),
     _Curated("huile de tournesol", "Sonnenblumenöl",
              ("huile de tournesol",), ("Sonnenblumenöl",),
              ("sunflower oil",))),
    (frozenset({"oil", "vegetable"}),
     _Curated("huile végétale", "Pflanzenöl", ("huile végétale",),
              ("Pflanzenöl", "Pflanzenoel"), ("vegetable oil",))),
    (frozenset({"oil", "peanut"}),
     _Curated("huile d'arachide", "Erdnussöl",
              ("huile d'arachide", "huile arachide", "huile de cacahuète"),
              ("Erdnussöl", "Erdnussoel", "Erdnuss Öl"),
              ("peanut oil",))),
    (frozenset({"oil", "soya"}),
     _Curated("huile de soja", "Sojaöl",
              ("huile de soja", "huile soja"),
              ("Sojaöl", "Sojaoel", "Soja Öl"),
              ("soya oil", "soy oil"))),
    (frozenset({"oil", "soy"}),
     _Curated("huile de soja", "Sojaöl",
              ("huile de soja", "huile soja"),
              ("Sojaöl", "Sojaoel", "Soja Öl"),
              ("soy oil", "soya oil"))),
    (frozenset({"oil", "blend"}),
     _Curated("mélange d'huiles", "Ölmischung",
              ("mélange d'huiles", "huile mélange"),
              ("Ölmischung", "Oelmischung", "Öl Mischung"),
              ("oil blend",))),
    (frozenset({"oil", "corn"}),
     _Curated("huile de maïs", "Maisöl",
              ("huile de maïs", "huile mais"),
              ("Maisöl", "Maisoel", "Mais Öl"),
              ("corn oil",))),
    (frozenset({"oil", "coconut"}),
     _Curated("huile de coco", "Kokosöl",
              ("huile de coco", "huile de noix de coco"),
              ("Kokosöl", "Kokosoel", "Kokos Öl"),
              ("coconut oil",))),
    (frozenset({"oil", "sesame"}),
     _Curated("huile de sésame", "Sesamöl",
              ("huile de sésame", "huile sesame"),
              ("Sesamöl", "Sesamoel", "Sesam Öl"),
              ("sesame oil",))),
    (frozenset({"oil", "palm"}),
     _Curated("huile de palme", "Palmöl",
              ("huile de palme", "huile palme"),
              ("Palmöl", "Palmoel", "Palm Öl"),
              ("palm oil",))),
    (frozenset({"oil", "canola"}),
     _Curated("huile de colza", "Rapsöl", ("huile de colza",),
              ("Rapsöl", "Rapsoel"), ("canola oil", "rapeseed oil"))),
    (frozenset({"vinegar", "balsamic"}),
     _Curated("vinaigre balsamique", "Balsamico-Essig",
              ("vinaigre balsamique",), ("Balsamico-Essig", "Balsamico"),
              ("balsamic vinegar",))),
    (frozenset({"vinegar", "cider"}),
     _Curated("vinaigre de cidre", "Apfelessig", ("vinaigre de cidre",),
              ("Apfelessig",), ("cider vinegar", "apple cider vinegar"))),
    (frozenset({"vinegar", "wine"}),
     _Curated("vinaigre de vin", "Weinessig", ("vinaigre de vin",),
              ("Weinessig",), ("wine vinegar",))),
    (frozenset({"vinegar"}),
     _Curated("vinaigre", "Essig", ("vinaigre",), ("Essig",), ("vinegar",))),
    # --- common retailer foods --------------------------------------------
    (frozenset({"chickpeas"}),
     _Curated("pois chiches", "Kichererbsen", ("pois chiches",),
              ("Kichererbsen",), ("chickpeas",))),
    (frozenset({"beans", "black"}),
     _Curated("haricots noirs", "schwarze Bohnen", ("haricots noirs",),
              ("schwarze Bohnen",), ("black beans",))),
    (frozenset({"beans"}),
     _Curated("haricots", "Bohnen", ("haricots",), ("Bohnen",), ("beans",))),
    (frozenset({"peas"}),
     _Curated("pois", "Erbsen", ("pois",), ("Erbsen",), ("peas",))),
    (frozenset({"milk"}),
     _Curated("lait", "Milch", ("lait",), ("Milch",), ("milk",))),
    (frozenset({"yoghurt"}),
     _Curated("yaourt", "Joghurt", ("yaourt",), ("Joghurt",),
              ("yoghurt", "yogurt"))),
    (frozenset({"cheese"}),
     _Curated("fromage", "Käse", ("fromage",), ("Käse", "Kaese"),
              ("cheese",))),
    (frozenset({"butter"}),
     _Curated("beurre", "Butter", ("beurre",), ("Butter",), ("butter",))),
    (frozenset({"tofu"}),
     _Curated("tofu", "Tofu", ("tofu",), ("Tofu",), ("tofu",))),
    (frozenset({"potato"}),
     _Curated("pomme de terre", "Kartoffel", ("pommes de terre",),
              ("Kartoffeln",), ("potato", "potatoes"))),
    (frozenset({"tomato"}),
     _Curated("tomate", "Tomate", ("tomates",), ("Tomaten",),
              ("tomato", "tomatoes"))),
    (frozenset({"apple"}),
     _Curated("pomme", "Apfel", ("pommes",), ("Äpfel", "Apfel"),
              ("apple", "apples"))),
    (frozenset({"pasta"}),
     _Curated("pâtes", "Nudeln", ("pâtes", "pates"), ("Nudeln", "Pasta"),
              ("pasta",))),
    (frozenset({"bread"}),
     _Curated("pain", "Brot", ("pain",), ("Brot",), ("bread",))),
    (frozenset({"chicken"}),
     _Curated("poulet", "Hähnchen", ("poulet",), ("Hähnchen", "Huhn"),
              ("chicken",))),
    (frozenset({"salmon"}),
     _Curated("saumon", "Lachs", ("saumon",), ("Lachs",), ("salmon",))),
    (frozenset({"tuna"}),
     _Curated("thon", "Thunfisch", ("thon",), ("Thunfisch",), ("tuna",))),
    (frozenset({"egg"}),
     _Curated("œuf", "Ei", ("œuf", "oeuf", "œufs"), ("Ei", "Eier"),
              ("egg", "eggs"))),
)

#: Base nouns for compositional fallback (token -> (fr, de)).
_BASE_NOUNS: dict[str, tuple[str, str]] = {
    "lentils": ("lentilles", "Linsen"), "rice": ("riz", "Reis"),
    "oil": ("huile", "Öl"), "vinegar": ("vinaigre", "Essig"),
    "coffee": ("café", "Kaffee"), "milk": ("lait", "Milch"),
    "cheese": ("fromage", "Käse"), "beans": ("haricots", "Bohnen"),
    "peas": ("pois", "Erbsen"), "tomato": ("tomate", "Tomate"),
    "potato": ("pomme de terre", "Kartoffel"), "bread": ("pain", "Brot"),
    "chicken": ("poulet", "Hähnchen"), "fish": ("poisson", "Fisch"),
    "yoghurt": ("yaourt", "Joghurt"), "butter": ("beurre", "Butter"),
}
#: Modifier tokens for compositional fallback (token -> (fr, de)).
_MODIFIERS: dict[str, tuple[str, str]] = {
    "dried": ("sèches", "getrocknet"), "cooked": ("cuit", "gekocht"),
    "boiled": ("bouilli", "gekocht"), "raw": ("cru", "roh"),
    "frozen": ("surgelé", "tiefgekühlt"), "powder": ("en poudre", "Pulver"),
    "instant": ("instantané", "instant"), "drink": ("boisson", "Drink"),
    "sweetened": ("sucré", "gesüßt"), "unsweetened": ("non sucré", "ungesüßt"),
    "smoked": ("fumé", "geräuchert"), "fresh": ("frais", "frisch"),
}


#: Canonical FR/DE marker to APPEND when a curated/base translation would
#: otherwise drop a high-risk state present in the English name.
_STATE_AUGMENT: dict[str, tuple[str, str]] = {
    "drink": ("boisson", "Drink"), "dried": ("sèches", "getrocknet"),
    "cooked": ("cuit", "gekocht"), "instant": ("instantané", "instant"),
    "powder": ("en poudre", "Pulver"),
}


def _augment_states(tr: Translation, tokens: list[str]) -> Translation:
    """Never collapse a state: append any FR/DE marker missing for a state
    present in the English name. Downgrades to needs_review if it had to."""
    token_set = set(tokens)
    fr_blob = (tr.food_name_fr + " " + " ".join(tr.aliases_fr)).lower()
    de_blob = (tr.food_name_de + " " + " ".join(tr.aliases_de)).lower()
    augmented = False
    for label, spec in STATE_CHECKS.items():
        if not (token_set & spec["en"]):
            continue
        fr_word, de_word = _STATE_AUGMENT[label]
        if tr.food_name_fr and not any(m in fr_blob for m in spec["fr"]):
            tr.food_name_fr = f"{tr.food_name_fr} {fr_word}".strip()
            augmented = True
        if tr.food_name_de and not any(m in de_blob for m in spec["de"]):
            tr.food_name_de = f"{tr.food_name_de} {de_word}".strip()
            augmented = True
    if augmented and tr.review_status == "auto_validated":
        tr.review_status = "needs_review"
        tr.notes = (tr.notes + "+state_augmented").strip("+")
    return tr


def _best_curated(tokens: list[str]) -> _Curated | None:
    token_set = set(tokens)
    best: _Curated | None = None
    best_size = 0
    for signature, entry in _CURATED:
        if signature <= token_set and len(signature) > best_size:
            best, best_size = entry, len(signature)
    return best


# --- translators -----------------------------------------------------------
@dataclass
class Translation:
    food_name_fr: str = ""
    food_name_de: str = ""
    aliases_fr: list[str] = field(default_factory=list)
    aliases_de: list[str] = field(default_factory=list)
    aliases_en: list[str] = field(default_factory=list)
    source: str = "unavailable"
    review_status: str = "needs_review"
    notes: str = ""


class DeterministicTranslator:
    """Rule-based FR/DE translator (no LLM, no network) — used by ``--no-llm``.

    Curated table for known foods (auto_validated), a state-preserving
    compositional fallback for known base nouns (needs_review), else marks the
    row unavailable/needs_review. It never collapses a food state/form.
    """

    name = "deterministic"
    source_label = "imported"

    def translate(self, food_name_en: str) -> Translation:
        tokens = _tokens(food_name_en)
        if not tokens:
            return Translation(notes="empty_source_name")
        curated = _best_curated(tokens)
        if curated is not None:
            return _augment_states(Translation(
                food_name_fr=curated.fr, food_name_de=curated.de,
                aliases_fr=list(curated.afr), aliases_de=list(curated.ade),
                aliases_en=list(curated.aen) or [food_name_en.lower()],
                source=self.source_label, review_status="auto_validated",
                notes="curated"), tokens)
        # Compositional fallback: known base noun + preserved modifiers.
        base = next((t for t in tokens if t in _BASE_NOUNS), None)
        if base is None:
            return Translation(notes="no_known_base_term")
        mods = [t for t in tokens if t in _MODIFIERS]
        fr_base, de_base = _BASE_NOUNS[base]
        fr = " ".join([fr_base, *[_MODIFIERS[m][0] for m in mods]]).strip()
        de = " ".join([*[_MODIFIERS[m][1] for m in mods], de_base]).strip()
        return _augment_states(Translation(
            food_name_fr=fr, food_name_de=de, aliases_fr=[fr_base],
            aliases_de=[de_base], aliases_en=[food_name_en.lower()],
            source=self.source_label, review_status="needs_review",
            notes="composed_from_base+modifiers"), tokens)


# --- alias helpers ---------------------------------------------------------
def parse_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = str(value).split(ALIAS_SEP)
    return _dedupe([i.strip() for i in items if i and i.strip()])


def join_aliases(items: list[str]) -> str:
    return ALIAS_SEP.join(_dedupe([i.strip() for i in items if i.strip()]))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# --- Part C — row generation -----------------------------------------------
def _ref_name(ref: dict[str, Any]) -> str:
    return str(ref.get("food_name_en") or ref.get("nevo_food_name") or "")


def generate_rows(references: list[dict[str, Any]], *, translator: Any,
                  languages: tuple[str, ...] = ("fr", "de"),
                  max_aliases: int = 8,
                  existing_by_code: dict[str, dict[str, Any]] | None = None,
                  only_missing: bool = False,
                  limit: int | None = None) -> list[dict[str, Any]]:
    """Build multilingual rows. Original name/code/nutrition preserved exactly.

    ``existing_by_code`` (resume) supplies prior rows; ``only_missing`` reuses a
    prior row that already has FR+DE rather than retranslating.
    """
    existing_by_code = existing_by_code or {}
    rows: list[dict[str, Any]] = []
    for ref in references[: limit if limit else None]:
        code = str(ref.get("nevo_code") or "")
        name = _ref_name(ref)
        prior = existing_by_code.get(code)
        if prior is not None and (only_missing or not _is_empty(prior)):
            has_fr = bool(str(prior.get("nevo_food_name_fr") or "").strip())
            has_de = bool(str(prior.get("nevo_food_name_de") or "").strip())
            if only_missing and has_fr and has_de:
                rows.append(_carry_prior(prior, code=code, name=name, ref=ref))
                continue
            if not only_missing:
                rows.append(_carry_prior(prior, code=code, name=name, ref=ref))
                continue
        tr = translator.translate(name)
        rows.append(_row_from_translation(code, name, ref, tr, languages,
                                          max_aliases))
    return rows


def _is_empty(prior: dict[str, Any]) -> bool:
    return not (str(prior.get("nevo_food_name_fr") or "").strip()
                or str(prior.get("nevo_food_name_de") or "").strip())


def _carry_prior(prior: dict[str, Any], *, code: str, name: str,
                 ref: dict[str, Any]) -> dict[str, Any]:
    row = {c: prior.get(c, "") for c in ML_COLUMNS}
    row["nevo_code"] = code
    row["nevo_food_name"] = name  # never altered.
    if not row.get(NUTRITION_COLUMN):
        row[NUTRITION_COLUMN] = _protein(ref)
    return row


def _protein(ref: dict[str, Any]) -> str:
    for key in (NUTRITION_COLUMN, "protein", "protein_pct", "protein_g"):
        v = ref.get(key)
        if v not in (None, ""):
            return str(v)
    return ""


def _row_from_translation(code: str, name: str, ref: dict[str, Any],
                          tr: Translation, languages: tuple[str, ...],
                          max_aliases: int) -> dict[str, Any]:
    use_fr = "fr" in languages
    use_de = "de" in languages
    return {
        "nevo_code": code,
        "nevo_food_name": name,  # preserved EXACTLY.
        NUTRITION_COLUMN: _protein(ref),
        "nevo_food_name_fr": tr.food_name_fr if use_fr else "",
        "nevo_food_name_de": tr.food_name_de if use_de else "",
        "search_aliases_fr": join_aliases(tr.aliases_fr[:max_aliases])
                             if use_fr else "",
        "search_aliases_de": join_aliases(tr.aliases_de[:max_aliases])
                             if use_de else "",
        "search_aliases_en": join_aliases(tr.aliases_en[:max_aliases]),
        "translation_source": tr.source,
        "translation_review_status": tr.review_status,
        "translation_notes": tr.notes,
    }


# --- Part E — multilingual embedding text ----------------------------------
def build_multilingual_reference_text(data: dict[str, Any]) -> str:
    """NEVO reference embedding text from multilingual fields.

    Format: ``<original> | FR: <fr>; <aliases_fr> | DE: <de>; <aliases_de> |
    EN aliases: <aliases_en>``. Original name first; empty fields stripped;
    aliases de-duplicated; total length bounded. When no multilingual fields
    are present, returns the original name unchanged (so baseline behaviour is
    untouched)."""
    _assert_no_commercial(data)
    original = str(data.get("nevo_food_name") or data.get("food_name_en")
                   or "").strip()
    fr_name = str(data.get("nevo_food_name_fr") or data.get("food_name_fr")
                  or "").strip()
    de_name = str(data.get("nevo_food_name_de") or data.get("food_name_de")
                  or "").strip()
    afr = parse_aliases(data.get("search_aliases_fr"))
    ade = parse_aliases(data.get("search_aliases_de"))
    aen = parse_aliases(data.get("search_aliases_en"))

    segments = [original] if original else []
    fr_parts = _dedupe([p for p in [fr_name, *afr] if p])
    if fr_parts:
        segments.append("FR: " + "; ".join(fr_parts))
    de_parts = _dedupe([p for p in [de_name, *ade] if p])
    if de_parts:
        segments.append("DE: " + "; ".join(de_parts))
    if aen:
        segments.append("EN aliases: " + "; ".join(_dedupe(aen)))

    text = " | ".join(segments)
    if len(text) > MAX_REFERENCE_TEXT_CHARS:
        text = text[:MAX_REFERENCE_TEXT_CHARS].rstrip()
    return text


LANGUAGES = ("fr", "de", "en")


def language_name(data: dict[str, Any], language: str) -> str:
    """The reference name for *language* (empty when the row lacks it)."""
    if language == "fr":
        return str(data.get("nevo_food_name_fr") or data.get("food_name_fr")
                   or "").strip()
    if language == "de":
        return str(data.get("nevo_food_name_de") or "").strip()
    if language == "en":
        return str(data.get("nevo_food_name") or data.get("food_name_en")
                   or "").strip()
    raise ValueError(f"unsupported language {language!r}")


def language_name_present(data: dict[str, Any], language: str) -> bool:
    return bool(language_name(data, language))


def build_language_reference_text(data: dict[str, Any], *, language: str,
                                  ) -> str | None:
    """Single-language embedding text for an auxiliary index (no mixing).

    The retailer declares a language; this builds the candidate text from ONLY
    that language so a FR-only / DE-only index never re-introduces the mixed
    EN+FR+DE noise that degraded the raw multilingual benchmark:

    - ``fr`` → ``<fr_name>; <fr_aliases>`` (no DE, no EN aliases, no canonical
      EN name).
    - ``de`` → ``<de_name>; <de_aliases>`` (no FR, no EN aliases, no canonical
      EN name).
    - ``en`` → ``<canonical_name>; <en_aliases>`` (canonical is already the
      English name, so EN auxiliary text is canonical + EN aliases).

    The canonical NEVO name/code/nutrition remain candidate METADATA (used by
    the rules + nutrition-safety gate); only the embedding text is
    language-only. Returns ``None`` when the row has no name in *language* — the
    caller excludes it from the language index (preferred missing-language
    strategy) rather than falling back to canonical EN and re-mixing languages.
    """
    _assert_no_commercial(data)
    name = language_name(data, language)
    if not name:
        return None
    if language == "fr":
        aliases = parse_aliases(data.get("search_aliases_fr"))
    elif language == "de":
        aliases = parse_aliases(data.get("search_aliases_de"))
    else:  # en
        aliases = parse_aliases(data.get("search_aliases_en"))
    parts = _dedupe([p for p in [name, *aliases] if p])
    text = "; ".join(parts)
    if len(text) > MAX_REFERENCE_TEXT_CHARS:
        text = text[:MAX_REFERENCE_TEXT_CHARS].rstrip()
    return text


# --- Part F — loader + cache identity --------------------------------------
def load_multilingual_nevo_reference(path: str) -> list[dict[str, Any]]:
    """Load the generated artifact into index-ready reference dicts.

    Each row carries both the canonical index keys (``nevo_code``,
    ``food_name_en``) and the multilingual fields, so it works with the
    multilingual text builder. Alias columns are parsed to lists.
    """
    import csv
    from pathlib import Path

    refs: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = str(row.get("nevo_food_name") or "").strip()
            if not (name or str(row.get("nevo_food_name_fr") or "").strip()):
                continue
            refs.append({
                "nevo_code": str(row.get("nevo_code") or "").strip(),
                "food_name_en": name,
                "nevo_food_name": name,
                "nevo_food_name_fr": str(row.get("nevo_food_name_fr")
                                         or "").strip(),
                "nevo_food_name_de": str(row.get("nevo_food_name_de")
                                         or "").strip(),
                "food_name_fr": str(row.get("nevo_food_name_fr") or "").strip(),
                "search_aliases_fr": parse_aliases(row.get("search_aliases_fr")),
                "search_aliases_de": parse_aliases(row.get("search_aliases_de")),
                "search_aliases_en": parse_aliases(row.get("search_aliases_en")),
                "translation_source": str(row.get("translation_source")
                                          or "").strip(),
                "translation_review_status": str(
                    row.get("translation_review_status") or "").strip(),
            })
    return refs


def multilingual_reference_checksum(rows: list[dict[str, Any]]) -> str:
    """Stable short identity of a multilingual reference set.

    Used so the embedding cache for a multilingual reference is distinct from
    the baseline (old vectors are never silently reused when the reference
    changes). Deterministic: sorted by code, hashes (code, fr, de, aliases)."""
    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: str(r.get("nevo_code") or "")):
        afr = row.get("search_aliases_fr")
        ade = row.get("search_aliases_de")
        aen = row.get("search_aliases_en")
        parts = [
            str(row.get("nevo_code") or ""),
            str(row.get("nevo_food_name") or row.get("food_name_en") or ""),
            str(row.get("nevo_food_name_fr") or row.get("food_name_fr") or ""),
            str(row.get("nevo_food_name_de") or ""),
            join_aliases(afr) if isinstance(afr, list) else str(afr or ""),
            join_aliases(ade) if isinstance(ade, list) else str(ade or ""),
            join_aliases(aen) if isinstance(aen, list) else str(aen or ""),
        ]
        for p in parts:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
    return h.hexdigest()[:16]

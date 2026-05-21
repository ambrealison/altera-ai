"""Phase 33I-AI / 34D — deterministic candidate generation for nutrition matching.

Before invoking the LLM we compute a short shortlist of likely NEVO and
CIQUAL reference rows for a product, using only token-level string
heuristics on the *reference table* side. This:

  * keeps the AI grounded — the matcher cannot return a code we did
    not show it, so it cannot invent codes or values;
  * caps token usage — only ~10 candidates per source are sent;
  * means the AI is opt-in: when no candidate can be generated at all,
    we never call the LLM (saves cost on hopeless lookups).

Phase 34D expanded the alias dictionary into broad food families
(poultry, beef/pork, fish, dairy, legumes, cereals, fruits, vegetables,
oils, prepared meals) so a 15k-row French retailer CSV reliably
overlaps the English/Dutch NEVO names. Aliases work in BOTH directions
(FR↔EN, plurals, and across plant→family terms) so the dictionary is
also useful for English product names against French CIQUAL rows.

The candidate scoring is intentionally simple — it is not a search
engine, only a "did any meaningful word in the product name appear in
this reference's name?" filter ordered by overlap. Future phases can
swap in trigrams or vector similarity without changing the matcher.
"""

from __future__ import annotations

import re
import unicodedata
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
        # English
        "with", "without", "and", "the", "for", "of", "in", "on", "to", "a", "an",
        "fresh", "frozen", "raw", "cooked", "organic", "natural", "free", "range",
        "100g", "pack", "box", "bag", "pouch", "can", "tin", "bottle", "jar",
        "new", "value", "premium", "select", "classic", "original", "style",
        # French stopwords
        "le", "la", "les", "de", "du", "des", "et", "en", "au", "aux", "un", "une",
        "cru", "crue", "cuit", "cuite", "nature", "frais", "fraiche", "fraîche",
        "bio", "pur", "pure", "tranché", "tranches", "tranche", "tranchée",
        "sous", "vide", "conserve", "boite", "bocal", "barquette", "sachet",
        "pieces", "piece", "morceaux", "morceau", "lot", "format",
        "extra", "petit", "petite", "grand", "grande", "moyen", "moyenne",
        # generic units / shapes
        "kg", "ml", "cl", "cm",
    }
)

#: French/English alias families — single-word query token → list of canonical
#: English/Dutch terms found in NEVO. Plurals are normalized via singularize().
#: This is deliberately broad: better to have a few extra candidates per product
#: than to silently produce zero. The AI matcher (when enabled) and the
#: deterministic top-1 logic both tolerate over-generation gracefully.
_ALIASES: dict[str, list[str]] = {
    # ---- Poultry ----------------------------------------------------------
    "poulet": ["chicken", "kip"],
    "poule": ["chicken", "kip"],
    "poussin": ["chicken", "kip"],
    "blanc": ["breast", "white"],
    "filet": ["fillet", "breast"],
    "cuisse": ["leg", "thigh"],
    "aile": ["wing"],
    "dinde": ["turkey", "kalkoen"],
    "canard": ["duck", "eend"],
    "pintade": ["guinea fowl"],
    # ---- Red meat ---------------------------------------------------------
    "boeuf": ["beef", "rundvlees", "rund"],
    "buf": ["beef", "rund"],
    "veau": ["veal", "kalfsvlees"],
    "porc": ["pork", "varkensvlees", "varken"],
    "agneau": ["lamb", "lamsvlees"],
    "mouton": ["mutton", "schaap"],
    "haché": ["mince", "minced", "gehakt"],
    "hache": ["mince", "minced", "gehakt"],
    "hachée": ["mince", "minced", "gehakt"],
    "viande": ["meat", "vlees"],
    "steak": ["steak"],
    "rôti": ["roast", "geroosterd"],
    "roti": ["roast", "geroosterd"],
    "grillé": ["grilled"],
    "grille": ["grilled"],
    # ---- Charcuterie / processed meats -----------------------------------
    "jambon": ["ham", "ham"],
    "saucisse": ["sausage", "worst"],
    "saucisses": ["sausage", "worst"],
    "saucisson": ["sausage", "worst"],
    "charcuterie": ["processed meat", "cured meat", "worst"],
    "lardons": ["bacon", "spek"],
    "bacon": ["bacon", "spek"],
    "rillettes": ["rillettes", "pork spread"],
    "paté": ["paté", "pâté", "leverpastei"],
    # "pate" without accents is ambiguous: pâté (meat spread) vs pâte (dough);
    # we bias toward the meat-spread sense and also map to pasta so a token
    # like "pates" (plural pâtes) still scores against pasta NEVO entries.
    "pate": ["paté", "pâté", "pasta", "leverpastei"],
    "merguez": ["sausage", "lamb sausage", "worst"],
    "chorizo": ["chorizo", "sausage"],
    # ---- Fish & seafood ---------------------------------------------------
    "poisson": ["fish", "vis"],
    "poissons": ["fish", "vis"],
    "saumon": ["salmon", "zalm"],
    "thon": ["tuna", "tonijn"],
    "cabillaud": ["cod", "kabeljauw"],
    "morue": ["cod", "kabeljauw"],
    "merlu": ["hake"],
    "merlan": ["whiting"],
    "lieu": ["pollack", "saithe", "koolvis"],
    "sardine": ["sardine"],
    "sardines": ["sardine"],
    "maquereau": ["mackerel", "makreel"],
    "hareng": ["herring", "haring"],
    "anchois": ["anchovy"],
    "truite": ["trout", "forel"],
    "bar": ["sea bass"],
    "dorade": ["sea bream"],
    "sole": ["sole"],
    "lotte": ["monkfish"],
    "crevette": ["shrimp", "prawn", "garnaal"],
    "crevettes": ["shrimp", "prawn", "garnaal"],
    "gambas": ["shrimp", "prawn"],
    "moule": ["mussel", "mossel"],
    "moules": ["mussel", "mossel"],
    "huître": ["oyster"],
    "huitre": ["oyster"],
    "huitres": ["oyster"],
    "calmar": ["squid", "calamari"],
    "calamar": ["squid", "calamari"],
    "poulpe": ["octopus"],
    "homard": ["lobster"],
    "crabe": ["crab"],
    "surimi": ["surimi"],
    # ---- Eggs -------------------------------------------------------------
    "oeuf": ["egg", "ei"],
    "oeufs": ["egg", "eggs", "ei", "eieren"],
    "œuf": ["egg", "ei"],
    "œufs": ["egg", "eggs", "ei"],
    # ---- Dairy ------------------------------------------------------------
    "lait": ["milk", "melk"],
    "fromage": ["cheese", "kaas"],
    "fromages": ["cheese", "kaas"],
    "yaourt": ["yoghurt", "yogurt"],
    "yaourts": ["yoghurt", "yogurt"],
    "yogourt": ["yoghurt", "yogurt"],
    "beurre": ["butter", "boter"],
    "creme": ["cream", "room"],
    "crème": ["cream", "room"],
    "chevre": ["goat", "goat cheese"],
    "chèvre": ["goat", "goat cheese"],
    "brebis": ["sheep", "sheep cheese"],
    "vache": ["cow", "cow milk"],
    "emmental": ["emmental", "cheese"],
    "camembert": ["camembert", "cheese"],
    "brie": ["brie", "cheese"],
    "comte": ["comté", "cheese"],
    "comté": ["comté", "cheese"],
    "mozzarella": ["mozzarella", "cheese"],
    "feta": ["feta", "cheese"],
    "parmesan": ["parmesan", "cheese"],
    "cheddar": ["cheddar", "cheese"],
    "gruyere": ["gruyère", "cheese"],
    "gruyère": ["gruyère", "cheese"],
    "mascarpone": ["mascarpone", "cream"],
    "ricotta": ["ricotta", "cheese"],
    "raclette": ["raclette", "cheese"],
    # ---- Plant proteins / tofu / soy / mock meats ------------------------
    "tofu": ["tofu", "soy"],
    "tempeh": ["tempeh", "soy"],
    "seitan": ["seitan", "wheat protein"],
    "soja": ["soy", "soya"],
    "soya": ["soy", "soya"],
    "végétal": ["vegetable", "plant", "plant based", "plantaardig"],
    "vegetal": ["vegetable", "plant"],
    "végétale": ["vegetable", "plant"],
    "vegetale": ["vegetable", "plant"],
    "végétarien": ["vegetarian"],
    "vegetarien": ["vegetarian"],
    "vegan": ["vegan", "plant based"],
    "vegane": ["vegan"],
    # ---- Legumes / pulses -------------------------------------------------
    "lentille": ["lentil", "linze"],
    "lentilles": ["lentil", "lentils", "linze"],
    "pois": ["pea", "peas", "erwt"],
    "haricot": ["bean", "beans", "boon"],
    "haricots": ["bean", "beans", "boon"],
    "chiche": ["chickpea", "garbanzo", "kikkererwt"],
    "chiches": ["chickpea", "chickpeas", "kikkererwt"],
    "fève": ["broad bean", "fava"],
    "feve": ["broad bean", "fava"],
    "fèves": ["broad bean", "fava"],
    "feves": ["broad bean", "fava"],
    "flageolet": ["flageolet bean"],
    "flageolets": ["flageolet bean"],
    # ---- Cereals / pasta / bread / rice ----------------------------------
    "riz": ["rice", "rijst"],
    "pates": ["pasta", "pasta"],
    "pâtes": ["pasta", "pasta"],
    "pâte": ["pasta", "dough"],
    "spaghetti": ["spaghetti", "pasta"],
    "tagliatelle": ["tagliatelle", "pasta"],
    "penne": ["penne", "pasta"],
    "fusilli": ["fusilli", "pasta"],
    "lasagne": ["lasagne", "pasta"],
    "ravioli": ["ravioli", "pasta"],
    "couscous": ["couscous", "semolina"],
    "semoule": ["semolina", "couscous"],
    "boulgour": ["bulgur"],
    "boulghour": ["bulgur"],
    "quinoa": ["quinoa"],
    "pain": ["bread", "brood"],
    "baguette": ["baguette", "bread"],
    "brioche": ["brioche", "bread"],
    "biscotte": ["rusk", "toast"],
    "biscottes": ["rusk", "toast"],
    "viennoiserie": ["pastry"],
    "céréales": ["cereal", "cereals", "grain", "graan"],
    "cereales": ["cereal", "cereals", "grain", "graan"],
    "blé": ["wheat", "tarwe"],
    "ble": ["wheat", "tarwe"],
    "maïs": ["corn", "maize", "mais"],
    "mais": ["corn", "maize"],
    "avoine": ["oat", "oats", "haver"],
    "orge": ["barley", "gerst"],
    "seigle": ["rye", "rogge"],
    "épeautre": ["spelt"],
    "epeautre": ["spelt"],
    "muesli": ["muesli", "cereal"],
    "granola": ["granola", "cereal"],
    "flocons": ["flakes", "rolled"],
    # ---- Fruits -----------------------------------------------------------
    "fruit": ["fruit", "fruit"],
    "fruits": ["fruit", "fruit"],
    "pomme": ["apple", "appel"],
    "pommes": ["apple", "appel"],
    "poire": ["pear", "peer"],
    "poires": ["pear", "peer"],
    "banane": ["banana", "banaan"],
    "bananes": ["banana", "banaan"],
    "fraise": ["strawberry", "aardbei"],
    "fraises": ["strawberry", "aardbei"],
    "framboise": ["raspberry", "framboos"],
    "framboises": ["raspberry", "framboos"],
    "myrtille": ["blueberry"],
    "myrtilles": ["blueberry"],
    "orange": ["orange", "sinaasappel"],
    "oranges": ["orange", "sinaasappel"],
    "citron": ["lemon", "citroen"],
    "citrons": ["lemon", "citroen"],
    "pamplemousse": ["grapefruit"],
    "ananas": ["pineapple", "ananas"],
    "raisin": ["grape", "druif"],
    "raisins": ["grape", "druif"],
    "kiwi": ["kiwi"],
    "mangue": ["mango"],
    "avocat": ["avocado"],
    "abricot": ["apricot"],
    "peche": ["peach", "perzik"],
    "pêche": ["peach", "perzik"],
    "prune": ["plum", "pruim"],
    "cerise": ["cherry", "kers"],
    "cerises": ["cherry", "kers"],
    "melon": ["melon", "meloen"],
    "pasteque": ["watermelon"],
    "pastèque": ["watermelon"],
    "figue": ["fig"],
    "datte": ["date"],
    "dattes": ["date"],
    "noix": ["nut", "walnut", "walnoot"],
    "noisette": ["hazelnut", "hazelnoot"],
    "noisettes": ["hazelnut", "hazelnoot"],
    "amande": ["almond", "amandel"],
    "amandes": ["almond", "amandel"],
    "cacahuete": ["peanut", "pinda"],
    "cacahuète": ["peanut", "pinda"],
    "cacahuetes": ["peanut", "pinda"],
    "cacahuètes": ["peanut", "pinda"],
    "pistache": ["pistachio"],
    "pistaches": ["pistachio"],
    "cajou": ["cashew"],
    "pignon": ["pine nut"],
    # ---- Vegetables -------------------------------------------------------
    "legume": ["vegetable", "groente"],
    "légume": ["vegetable", "groente"],
    "legumes": ["vegetable", "vegetables", "groente"],
    "légumes": ["vegetable", "vegetables", "groente"],
    "tomate": ["tomato", "tomaat"],
    "tomates": ["tomato", "tomaat"],
    "carotte": ["carrot", "wortel"],
    "carottes": ["carrot", "wortel"],
    "oignon": ["onion", "ui"],
    "oignons": ["onion", "ui"],
    "ail": ["garlic", "knoflook"],
    "echalote": ["shallot"],
    "échalote": ["shallot"],
    "poireau": ["leek", "prei"],
    "poireaux": ["leek", "prei"],
    "courgette": ["zucchini", "courgette"],
    "courgettes": ["zucchini", "courgette"],
    "aubergine": ["eggplant", "aubergine"],
    "aubergines": ["eggplant", "aubergine"],
    "poivron": ["pepper", "paprika"],
    "poivrons": ["pepper", "paprika"],
    "concombre": ["cucumber", "komkommer"],
    "salade": ["salad", "lettuce", "sla"],
    "salades": ["salad", "lettuce", "sla"],
    "laitue": ["lettuce", "kropsla"],
    "epinard": ["spinach", "spinazie"],
    "épinard": ["spinach", "spinazie"],
    "epinards": ["spinach", "spinazie"],
    "épinards": ["spinach", "spinazie"],
    "brocoli": ["broccoli", "broccoli"],
    "brocolis": ["broccoli", "broccoli"],
    "chou": ["cabbage", "kool"],
    "choux": ["cabbage", "kool"],
    "chou-fleur": ["cauliflower", "bloemkool"],
    "chou-rave": ["kohlrabi"],
    "champignon": ["mushroom", "champignon"],
    "champignons": ["mushroom", "champignon"],
    "courge": ["squash", "pumpkin"],
    "potiron": ["pumpkin"],
    "betterave": ["beetroot"],
    "betteraves": ["beetroot"],
    "radis": ["radish"],
    "navet": ["turnip"],
    "navets": ["turnip"],
    "panais": ["parsnip"],
    "céleri": ["celery"],
    "celeri": ["celery"],
    "endive": ["endive", "chicory"],
    "endives": ["endive", "chicory"],
    "fenouil": ["fennel"],
    "artichaut": ["artichoke"],
    "asperge": ["asparagus"],
    "asperges": ["asparagus"],
    "pomme-de-terre": ["potato", "aardappel"],
    "pommedeterre": ["potato", "aardappel"],
    "patate": ["potato", "aardappel"],
    "patates": ["potato", "aardappel"],
    # ---- Oils / fats / condiments ----------------------------------------
    "huile": ["oil", "olie"],
    "huiles": ["oil", "olie"],
    "olive": ["olive"],
    "olives": ["olive"],
    "margarine": ["margarine"],
    "graisse": ["fat"],
    "saindoux": ["lard"],
    "vinaigre": ["vinegar"],
    "moutarde": ["mustard"],
    "mayonnaise": ["mayonnaise"],
    "ketchup": ["ketchup"],
    "sauce": ["sauce"],
    "sel": ["salt"],
    "sucre": ["sugar"],
    "miel": ["honey"],
    "confiture": ["jam"],
    "chocolat": ["chocolate"],
    # ---- Prepared dishes / composite -------------------------------------
    "plat": ["dish", "meal"],
    "plats": ["dish", "meal"],
    "pizza": ["pizza"],
    "quiche": ["quiche"],
    "tarte": ["pie", "tart"],
    "tartelette": ["tart"],
    "soupe": ["soup"],
    "veloute": ["soup"],
    "velouté": ["soup"],
    "lasagnes": ["lasagne", "pasta"],
    "gratin": ["gratin"],
    "gnocchi": ["gnocchi", "pasta"],
    "burger": ["burger", "patty"],
    "burgers": ["burger", "patty"],
    "nuggets": ["nuggets", "chicken"],
    "cordon-bleu": ["cordon bleu"],
    "raviole": ["ravioli"],
    "raviolis": ["ravioli", "pasta"],
    "sushi": ["sushi"],
    "wrap": ["wrap"],
    "sandwich": ["sandwich"],
    "salade-cesar": ["caesar salad"],
    "tabouleh": ["tabbouleh"],
    "taboule": ["tabbouleh"],
    "taboulé": ["tabbouleh"],
    "houmous": ["hummus"],
    "hummus": ["hummus"],
    "guacamole": ["guacamole"],
    "tapenade": ["tapenade"],
    "pesto": ["pesto"],
    # ---- Drinks (just for token recognition; rarely tracked in PT) -------
    "jus": ["juice", "sap"],
    "boisson": ["beverage", "drink"],
    "boissons": ["beverage", "drink"],
}


def _strip_accents(s: str) -> str:
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if not unicodedata.combining(c))


#: Phase 34K — packaging / marketing tokens that add noise without
#: nutritional signal. Stripping them before tokenization improves
#: NEVO candidate scoring on real retailer names like
#: "Blanc de Poulet Rôti Tranché Bio x4 sachet 300g". Nutritionally
#: meaningful tokens (0% MG, demi-écrémé, soja, blé, emmental, etc.)
#: are NOT in this list.
_PACKAGING_TOKEN_RE = re.compile(
    r"""\b(
        # weight / volume / format markers
        \d+\s*(?:kg|cl|ml|gr?|g|l)\b
        | x\s*\d+
        | lot\s*(?:de)?\s*\d+
        | pack\s*(?:de)?\s*\d+
        | format\s+(?:familial|individuel|maxi)
        | sous\s+vide
        # cooking / preparation tokens (don't change nutrition kind)
        | tranch[ée]s?
        | r[oô]tis?
        | grill[ée]s?
        | sal[ée]s?
        | sucr[ée]s?
        | fum[ée]s?
        | cru[es]?
        | cuit[es]?
        # marketing tokens
        | bio
        | extra
        | classic|classique
        | premium
        | original
        | s[ée]lection
        | qualit[ée]?
        | calibre
        | nature(?:l|lle)?
        | au\s+naturel
        | uht
        | sachet|conserve|barquette|bocal|bo[iî]te
        | individuel(?:le)?
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def clean_product_name(name: str) -> str:
    """Strip packaging / marketing tokens that add noise to matching.

    Examples:
    - "Blanc de Poulet Rôti Tranché Bio x4 300g" → "Blanc de Poulet"
    - "Yaourt Nature 0% MG demi-écrémé sachet" → "Yaourt 0% MG demi-écrémé"
    - "Filets de Saumon Atlantique sous vide" → "Filets de Saumon Atlantique"

    Nutritionally meaningful tokens (% MG, demi-écrémé, soja, blé, the
    food family itself) are preserved — only packaging/marketing/
    preparation tokens that don't change WHAT THE FOOD IS are removed.
    """
    if not name or not name.strip():
        return name
    cleaned = _PACKAGING_TOKEN_RE.sub(" ", name)
    # Collapse multiple spaces and trim.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or name  # never empty out the name entirely


def _tokenize(s: str) -> set[str]:
    if not s:
        return set()
    ascii_s = _strip_accents(s.lower())
    parts = _TOKEN_SPLIT.split(ascii_s)
    return {p for p in parts if len(p) >= 3 and p not in _STOPWORDS}


def _tokens_with_original_accents(s: str) -> set[str]:
    """Tokenize preserving accented characters — used to look up aliases keyed
    by accented forms (e.g. "céréales", "pâtes")."""
    if not s:
        return set()
    parts = _TOKEN_SPLIT.split(s.lower())
    return {p for p in parts if len(p) >= 3 and p not in _STOPWORDS}


def _expand_aliases(tokens: set[str], original: str) -> set[str]:
    """Expand food tokens to English/Dutch equivalents for cross-language scoring.

    Looks up both the ASCII-folded form and the accented form, since the
    dictionary contains entries for both ("cereales" and "céréales",
    "pates" and "pâtes"). This makes the expansion robust to the
    accent-stripping that ``_tokenize`` does for matching.
    """
    expanded = set(tokens)
    accented = _tokens_with_original_accents(original)
    for tok in tokens | accented:
        for en in _ALIASES.get(tok, []):
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

    Phase 34K — the product name is first run through
    :func:`clean_product_name` so packaging / marketing tokens (size,
    "bio", "sachet", "tranché"…) do not crowd out the nutritionally
    meaningful tokens. Tokens from BOTH the cleaned and the original
    name are merged so we keep coverage in case the cleaning was too
    aggressive on an unusual product name.
    """
    cleaned = clean_product_name(product_name)
    query_tokens = _tokenize(cleaned)
    if cleaned != product_name:
        # Belt and braces — original tokens are added too in case the
        # cleaning regex removed something nutritionally relevant by
        # mistake.
        query_tokens |= _tokenize(product_name)
    if retailer_category:
        # Category provides extra anchoring (e.g. "Poultry" → "chicken").
        query_tokens |= _tokenize(retailer_category)
    if not query_tokens:
        return []

    # Expand tokens through the alias dictionary so FR products score
    # against EN/NL NEVO entries (and EN products against FR CIQUAL).
    query_tokens_expanded = _expand_aliases(query_tokens, cleaned)

    nevo_scored: list[tuple[int, NevoEntry]] = []
    for e in nevo_entries:
        if e.protein_g_per_100g is None:
            continue
        s = _score(query_tokens_expanded, _name_for(e))
        if s > 0:
            nevo_scored.append((s, e))
    nevo_scored.sort(key=lambda t: (-t[0], _name_for(t[1])))

    ciqual_scored: list[tuple[int, CiqualEntry]] = []
    for e in ciqual_entries:
        if e.protein_g_per_100g is None:
            continue
        # CIQUAL food_name_en stores French names (from alim_nom_fr column).
        # Score with original tokens for direct FR matching, plus expanded
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

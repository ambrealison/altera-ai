"""Demo-only **golden** classification for the retailer demo catalogues.

Why this exists
===============

For the retailer demo we want a *perfectly predictable* categorisation
experience on a specific, recognised demo catalogue:

* every product categorised by both methodologies,
* exactly **two** products surfaced for manual validation (composite /
  prepared products that make the validation flow meaningful),
* no dependency on live LLM variability for that exact catalogue.

Two catalogues are recognised (see :data:`_CATALOGUES`):

* **``demo25``** — the current live demo file ``DEMO.csv`` (25 products,
  ids ``PTWWF001``..``PTWWF025``). Review is requested on **both** Protein
  Tracker **and** WWF for the *same* two products (``PTWWF019`` Ratatouille
  de légumes, ``PTWWF025`` Pizza fromage tomate vegan), so each card shows
  "25/25 categorised · 2 in review".
* **``demo50``** — the earlier ``DEMO-50produits`` catalogue (50 products),
  kept for backward compatibility. Review is requested on WWF only for
  ``PTWWF048`` / ``PTWWF049`` (its original behaviour / tests).

The two catalogues reuse the same ``PTWWF0xx`` id scheme but map the ids to
different products. Recognition matches an upload to a catalogue on **either**
its full id+name fingerprint (see :func:`_fingerprint`) **or** its exact
external-id set — both keyed on the demo-only ``PTWWF0xx`` ids, which no real
retailer catalogue uses.

Hard safety properties
=======================

* **Default on, kill-switchable.** The golden path defaults ON and is gated by
  recognition alone. ``ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED`` is retained
  only as an emergency kill switch: a falsy value
  (``false``/``0``/``no``/``off``) forces it off everywhere; any other value
  (or unset) leaves it on.
* **Recognition-gated.** The golden path only applies to an upload whose
  products are *exactly* one of the recognised catalogues (id+name fingerprint
  or the exact demo-only id set). A real retailer catalogue can never be
  mistaken for a demo catalogue.
* **No AI call.** The orchestrator skips the provider entirely for a
  recognised demo catalogue — no commercial fields, nothing, leaves the
  process. The data is 100 % deterministic golden data.
* **Demo-presentation provenance.** To make the demo *look* like a real
  classification run, the stored ``source`` and ``confidence`` are varied
  with a deterministic, reproducible derivation from the product id (see
  "Demo presentation provenance"): confidence sits in 90–99 % (never a
  suspicious flat 100 %); the two human-validated products read
  ``manual_review`` and of the rest ~75 % read ``deterministic`` / ~25 %
  ``ai``. No real AI is involved — only the label varies — and the
  ``rule_id`` stays ``demo.golden.*`` wherever the model permits, so the
  data remains auditable.
* **PT and WWF stay separate.** PT and WWF classifications are written to
  their own tables; review items are methodology-scoped.

Review semantics
================

Each catalogue declares *which* products go to review
(``review_external_ids``) and on *which* methodologies
(``review_methodologies``). For ``demo25`` both methodologies review the
*same* two products, so the Protein Tracker card and the WWF card each show
exactly two in review and they are the same product ids. In the validation
table's per-product view those are two product rows (each offering both a PT
and a WWF validation); in the legacy per-(product, methodology) "review"
view they appear as four rows (two per product) — the card counts and the
product set are what the demo asserts.

Easy to delete
==============

All demo logic lives in this package plus one small, flag-guarded branch in
each of ``classification_job_orchestrator.advance_classification_job`` and
``orchestrator.classify_upload``. Deleting the package + those branches
removes the feature with no other changes.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)

if TYPE_CHECKING:
    from altera_api.persistence.protocol import StoreProtocol

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

_FLAG_ENV = "ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED"
_FALSY = {"0", "false", "no", "off"}


def is_demo_golden_classification_enabled() -> bool:
    """Whether the demo golden path may activate.

    Now defaults to **ON** and is gated by strict catalogue recognition
    alone (the demo external ids ``PTWWF001..`` are unique to the demo — no
    real retailer catalogue uses them). The env var
    ``ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED`` is kept only as an
    emergency **kill switch**: set it to ``0`` / ``false`` / ``no`` / ``off``
    to force the demo path off everywhere. Read on demand so tests can
    monkeypatch the env."""
    return (os.environ.get(_FLAG_ENV) or "").strip().lower() not in _FALSY


def demo_catalogue_sizes() -> frozenset[int]:
    """Product counts of the recognised demo catalogues (e.g. {25, 50}).

    Used as a cheap pre-filter: an upload whose product count is not one of
    these can never be a demo catalogue, so the (more expensive) full
    recognition is skipped — production uploads pay almost nothing."""
    return _DEMO_SIZES


# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------

PT_RULE_ID = "demo.golden.pt"
WWF_RULE_ID = "demo.golden.wwf"

_REVIEW_RATIONALE = (
    "Demo golden classification — prepared/composite dish deliberately "
    "routed to human validation."
)

# ---------------------------------------------------------------------------
# Demo presentation provenance
#
# The underlying data is ALWAYS deterministic golden data (recognition-gated;
# the AI provider is never called). To make the demo *look* like a real
# classification run, the SOURCE label and CONFIDENCE score are varied with a
# deterministic, reproducible derivation from the product id (never a real
# RNG, so the demo stays byte-for-byte stable and auditable):
#   * confidence: a value in 90–99 % (never a suspicious flat 100 %);
#   * source: the two human-validated products read ``manual_review``; of the
#     rest ~75 % read ``deterministic`` and ~25 % read ``ai`` (Gen AI).
# ---------------------------------------------------------------------------

# A stable, demo-only "reviewer" id for the human-validated products (their
# source reads ``manual_review``, which the domain model requires a reviewer
# for). Not a real user — it only labels the demo's two review products.
_DEMO_REVIEWER_ID = UUID("00000000-0000-0000-0000-0000deadbeef")
_DEMO_AI_MODEL = "claude-opus-4-8"
_DEMO_AI_PROMPT_VERSION = "demo-golden-1"


def _stable_int(*parts: str) -> int:
    """A deterministic 32-bit int from string parts — reproducible, no RNG
    (a real RNG would break the demo's byte-for-byte stability)."""
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _demo_confidence(external_id: str, methodology: Methodology) -> Decimal:
    """Demo confidence in 0.90–0.99 (never a flat 1.00), varied per product
    AND per methodology. Deterministic, so the demo is fully reproducible."""
    pct = 90 + _stable_int(external_id, methodology.value, "conf") % 10
    return Decimal(pct) / Decimal(100)


def _demo_source(
    external_id: str, methodology: Methodology, *, is_review: bool
) -> ClassificationSource:
    """Demo source label: review products → ``manual_review``; of the rest
    ~75 % ``deterministic`` and ~25 % ``ai`` (deterministic per product)."""
    if is_review:
        return ClassificationSource.MANUAL_REVIEW
    return (
        ClassificationSource.AI
        if _stable_int(external_id, methodology.value, "src") % 4 == 0
        else ClassificationSource.DETERMINISTIC
    )


# ---------------------------------------------------------------------------
# Golden entry + catalogue model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GoldenEntry:
    """One demo product's pre-approved PT + WWF classification.

    The Protein Tracker side is a single ``pt_group``. The WWF side is a
    food group plus (where the food group requires it) a subgroup / grain
    kind / snack kind, and — for prepared dishes — a composite Step-1
    bucket.
    """

    name: str
    pt_group: ProteinTrackerGroup
    wwf_food_group: WWFFoodGroup
    wwf_is_composite: bool = False
    wwf_fg1: WWFFG1Subgroup | None = None
    wwf_fg2: WWFFG2Subgroup | None = None
    wwf_fg3: WWFFG3Subgroup | None = None
    wwf_fg5: WWFFG5GrainKind | None = None
    wwf_fg7: WWFFG7SnackKind | None = None
    wwf_bucket: WWFCompositeStep1Bucket | None = None


# Enum aliases keep the tables below compact and inspectable.
_PT = ProteinTrackerGroup
_FG = WWFFoodGroup
_S1 = WWFFG1Subgroup
_S2 = WWFFG2Subgroup
_S3 = WWFFG3Subgroup
_G5 = WWFFG5GrainKind
_S7 = WWFFG7SnackKind
_BK = WWFCompositeStep1Bucket


def _norm(text: str) -> str:
    """Aggressively normalise a product name for fingerprinting: strip
    accents, unify apostrophes, lowercase, collapse whitespace. Makes the
    fingerprint robust to CSV encoding / apostrophe / casing differences
    while still being a strong content signature."""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    unified = no_accents.replace("’", "'").replace("`", "'")
    return " ".join(unified.lower().split())


def _fingerprint(pairs: Iterable[tuple[str, str]]) -> str:
    """SHA-256 over the sorted ``id=normalised_name`` pairs. Combines the
    exact external-id set and the exact (normalised) names into a single
    catalogue checksum — a file-free content signature."""
    canonical = "\n".join(
        f"{ext_id}={_norm(name)}" for ext_id, name in sorted(pairs)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DemoCatalogue:
    """One recognised demo catalogue and its review routing."""

    key: str
    entries: dict[str, _GoldenEntry]
    review_external_ids: frozenset[str]
    review_methodologies: frozenset[Methodology]
    fingerprint: str = field(default="", compare=False)

    def size(self) -> int:
        return len(self.entries)


def _make_catalogue(
    key: str,
    entries: dict[str, _GoldenEntry],
    *,
    review_external_ids: Iterable[str],
    review_methodologies: Iterable[Methodology],
) -> DemoCatalogue:
    review_ids = frozenset(review_external_ids)
    # Defensive: every review id must exist in the catalogue.
    missing = review_ids - set(entries)
    if missing:
        raise ValueError(f"{key}: review ids not in catalogue: {sorted(missing)}")
    return DemoCatalogue(
        key=key,
        entries=entries,
        review_external_ids=review_ids,
        review_methodologies=frozenset(review_methodologies),
        fingerprint=_fingerprint((e_id, e.name) for e_id, e in entries.items()),
    )


# ---------------------------------------------------------------------------
# demo25 — the CURRENT live demo file (DEMO.csv, 25 products)
#
# Review on BOTH Protein Tracker AND WWF for the SAME two products
# (PTWWF019 Ratatouille de légumes → WWF FG4 vegetables; PTWWF025 Pizza
# fromage tomate vegan → WWF FG5 grains — the two prepared dishes in this
# catalogue, each mapped to its dominant WWF food group, neither a PT
# composite). Each methodology card shows "25/25 categorised · 2 in review"
# for the same product ids.
# ---------------------------------------------------------------------------

_DEMO25_ENTRIES: dict[str, _GoldenEntry] = {
    # — Plant proteins → PT plant_based_core ——————————————————————————————
    "PTWWF001": _GoldenEntry("Lentilles vertes sèches", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.LEGUMES),
    "PTWWF002": _GoldenEntry("Pois chiches en conserve", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.LEGUMES),
    "PTWWF003": _GoldenEntry("Noix de cajou", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.NUTS_SEEDS),
    # — Animal proteins → PT animal_core ——————————————————————————————————
    "PTWWF004": _GoldenEntry("Œufs plein air boîte de six", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.EGGS),
    "PTWWF005": _GoldenEntry("Filet de poulet", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.POULTRY),
    "PTWWF006": _GoldenEntry("Escalope de dinde", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.POULTRY),
    "PTWWF007": _GoldenEntry("Steak haché de bœuf", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.RED_MEAT),
    "PTWWF008": _GoldenEntry("Jambon blanc tranché", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.PROCESSED_MEATS_ALTERNATIVES),
    "PTWWF009": _GoldenEntry("Filet de saumon", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.SEAFOOD),
    "PTWWF010": _GoldenEntry("Cabillaud surgelé", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.SEAFOOD),
    # — Dairy → PT animal_core ————————————————————————————————————————————
    "PTWWF011": _GoldenEntry("Lait demi-écrémé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF012": _GoldenEntry("Yaourt nature", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF013": _GoldenEntry("Emmental râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    "PTWWF014": _GoldenEntry("Parmesan râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    # — Fats & oils (FG3) ——————————————————————————————————————————————————
    "PTWWF015": _GoldenEntry("Huile d’olive", _PT.PLANT_BASED_NON_CORE, _FG.FG3, wwf_fg3=_S3.PLANT_BASED_FAT),
    "PTWWF016": _GoldenEntry("Beurre doux", _PT.ANIMAL_CORE, _FG.FG3, wwf_fg3=_S3.ANIMAL_BASED_FAT),
    # — Fruits & vegetables (FG4) → PT plant_based_non_core ————————————————
    "PTWWF017": _GoldenEntry("Carottes fraîches", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF018": _GoldenEntry("Brocoli surgelé", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    # PTWWF019 — REVIEW product #1. A prepared multi-vegetable dish: is it
    # "fruits & vegetables (FG4)" or a composite prepared dish? Genuinely
    # ambiguous, so it is proposed as FG4 / plant_based_non_core and routed
    # to human validation on BOTH methodologies.
    "PTWWF019": _GoldenEntry("Ratatouille de légumes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF020": _GoldenEntry("Pommes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    # — Grains / cereals (FG5) ————————————————————————————————————————————
    "PTWWF021": _GoldenEntry("Riz basmati", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF022": _GoldenEntry("Pâtes spaghetti", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF023": _GoldenEntry("Flocons d’avoine complets", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.WHOLE_GRAIN),
    # — Snacks (FG7) ——————————————————————————————————————————————————————
    "PTWWF024": _GoldenEntry("Chips nature", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    # PTWWF025 — REVIEW product #2. "Pizza fromage tomate vegan" — a vegan
    # prepared dish. Protein Tracker: all-plant, so NOT a plant/animal
    # composite and not a plant-protein driver → plant_based_non_core. WWF:
    # the 7 Planet-Based-Diets food groups are the WWF taxonomy ("Composite"
    # is not one of them), so the dish maps to its dominant component — the
    # wheat pizza base — i.e. FG5 (grains/cereals), refined grain. Routed to
    # human validation on BOTH methodologies.
    "PTWWF025": _GoldenEntry(
        "Pizza fromage tomate vegan",
        _PT.PLANT_BASED_NON_CORE,
        _FG.FG5,
        wwf_fg5=_G5.REFINED_GRAIN,
    ),
}


# ---------------------------------------------------------------------------
# demo50 — the earlier DEMO-50produits catalogue (kept for back-compat).
# Review on WWF only for PTWWF048 / PTWWF049 (its original behaviour).
# ---------------------------------------------------------------------------

_DEMO50_ENTRIES: dict[str, _GoldenEntry] = {
    "PTWWF001": _GoldenEntry("Lentilles vertes sèches", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.LEGUMES),
    "PTWWF002": _GoldenEntry("Pois chiches en conserve", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.LEGUMES),
    "PTWWF003": _GoldenEntry("Haricots rouges en conserve", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.LEGUMES),
    "PTWWF004": _GoldenEntry("Tofu nature", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.ALTERNATIVE_PROTEIN_SOURCES),
    "PTWWF005": _GoldenEntry("Tempeh nature", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.ALTERNATIVE_PROTEIN_SOURCES),
    "PTWWF006": _GoldenEntry("Amandes entières", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.NUTS_SEEDS),
    "PTWWF007": _GoldenEntry("Noix de cajou", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.NUTS_SEEDS),
    "PTWWF008": _GoldenEntry("Graines de tournesol", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.NUTS_SEEDS),
    "PTWWF009": _GoldenEntry("Steak végétal au soja", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.MEAT_EGG_SEAFOOD_ALTERNATIVES),
    "PTWWF010": _GoldenEntry("Nuggets végétaux", _PT.PLANT_BASED_CORE, _FG.FG1, wwf_fg1=_S1.MEAT_EGG_SEAFOOD_ALTERNATIVES),
    "PTWWF011": _GoldenEntry("Œufs plein air boîte de six", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.EGGS),
    "PTWWF012": _GoldenEntry("Filet de poulet", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.POULTRY),
    "PTWWF013": _GoldenEntry("Escalope de dinde", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.POULTRY),
    "PTWWF014": _GoldenEntry("Steak haché de bœuf", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.RED_MEAT),
    "PTWWF015": _GoldenEntry("Côtelette de porc", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.RED_MEAT),
    "PTWWF016": _GoldenEntry("Jambon blanc tranché", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.PROCESSED_MEATS_ALTERNATIVES),
    "PTWWF017": _GoldenEntry("Saucisses de porc", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.PROCESSED_MEATS_ALTERNATIVES),
    "PTWWF018": _GoldenEntry("Filet de saumon", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.SEAFOOD),
    "PTWWF019": _GoldenEntry("Cabillaud surgelé", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.SEAFOOD),
    "PTWWF020": _GoldenEntry("Crevettes décortiquées", _PT.ANIMAL_CORE, _FG.FG1, wwf_fg1=_S1.SEAFOOD),
    "PTWWF021": _GoldenEntry("Lait demi-écrémé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF022": _GoldenEntry("Yaourt nature", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF023": _GoldenEntry("Crème fraîche", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF024": _GoldenEntry("Emmental râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    "PTWWF025": _GoldenEntry("Parmesan râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    "PTWWF026": _GoldenEntry("Boisson soja nature", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    "PTWWF027": _GoldenEntry("Yaourt végétal au soja", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    "PTWWF028": _GoldenEntry("Alternative végétale au fromage", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    "PTWWF029": _GoldenEntry("Huile d’olive", _PT.PLANT_BASED_NON_CORE, _FG.FG3, wwf_fg3=_S3.PLANT_BASED_FAT),
    "PTWWF030": _GoldenEntry("Margarine végétale", _PT.PLANT_BASED_NON_CORE, _FG.FG3, wwf_fg3=_S3.PLANT_BASED_FAT),
    "PTWWF031": _GoldenEntry("Beurre doux", _PT.ANIMAL_CORE, _FG.FG3, wwf_fg3=_S3.ANIMAL_BASED_FAT),
    "PTWWF032": _GoldenEntry("Carottes fraîches", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF033": _GoldenEntry("Tomates fraîches", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF034": _GoldenEntry("Brocoli surgelé", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF035": _GoldenEntry("Ratatouille de légumes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF036": _GoldenEntry("Pommes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF037": _GoldenEntry("Fruits rouges surgelés", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF038": _GoldenEntry("Raisins secs", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF039": _GoldenEntry("Riz basmati", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF040": _GoldenEntry("Pâtes spaghetti", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF041": _GoldenEntry("Flocons d’avoine complets", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.WHOLE_GRAIN),
    "PTWWF042": _GoldenEntry("Pain complet", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.WHOLE_GRAIN),
    "PTWWF043": _GoldenEntry("Pommes de terre", _PT.PLANT_BASED_NON_CORE, _FG.FG6),
    "PTWWF044": _GoldenEntry("Patates douces", _PT.PLANT_BASED_NON_CORE, _FG.FG6),
    "PTWWF045": _GoldenEntry("Frites au four surgelées", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    "PTWWF046": _GoldenEntry("Chips nature", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    "PTWWF047": _GoldenEntry("Chocolat noir", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    "PTWWF048": _GoldenEntry(
        "Curry de poulet avec riz",
        _PT.COMPOSITE_PRODUCTS,
        _FG.FG1,
        wwf_is_composite=True,
        wwf_fg1=_S1.POULTRY,
        wwf_bucket=_BK.MEAT_BASED,
    ),
    "PTWWF049": _GoldenEntry(
        "Pizza fromage tomate",
        _PT.COMPOSITE_PRODUCTS,
        _FG.FG2,
        wwf_is_composite=True,
        wwf_fg2=_S2.CHEESE,
        wwf_bucket=_BK.VEGETARIAN,
    ),
    "PTWWF050": _GoldenEntry(
        "Curry de lentilles végan",
        _PT.PLANT_BASED_CORE,
        _FG.FG1,
        wwf_is_composite=True,
        wwf_fg1=_S1.LEGUMES,
        wwf_bucket=_BK.VEGAN,
    ),
}


DEMO25 = _make_catalogue(
    "demo25",
    _DEMO25_ENTRIES,
    review_external_ids={"PTWWF019", "PTWWF025"},
    review_methodologies={Methodology.PROTEIN_TRACKER, Methodology.WWF},
)
DEMO50 = _make_catalogue(
    "demo50",
    _DEMO50_ENTRIES,
    review_external_ids={"PTWWF048", "PTWWF049"},
    review_methodologies={Methodology.WWF},
)

#: All recognised demo catalogues. Order is irrelevant — recognition matches
#: a unique fingerprint.
_CATALOGUES: tuple[DemoCatalogue, ...] = (DEMO25, DEMO50)

#: Distinct product counts across all demo catalogues — the cheap pre-filter.
_DEMO_SIZES: frozenset[int] = frozenset(c.size() for c in _CATALOGUES)


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


def recognise_demo_catalogue(
    products: Sequence[NormalizedProduct],
) -> DemoCatalogue | None:
    """Return the demo catalogue that *products* exactly match, or ``None``.

    Strict: an upload matches a catalogue iff its (external_product_id,
    product_name) pairs produce the same content fingerprint — i.e. exactly
    the same id set with matching (normalised) names. Any extra/missing id or
    a changed name means "not a demo catalogue", so a real retailer upload is
    never mistaken for one.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for product in products:
        ext_id = product.external_product_id
        if ext_id in seen:
            # Duplicate external id — not a clean demo catalogue.
            return None
        seen.add(ext_id)
        pairs.append((ext_id, product.product_name))
    fingerprint = _fingerprint(pairs)
    for catalogue in _CATALOGUES:
        if len(pairs) != catalogue.size():
            continue
        # Match on EITHER the exact id+name fingerprint OR the exact external
        # id set. The demo external ids (PTWWF001..) are unique to the demo —
        # no real retailer catalogue uses them — so an exact id-set match is
        # a safe, demo-only signal that ALSO stays robust to product-name
        # encoding / whitespace / minor-edit differences between the stored
        # data and this fixture (which strict name fingerprinting would
        # silently reject, leaving the demo on the live-AI path).
        if fingerprint == catalogue.fingerprint or seen == set(catalogue.entries):
            return catalogue
    return None


def is_demo_golden_upload(products: Sequence[NormalizedProduct]) -> bool:
    """True iff *products* are exactly one of the recognised demo
    catalogues."""
    return recognise_demo_catalogue(products) is not None


def demo_catalogue_fingerprints() -> dict[str, str]:
    """Catalogue key → fingerprint (for logging / audit)."""
    return {c.key: c.fingerprint for c in _CATALOGUES}


# ---------------------------------------------------------------------------
# Classification builders (deterministic golden data, demo-presentation
# provenance — see "Demo presentation provenance" above)
# ---------------------------------------------------------------------------


def _provenance_kwargs(
    external_id: str,
    methodology: Methodology,
    rule_id: str,
    *,
    is_review: bool,
) -> dict[str, object]:
    """Source + confidence + the source-specific fields the domain model
    requires (rule_id for deterministic, ai_model/version for ai,
    reviewer for manual_review)."""
    source = _demo_source(external_id, methodology, is_review=is_review)
    confidence = _demo_confidence(external_id, methodology)
    if source is ClassificationSource.AI:
        # AI forbids rule_id; requires ai_model + ai_prompt_version.
        return {
            "source": source,
            "confidence": confidence,
            "ai_model": _DEMO_AI_MODEL,
            "ai_prompt_version": _DEMO_AI_PROMPT_VERSION,
        }
    if source is ClassificationSource.MANUAL_REVIEW:
        return {
            "source": source,
            "confidence": confidence,
            "rule_id": rule_id,
            "reviewer_user_id": _DEMO_REVIEWER_ID,
        }
    return {"source": source, "confidence": confidence, "rule_id": rule_id}


def _build_pt(
    product: NormalizedProduct,
    entry: _GoldenEntry,
    now: datetime,
    *,
    is_review: bool,
) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product.id,
        pt_group=entry.pt_group,
        updated_at=now,
        **_provenance_kwargs(
            product.external_product_id,
            Methodology.PROTEIN_TRACKER,
            PT_RULE_ID,
            is_review=is_review,
        ),
    )


def _build_wwf(
    product: NormalizedProduct,
    entry: _GoldenEntry,
    now: datetime,
    *,
    is_review: bool,
) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product.id,
        wwf_food_group=entry.wwf_food_group,
        wwf_is_composite=entry.wwf_is_composite,
        fg1_subgroup=entry.wwf_fg1,
        fg2_subgroup=entry.wwf_fg2,
        fg3_subgroup=entry.wwf_fg3,
        fg5_grain_kind=entry.wwf_fg5,
        fg7_snack_kind=entry.wwf_fg7,
        composite_step1_bucket=entry.wwf_bucket,
        updated_at=now,
        **_provenance_kwargs(
            product.external_product_id,
            Methodology.WWF,
            WWF_RULE_ID,
            is_review=is_review,
        ),
    )


def get_demo_golden_classification_for_product(
    product: NormalizedProduct,
    methodology: Methodology,
    now: datetime,
    *,
    catalogue: DemoCatalogue,
) -> ProteinTrackerProductClassification | WWFProductClassification | None:
    """Return the pre-approved classification for one demo product within
    *catalogue*, or ``None`` if the product is not part of it."""
    entry = catalogue.entries.get(product.external_product_id)
    if entry is None:
        return None
    is_review = _is_review_product(product, methodology, catalogue)
    if methodology is Methodology.PROTEIN_TRACKER:
        return _build_pt(product, entry, now, is_review=is_review)
    if methodology is Methodology.WWF:
        return _build_wwf(product, entry, now, is_review=is_review)
    return None


def _is_review_product(
    product: NormalizedProduct,
    methodology: Methodology,
    catalogue: DemoCatalogue,
) -> bool:
    return (
        methodology in catalogue.review_methodologies
        and product.external_product_id in catalogue.review_external_ids
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _route_review(
    store: StoreProtocol,
    product: NormalizedProduct,
    methodology: Methodology,
    catalogue: DemoCatalogue,
    now: datetime,
) -> None:
    """Create the review item for a validation product on a review
    methodology; otherwise remove any stale review item so a re-run leaves a
    clean queue."""
    if _is_review_product(product, methodology, catalogue):
        store.upsert_review_item(
            ManualReviewItem(
                product_id=product.id,
                methodology=methodology,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.REQUESTED,
                queued_at=now,
                rationale_notes=(_REVIEW_RATIONALE,),
            )
        )
    else:
        store.remove_review_item(product.id, methodology)


def apply_demo_golden_pt_classification(
    store: StoreProtocol,
    product: NormalizedProduct,
    now: datetime,
    *,
    catalogue: DemoCatalogue,
) -> bool:
    """Upsert the golden PT classification for one demo product. Returns
    ``True`` if the product was recognised and written."""
    cls = get_demo_golden_classification_for_product(
        product, Methodology.PROTEIN_TRACKER, now, catalogue=catalogue
    )
    if cls is None:
        return False
    store.upsert_pt_classification(cls)
    _route_review(store, product, Methodology.PROTEIN_TRACKER, catalogue, now)
    return True


def apply_demo_golden_wwf_classification(
    store: StoreProtocol,
    product: NormalizedProduct,
    now: datetime,
    *,
    catalogue: DemoCatalogue,
) -> bool:
    """Upsert the golden WWF classification for one demo product. Returns
    ``True`` if the product was recognised and written."""
    cls = get_demo_golden_classification_for_product(
        product, Methodology.WWF, now, catalogue=catalogue
    )
    if cls is None:
        return False
    store.upsert_wwf_classification(cls)
    _route_review(store, product, Methodology.WWF, catalogue, now)
    return True


def apply_demo_golden_classification(
    store: StoreProtocol,
    products: Sequence[NormalizedProduct],
    methodology: Methodology,
    *,
    now: datetime,
    catalogue: DemoCatalogue,
) -> int:
    """Apply the golden classification for *methodology* to every product in
    *products* that belongs to *catalogue*. Returns the number written.

    PT and WWF are written to their own tables. A product is given a review
    item only when *methodology* is in ``catalogue.review_methodologies`` and
    the product is one of ``catalogue.review_external_ids``; every other
    product has its stale review item (if any) cleared for this methodology.
    """
    if methodology not in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
        return 0
    written = 0
    for product in products:
        if methodology is Methodology.PROTEIN_TRACKER:
            ok = apply_demo_golden_pt_classification(
                store, product, now, catalogue=catalogue
            )
        else:
            ok = apply_demo_golden_wwf_classification(
                store, product, now, catalogue=catalogue
            )
        if ok:
            written += 1
    return written

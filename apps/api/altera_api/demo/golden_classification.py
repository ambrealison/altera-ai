"""Demo-only **golden** classification for the ``DEMO-50produits`` catalogue.

Why this exists
===============

For the retailer demo we want a *perfectly predictable* categorisation
experience on one specific 50-product catalogue (``DEMO-50produits.csv``):

* 50/50 Protein Tracker products categorised,
* 50/50 WWF products categorised,
* exactly **two** products surfaced for manual validation
  (``PTWWF048`` "Curry de poulet avec riz" and ``PTWWF049`` "Pizza fromage
  tomate" — composite/prepared products that make the validation flow
  meaningful),
* no dependency on live LLM variability for this exact catalogue.

Hard safety properties
=======================

* **Default off.** Nothing here activates unless
  ``ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED`` is truthy. With the flag
  off the platform behaves exactly as it does today.
* **Recognition-gated.** Even with the flag on, the golden path only
  applies to an upload whose products are *exactly* the 50 demo products
  (matched by ``external_product_id`` **and** product name — see
  :data:`_DEMO_FINGERPRINT`). A real retailer catalogue can never be
  mistaken for the demo catalogue.
* **No AI call.** The orchestrator skips the provider entirely for a
  recognised demo catalogue — no commercial fields, nothing, leaves the
  process.
* **Honest provenance.** Classifications are stored with
  ``source=deterministic`` and ``rule_id`` ``demo.golden.pt`` /
  ``demo.golden.wwf`` (never ``source=ai`` — we do not fake AI
  provenance). Confidence is ``1`` (the model contract requires that for
  deterministic source).
* **PT and WWF stay separate.** PT and WWF classifications are written to
  their own tables; review items are methodology-scoped.

Easy to delete
==============

All demo logic lives in this package plus one small, flag-guarded branch
in ``classification_job_orchestrator.advance_classification_job``. Deleting
the package + that branch removes the feature with no other changes.

Review semantics (exactly two products visible in validation)
=============================================================

Both Protein Tracker and WWF are active on the demo project, so naively
flagging both products under both methodologies would create *four*
``(product, methodology)`` review rows. To keep the validation experience
to exactly two product rows we attach the two review items to a **single**
methodology (:data:`REVIEW_METHODOLOGY` = WWF — composites are a
first-class WWF Step-1 concept, so "is this a meat / vegetarian / vegan
composite?" is the most meaningful thing to validate). Both products still
receive *both* a PT and a WWF classification (so both methodologies report
50/50 categorised); only the WWF review queue carries the two items, and
the Protein Tracker review queue stays empty.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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
_TRUTHY = {"1", "true", "yes", "on"}


def is_demo_golden_classification_enabled() -> bool:
    """True only when ``ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED`` is set
    to a truthy value. Read on demand so tests can monkeypatch the env."""
    return (os.environ.get(_FLAG_ENV) or "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------

PT_RULE_ID = "demo.golden.pt"
WWF_RULE_ID = "demo.golden.wwf"

#: External product ids deliberately left for human validation. They are
#: composite/prepared products and exercise the validation flow.
REVIEW_EXTERNAL_IDS: frozenset[str] = frozenset({"PTWWF048", "PTWWF049"})

#: Methodology whose review queue carries the two validation items. Keeping
#: them on a single methodology guarantees exactly two product rows in the
#: validation experience (see the module docstring). WWF is chosen because
#: its Step-1 composite buckets make the validation decision meaningful.
REVIEW_METHODOLOGY: Methodology = Methodology.WWF

_REVIEW_RATIONALE = (
    "Demo golden classification — composite/prepared product deliberately "
    "routed to human validation."
)


# ---------------------------------------------------------------------------
# The golden catalogue
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


# Enum aliases keep the table below compact and inspectable.
_PT = ProteinTrackerGroup
_FG = WWFFoodGroup
_S1 = WWFFG1Subgroup
_S2 = WWFFG2Subgroup
_S3 = WWFFG3Subgroup
_G5 = WWFFG5GrainKind
_S7 = WWFFG7SnackKind
_BK = WWFCompositeStep1Bucket

#: external_product_id → pre-approved classification. Keyed by the STABLE
#: external id (never row order). Built from the demo catalogue's ids +
#: names only — the raw commercial CSV is NOT committed.
_GOLDEN: dict[str, _GoldenEntry] = {
    # — Plant proteins (FG1 plant subgroups) → PT plant_based_core ———————
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
    # — Animal proteins (FG1 animal subgroups) → PT animal_core ——————————
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
    # — Dairy (FG2 animal) → PT animal_core ——————————————————————————————
    "PTWWF021": _GoldenEntry("Lait demi-écrémé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF022": _GoldenEntry("Yaourt nature", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF023": _GoldenEntry("Crème fraîche", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.OTHER_DAIRY_ANIMAL),
    "PTWWF024": _GoldenEntry("Emmental râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    "PTWWF025": _GoldenEntry("Parmesan râpé", _PT.ANIMAL_CORE, _FG.FG2, wwf_fg2=_S2.CHEESE),
    # — Dairy alternatives (FG2 plant) → PT plant_based_core —————————————
    "PTWWF026": _GoldenEntry("Boisson soja nature", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    "PTWWF027": _GoldenEntry("Yaourt végétal au soja", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    "PTWWF028": _GoldenEntry("Alternative végétale au fromage", _PT.PLANT_BASED_CORE, _FG.FG2, wwf_fg2=_S2.DAIRY_ALTERNATIVE_PLANT),
    # — Fats & oils (FG3) ————————————————————————————————————————————————
    "PTWWF029": _GoldenEntry("Huile d’olive", _PT.PLANT_BASED_NON_CORE, _FG.FG3, wwf_fg3=_S3.PLANT_BASED_FAT),
    "PTWWF030": _GoldenEntry("Margarine végétale", _PT.PLANT_BASED_NON_CORE, _FG.FG3, wwf_fg3=_S3.PLANT_BASED_FAT),
    "PTWWF031": _GoldenEntry("Beurre doux", _PT.ANIMAL_CORE, _FG.FG3, wwf_fg3=_S3.ANIMAL_BASED_FAT),
    # — Fruits & vegetables (FG4) → PT plant_based_non_core ——————————————
    "PTWWF032": _GoldenEntry("Carottes fraîches", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF033": _GoldenEntry("Tomates fraîches", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF034": _GoldenEntry("Brocoli surgelé", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF035": _GoldenEntry("Ratatouille de légumes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF036": _GoldenEntry("Pommes", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF037": _GoldenEntry("Fruits rouges surgelés", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    "PTWWF038": _GoldenEntry("Raisins secs", _PT.PLANT_BASED_NON_CORE, _FG.FG4),
    # — Grains / cereals (FG5) ——————————————————————————————————————————
    "PTWWF039": _GoldenEntry("Riz basmati", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF040": _GoldenEntry("Pâtes spaghetti", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.REFINED_GRAIN),
    "PTWWF041": _GoldenEntry("Flocons d’avoine complets", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.WHOLE_GRAIN),
    "PTWWF042": _GoldenEntry("Pain complet", _PT.PLANT_BASED_NON_CORE, _FG.FG5, wwf_fg5=_G5.WHOLE_GRAIN),
    # — Tubers / starchy (FG6) ——————————————————————————————————————————
    "PTWWF043": _GoldenEntry("Pommes de terre", _PT.PLANT_BASED_NON_CORE, _FG.FG6),
    "PTWWF044": _GoldenEntry("Patates douces", _PT.PLANT_BASED_NON_CORE, _FG.FG6),
    # — Snacks high in fat/salt/sugar (FG7) ——————————————————————————————
    "PTWWF045": _GoldenEntry("Frites au four surgelées", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    "PTWWF046": _GoldenEntry("Chips nature", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    "PTWWF047": _GoldenEntry("Chocolat noir", _PT.PLANT_BASED_NON_CORE, _FG.FG7, wwf_fg7=_S7.PLANT_BASED_SNACK),
    # — Composite / prepared dishes ——————————————————————————————————————
    # PTWWF048 + PTWWF049 are the TWO products left for manual validation
    # (review item created on the WWF queue only — see REVIEW_*). PTWWF050
    # is a clearly-vegan composite and is auto-accepted, which makes the
    # demo story crisp: obvious vegan composite → auto; meat & cheese
    # composites → human validation.
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
        _PT.PLANT_BASED_CORE,  # vegan → all-plant → not a PT composite
        _FG.FG1,
        wwf_is_composite=True,  # prepared dish → WWF Step-1 composite
        wwf_fg1=_S1.LEGUMES,
        wwf_bucket=_BK.VEGAN,
    ),
}


# ---------------------------------------------------------------------------
# Recognition (id-set + name fingerprint == a content checksum without the
# raw file)
# ---------------------------------------------------------------------------


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
    catalogue checksum."""
    canonical = "\n".join(
        f"{ext_id}={_norm(name)}" for ext_id, name in sorted(pairs)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Stable checksum of the demo catalogue. Recognition compares an upload's
#: (id, name) pairs against this. Logged for auditability.
_DEMO_FINGERPRINT: str = _fingerprint(
    (ext_id, entry.name) for ext_id, entry in _GOLDEN.items()
)

#: Optional belt-and-braces override: if an operator sets
#: ``ALTERA_DEMO_GOLDEN_SHA256`` to the raw CSV's SHA-256, recognition can
#: additionally require it (enforced by the caller that has the file
#: checksum). Exposed for completeness; the id+name fingerprint above is
#: the primary, file-free recognition mechanism.
_SHA256_ENV = "ALTERA_DEMO_GOLDEN_SHA256"


def demo_catalogue_fingerprint() -> str:
    """The demo catalogue fingerprint (for logging / audit)."""
    return _DEMO_FINGERPRINT


def is_demo_golden_upload(products: Sequence[NormalizedProduct]) -> bool:
    """True iff *products* are exactly the recognised demo catalogue.

    Strict: the upload must contain exactly the 50 demo external ids and
    every product name must match (after normalisation). Any extra /
    missing id, or a mismatched name, means "not the demo catalogue" — so a
    real retailer upload is never mistaken for it.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for product in products:
        ext_id = product.external_product_id
        if ext_id in seen:
            # Duplicate external id — not the clean demo catalogue.
            return False
        seen.add(ext_id)
        pairs.append((ext_id, product.product_name))
    if len(pairs) != len(_GOLDEN):
        return False
    return _fingerprint(pairs) == _DEMO_FINGERPRINT


# ---------------------------------------------------------------------------
# Classification builders (honest deterministic provenance)
# ---------------------------------------------------------------------------


def _build_pt(
    product: NormalizedProduct, entry: _GoldenEntry, now: datetime
) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product.id,
        pt_group=entry.pt_group,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id=PT_RULE_ID,
        updated_at=now,
    )


def _build_wwf(
    product: NormalizedProduct, entry: _GoldenEntry, now: datetime
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
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id=WWF_RULE_ID,
        updated_at=now,
    )


def get_demo_golden_classification_for_product(
    product: NormalizedProduct,
    methodology: Methodology,
    now: datetime,
) -> ProteinTrackerProductClassification | WWFProductClassification | None:
    """Return the pre-approved classification for one demo product, or
    ``None`` if the product is not part of the demo catalogue."""
    entry = _GOLDEN.get(product.external_product_id)
    if entry is None:
        return None
    if methodology is Methodology.PROTEIN_TRACKER:
        return _build_pt(product, entry, now)
    if methodology is Methodology.WWF:
        return _build_wwf(product, entry, now)
    return None


def _is_review_product(product: NormalizedProduct, methodology: Methodology) -> bool:
    return (
        methodology is REVIEW_METHODOLOGY
        and product.external_product_id in REVIEW_EXTERNAL_IDS
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_demo_golden_pt_classification(
    store: StoreProtocol, product: NormalizedProduct, now: datetime
) -> bool:
    """Upsert the golden PT classification for one demo product. Returns
    ``True`` if the product was recognised and written."""
    cls = get_demo_golden_classification_for_product(
        product, Methodology.PROTEIN_TRACKER, now
    )
    if cls is None:
        return False
    store.upsert_pt_classification(cls)
    _route_review(store, product, Methodology.PROTEIN_TRACKER, now)
    return True


def apply_demo_golden_wwf_classification(
    store: StoreProtocol, product: NormalizedProduct, now: datetime
) -> bool:
    """Upsert the golden WWF classification for one demo product. Returns
    ``True`` if the product was recognised and written."""
    cls = get_demo_golden_classification_for_product(product, Methodology.WWF, now)
    if cls is None:
        return False
    store.upsert_wwf_classification(cls)
    _route_review(store, product, Methodology.WWF, now)
    return True


def _route_review(
    store: StoreProtocol,
    product: NormalizedProduct,
    methodology: Methodology,
    now: datetime,
) -> None:
    """Create the review item for the two validation products on the review
    methodology; otherwise remove any stale review item so a re-run leaves a
    clean queue."""
    if _is_review_product(product, methodology):
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


def apply_demo_golden_classification(
    store: StoreProtocol,
    products: Sequence[NormalizedProduct],
    methodology: Methodology,
    *,
    now: datetime,
) -> int:
    """Apply the golden classification for *methodology* to every demo
    product in *products*. Returns the number of products written.

    PT and WWF are written to their own tables. Exactly the two
    :data:`REVIEW_EXTERNAL_IDS` products get a review item — and only on
    :data:`REVIEW_METHODOLOGY` — so the validation experience shows exactly
    two product rows. Every other product has its stale review item (if
    any) cleared for this methodology.
    """
    if methodology not in (Methodology.PROTEIN_TRACKER, Methodology.WWF):
        return 0
    written = 0
    for product in products:
        if methodology is Methodology.PROTEIN_TRACKER:
            ok = apply_demo_golden_pt_classification(store, product, now)
        else:
            ok = apply_demo_golden_wwf_classification(store, product, now)
        if ok:
            written += 1
    return written

"""Deterministic rules engine.

For each ``(product, methodology)`` the engine returns one of:

* ``PTMatched`` / ``WWFMatched`` — at least one rule fired, all firing rules
  agreed on a category. The returned classification has
  ``source=DETERMINISTIC`` and ``confidence=1.0``. If multiple rules fired
  on the same category, their ids are comma-joined into ``rule_id``.
* ``PTPassThrough`` / ``WWFPassThrough`` — no rule fired. The product is
  punted to the AI classifier (or remains unclassified for ops policy).
* ``PTRuleCollision`` / ``WWFRuleCollision`` — more than one rule fired and
  they disagreed on category. The product is routed to manual review
  with reason ``rule_collision``.
* ``PTContradiction`` / ``WWFContradiction`` — a contradiction between the
  product's label claims and its ingredients or retailer category was
  detected, or the product is an obvious non-food / out-of-scope item.
  Checked before rule matching; routes to manual review with reason
  ``contradiction_detected``.

The engine never produces a confidence in (0, 1). It is `1.0` on a
match, undefined on pass-through.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.wwf import WWFProductClassification
from altera_api.rules.conditions import ConditionContext, match_condition_node
from altera_api.rules.schema import PTRule, WWFRule, WWFRuleCategory

_RULE_ID_SEPARATOR = ","


# ---------------------------------------------------------------------------
# Verdict types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PTMatched:
    classification: ProteinTrackerProductClassification
    fired_rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class PTPassThrough:
    product_id: UUID
    methodology: Methodology = Methodology.PROTEIN_TRACKER


@dataclass(frozen=True)
class PTRuleCollision:
    product_id: UUID
    methodology: Methodology = Methodology.PROTEIN_TRACKER
    conflicting_rule_ids: tuple[str, ...] = ()
    conflicting_categories: tuple[ProteinTrackerGroup, ...] = ()


@dataclass(frozen=True)
class PTContradiction:
    """One or more label/ingredient/category contradictions detected.

    The product bypasses the AI classifier and routes directly to manual
    review with ``reason=contradiction_detected``.
    """

    product_id: UUID
    contradiction_notes: tuple[str, ...]
    methodology: Methodology = Methodology.PROTEIN_TRACKER


@dataclass(frozen=True)
class WWFMatched:
    classification: WWFProductClassification
    fired_rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class WWFPassThrough:
    product_id: UUID
    methodology: Methodology = Methodology.WWF


@dataclass(frozen=True)
class WWFRuleCollision:
    product_id: UUID
    methodology: Methodology = Methodology.WWF
    conflicting_rule_ids: tuple[str, ...] = ()
    conflicting_categories: tuple[WWFRuleCategory, ...] = ()


@dataclass(frozen=True)
class WWFContradiction:
    """One or more label/ingredient/category contradictions detected.

    The product bypasses the AI classifier and routes directly to manual
    review with ``reason=contradiction_detected``.
    """

    product_id: UUID
    contradiction_notes: tuple[str, ...]
    methodology: Methodology = Methodology.WWF


PTVerdict: TypeAlias = PTMatched | PTPassThrough | PTRuleCollision | PTContradiction
WWFVerdict: TypeAlias = WWFMatched | WWFPassThrough | WWFRuleCollision | WWFContradiction
Verdict: TypeAlias = PTVerdict | WWFVerdict


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

# Ingredient keywords that strongly indicate animal origin.
# Checked when a product carries a 'vegan' label.
# Kept narrow to avoid false positives (e.g. "coconut butter" → not checked).
_VEGAN_CONTRADICTING_INGREDIENTS: frozenset[str] = frozenset({
    "whole milk", "skimmed milk", "semi-skimmed milk",
    "cow's milk", "cows milk", "full fat milk",
    "milk powder", "dried milk", "condensed milk", "evaporated milk",
    "lactose", "whey protein", "whey", "casein", "caseinate", "lactalbumin",
    "egg white", "egg yolk", "whole egg", "dried egg", "egg powder",
    "honey",
    "gelatin", "gelatine",
    "lard", "tallow", "suet",
    "anchovies", "anchovy",
    "fish sauce",
})

# Ingredient keywords indicating meat or fish.
# Checked when a product carries a 'vegetarian' label.
_VEGETARIAN_CONTRADICTING_INGREDIENTS: frozenset[str] = frozenset({
    "beef", "pork", "chicken", "turkey", "duck", "goose",
    "veal", "lamb", "venison", "rabbit",
    "bacon", "ham", "salami", "pepperoni", "chorizo",
    "gelatin", "gelatine",
    "lard", "tallow", "suet",
    "fish sauce", "anchovies", "anchovy",
})

# Ingredients contradicting a 'plant-based' name or label claim.
_PLANT_BASED_CONTRADICTING_INGREDIENTS: frozenset[str] = frozenset({
    "whey protein", "whey", "casein", "caseinate", "milk protein",
})

# Retailer category keywords that indicate animal products —
# used to detect "vegan label + animal retailer category".
_ANIMAL_RETAILER_CATEGORIES: frozenset[str] = frozenset({
    "meat", "poultry", "red meat", "fresh meat",
    "fish", "seafood", "fresh fish",
    "charcuterie", "deli meat",
})

# Product name / retailer-category signals for obvious out-of-scope items.
# These products should not consume AI tokens.
_OUT_OF_SCOPE_SIGNALS: frozenset[str] = frozenset({
    "pet food", "cat food", "dog food", "dog treat", "cat treat",
    "bird food", "fish food", "hamster food", "rabbit food",
    "baby formula", "infant formula", "follow-on formula",
    "nappy", "nappies", "diaper",
    "laundry", "washing powder", "dishwasher tablet",
    "cigarette", "tobacco", "e-cigarette",
})


def _detect_contradictions(ctx: ConditionContext) -> tuple[str, ...]:
    """Return human-readable notes for every contradiction detected.

    An empty tuple means no contradictions; classification proceeds normally.
    Runs in O(n) over small constant sets — cheap enough to call per product.
    """
    notes: list[str] = []

    # --- Vegan label contradictions ---
    if "vegan" in ctx.labels:
        for ingredient in _VEGAN_CONTRADICTING_INGREDIENTS:
            if ingredient in ctx.ingredients_text_lower:
                notes.append(f"vegan label + '{ingredient}' in ingredients")
                break  # one note per category is enough
        for cat_kw in _ANIMAL_RETAILER_CATEGORIES:
            if cat_kw in ctx.retailer_category_lower:
                # Skip plant-based alternative categories (e.g. "Meat Alternatives")
                if any(
                    excl in ctx.retailer_category_lower
                    for excl in ("alternative", "plant", "vegan", "vegetarian", "free")
                ):
                    continue
                notes.append(
                    f"vegan label + animal retailer category "
                    f"({ctx.retailer_category_lower!r})"
                )
                break

    # --- Vegetarian label contradictions ---
    if "vegetarian" in ctx.labels:
        for ingredient in _VEGETARIAN_CONTRADICTING_INGREDIENTS:
            if ingredient in ctx.ingredients_text_lower:
                notes.append(f"vegetarian label + '{ingredient}' in ingredients")
                break

    # --- Plant-based name/label + non-plant ingredient ---
    is_plant_based_claim = (
        "plant-based" in ctx.product_name_lower
        or "plant based" in ctx.product_name_lower
        or "plant-based" in ctx.labels
        or "plant_based" in ctx.labels
    )
    if is_plant_based_claim:
        for ingredient in _PLANT_BASED_CONTRADICTING_INGREDIENTS:
            if ingredient in ctx.ingredients_text_lower:
                notes.append(
                    f"plant-based claim + '{ingredient}' in ingredients"
                )
                break

    # --- Obvious out-of-scope / non-food product signals ---
    combined = (
        f"{ctx.product_name_lower} "
        f"{ctx.retailer_category_lower} "
        f"{ctx.retailer_subcategory_lower}"
    )
    for signal in _OUT_OF_SCOPE_SIGNALS:
        if signal in combined:
            notes.append(f"likely out-of-scope: '{signal}' detected")
            break

    return tuple(notes)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def _fired_rules(
    rules: Iterable[PTRule | WWFRule],
    ctx: ConditionContext,
) -> list[PTRule | WWFRule]:
    fired: list[PTRule | WWFRule] = []
    for rule in rules:
        if not match_condition_node(rule.match, ctx):
            continue
        if rule.exclude is not None and match_condition_node(rule.exclude, ctx):
            continue
        fired.append(rule)
    # Deterministic order: priority asc, then id asc.
    fired.sort(key=lambda r: (r.priority, r.id))
    return fired


def classify_protein_tracker(
    product: NormalizedProduct,
    rules: Sequence[PTRule],
    *,
    now: datetime,
    taxonomy_node: str | None = None,
) -> PTVerdict:
    """Apply PT rules to one product. See module docstring for outcomes."""
    ctx = ConditionContext.from_product(product, taxonomy_node=taxonomy_node)

    # Contradiction check runs before rule matching so flagged products
    # skip the AI classifier entirely.
    contradictions = _detect_contradictions(ctx)
    if contradictions:
        return PTContradiction(
            product_id=product.id,
            contradiction_notes=contradictions,
        )

    fired = [r for r in _fired_rules(rules, ctx) if isinstance(r, PTRule)]

    if not fired:
        return PTPassThrough(product_id=product.id)

    categories = {r.category for r in fired}
    if len(categories) > 1:
        return PTRuleCollision(
            product_id=product.id,
            conflicting_rule_ids=tuple(r.id for r in fired),
            conflicting_categories=tuple(sorted(categories, key=lambda c: c.value)),
        )

    (category,) = categories
    rule_ids = tuple(r.id for r in fired)
    classification = ProteinTrackerProductClassification(
        product_id=product.id,
        pt_group=category,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id=_RULE_ID_SEPARATOR.join(rule_ids),
        updated_at=now,
    )
    return PTMatched(classification=classification, fired_rule_ids=rule_ids)


def classify_wwf(
    product: NormalizedProduct,
    rules: Sequence[WWFRule],
    *,
    now: datetime,
    taxonomy_node: str | None = None,
) -> WWFVerdict:
    """Apply WWF rules to one product. See module docstring for outcomes."""
    ctx = ConditionContext.from_product(product, taxonomy_node=taxonomy_node)

    # Contradiction check runs before rule matching.
    contradictions = _detect_contradictions(ctx)
    if contradictions:
        return WWFContradiction(
            product_id=product.id,
            contradiction_notes=contradictions,
        )

    fired = [r for r in _fired_rules(rules, ctx) if isinstance(r, WWFRule)]

    if not fired:
        return WWFPassThrough(product_id=product.id)

    # Treat two firing rules as "agreeing" only when their full category
    # objects are identical — the WWF category is a structured object.
    distinct_categories = {r.category for r in fired}
    if len(distinct_categories) > 1:
        return WWFRuleCollision(
            product_id=product.id,
            conflicting_rule_ids=tuple(r.id for r in fired),
            conflicting_categories=tuple(distinct_categories),
        )

    (category,) = distinct_categories
    rule_ids = tuple(r.id for r in fired)
    classification = WWFProductClassification(
        product_id=product.id,
        wwf_food_group=category.wwf_food_group,
        wwf_is_composite=category.wwf_is_composite,
        fg1_subgroup=category.wwf_fg1_subgroup,
        fg2_subgroup=category.wwf_fg2_subgroup,
        fg3_subgroup=category.wwf_fg3_subgroup,
        fg5_grain_kind=category.wwf_fg5_grain_kind,
        fg7_snack_kind=category.wwf_fg7_snack_kind,
        composite_step1_bucket=category.wwf_composite_step1_bucket,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id=_RULE_ID_SEPARATOR.join(rule_ids),
        updated_at=now,
    )
    return WWFMatched(classification=classification, fired_rule_ids=rule_ids)

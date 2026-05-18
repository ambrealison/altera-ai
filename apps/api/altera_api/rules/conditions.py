"""Condition evaluation.

Pure functions over a ``ConditionContext`` — easy to unit test, no
hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass

from altera_api.domain.product import NormalizedProduct
from altera_api.rules.schema import ConditionNode


@dataclass(frozen=True)
class ConditionContext:
    """Pre-extracted, lowercased product attributes for matching.

    Keeping this separate from ``NormalizedProduct`` means the engine
    can apply rules against arbitrary inputs in tests, and avoids
    repeatedly lowercasing strings inside a hot inner loop.
    """

    product_name_lower: str
    brand_lower: str
    retailer_category_lower: str
    retailer_subcategory_lower: str
    ingredients_text_lower: str
    labels: frozenset[str]
    language: str | None
    country: str | None
    taxonomy_node: str | None

    @classmethod
    def from_product(
        cls,
        product: NormalizedProduct,
        *,
        taxonomy_node: str | None = None,
    ) -> ConditionContext:
        return cls(
            product_name_lower=product.product_name.lower(),
            brand_lower=(product.brand or "").lower(),
            retailer_category_lower=(product.retailer_category or "").lower(),
            retailer_subcategory_lower=(product.retailer_subcategory or "").lower(),
            ingredients_text_lower=(product.ingredients_text or "").lower(),
            labels=frozenset(label.lower() for label in product.labels),
            language=product.language,
            country=product.country,
            taxonomy_node=taxonomy_node,
        )


def _name_or_category_contains(needle_lower: str, ctx: ConditionContext) -> bool:
    """`product_name_contains` is a substring match against the product
    name *and* its retailer category / subcategory tags, which are the
    only category signals available before taxonomy resolution."""
    return (
        needle_lower in ctx.product_name_lower
        or needle_lower in ctx.retailer_category_lower
        or needle_lower in ctx.retailer_subcategory_lower
    )


def match_condition_node(node: ConditionNode, ctx: ConditionContext) -> bool:
    """Evaluate one condition node against a context.

    Leaves are checked directly; groups recurse. A group with no
    children is treated as **false** (defensive — the schema rejects an
    empty group at load time, but this keeps the matcher total).
    """
    # Group forms first
    if node.any_of is not None:
        return any(match_condition_node(child, ctx) for child in node.any_of)
    if node.all_of is not None:
        children = node.all_of
        if not children:
            return False
        return all(match_condition_node(child, ctx) for child in children)

    # Leaf forms
    if node.product_name_contains is not None:
        return any(
            _name_or_category_contains(needle.lower(), ctx) for needle in node.product_name_contains
        )
    if node.ingredients_contains is not None:
        return any(
            needle.lower() in ctx.ingredients_text_lower for needle in node.ingredients_contains
        )
    if node.brand_in is not None:
        return any(b.lower() == ctx.brand_lower for b in node.brand_in)
    if node.labels_contains is not None:
        return any(label.lower() in ctx.labels for label in node.labels_contains)
    if node.language_in is not None:
        return ctx.language is not None and ctx.language in node.language_in
    if node.country_in is not None:
        return ctx.country is not None and ctx.country in node.country_in
    if node.taxonomy_node is not None:
        # MVP: exact-match only. Descendant resolution will arrive when
        # the canonical tree is loaded; see docs/data/taxonomy.md.
        return ctx.taxonomy_node == node.taxonomy_node

    return False

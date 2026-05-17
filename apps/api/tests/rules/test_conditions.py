from __future__ import annotations

from altera_api.rules.conditions import ConditionContext, match_condition_node
from altera_api.rules.schema import ConditionNode


def _ctx(**overrides: object) -> ConditionContext:
    defaults = dict(
        product_name_lower="red lentil soup",
        brand_lower="greenleaf",
        retailer_category_lower="soups",
        retailer_subcategory_lower="pulse soups",
        ingredients_text_lower="red lentils, water, onion, salt",
        labels=frozenset({"vegan", "organic"}),
        language="en",
        country="GB",
        taxonomy_node=None,
    )
    defaults.update(overrides)
    return ConditionContext(**defaults)  # type: ignore[arg-type]


class TestLeafConditions:
    def test_product_name_contains_substring(self) -> None:
        node = ConditionNode(product_name_contains=("LENTIL",))
        assert match_condition_node(node, _ctx()) is True

    def test_product_name_contains_no_match(self) -> None:
        node = ConditionNode(product_name_contains=("beef",))
        assert match_condition_node(node, _ctx()) is False

    def test_product_name_matches_retailer_category(self) -> None:
        node = ConditionNode(product_name_contains=("pulse",))
        # Not in product_name_lower but in retailer_subcategory_lower
        assert match_condition_node(node, _ctx()) is True

    def test_brand_in(self) -> None:
        node = ConditionNode(brand_in=("GreenLeaf",))
        assert match_condition_node(node, _ctx()) is True

    def test_brand_in_no_match(self) -> None:
        node = ConditionNode(brand_in=("Other",))
        assert match_condition_node(node, _ctx()) is False

    def test_labels_contains(self) -> None:
        node = ConditionNode(labels_contains=("vegan",))
        assert match_condition_node(node, _ctx()) is True

    def test_labels_contains_case_insensitive(self) -> None:
        node = ConditionNode(labels_contains=("VEGAN",))
        assert match_condition_node(node, _ctx()) is True

    def test_labels_contains_no_match(self) -> None:
        node = ConditionNode(labels_contains=("gluten_free",))
        assert match_condition_node(node, _ctx()) is False

    def test_language_in(self) -> None:
        assert match_condition_node(ConditionNode(language_in=("en",)), _ctx())
        assert not match_condition_node(ConditionNode(language_in=("fr",)), _ctx())

    def test_country_in(self) -> None:
        assert match_condition_node(ConditionNode(country_in=("GB",)), _ctx())
        assert not match_condition_node(ConditionNode(country_in=("US",)), _ctx())

    def test_taxonomy_node_exact_only(self) -> None:
        ctx = _ctx(taxonomy_node="food.pulses.lentils")
        assert match_condition_node(
            ConditionNode(taxonomy_node="food.pulses.lentils"), ctx
        )
        assert not match_condition_node(ConditionNode(taxonomy_node="food.pulses"), ctx)

    def test_ingredients_contains(self) -> None:
        node = ConditionNode(ingredients_contains=("red lentil",))
        assert match_condition_node(node, _ctx())
        assert not match_condition_node(
            ConditionNode(ingredients_contains=("beef",)), _ctx()
        )


class TestGroups:
    def test_any_of(self) -> None:
        node = ConditionNode(
            any_of=(
                ConditionNode(product_name_contains=("beef",)),       # no
                ConditionNode(product_name_contains=("lentil",)),     # yes
            )
        )
        assert match_condition_node(node, _ctx())

    def test_any_of_none_match(self) -> None:
        node = ConditionNode(
            any_of=(
                ConditionNode(product_name_contains=("beef",)),
                ConditionNode(product_name_contains=("chicken",)),
            )
        )
        assert not match_condition_node(node, _ctx())

    def test_all_of(self) -> None:
        node = ConditionNode(
            all_of=(
                ConditionNode(product_name_contains=("lentil",)),
                ConditionNode(labels_contains=("vegan",)),
            )
        )
        assert match_condition_node(node, _ctx())

    def test_all_of_one_fails(self) -> None:
        node = ConditionNode(
            all_of=(
                ConditionNode(product_name_contains=("lentil",)),
                ConditionNode(labels_contains=("gluten_free",)),
            )
        )
        assert not match_condition_node(node, _ctx())

    def test_nested_groups(self) -> None:
        node = ConditionNode(
            any_of=(
                ConditionNode(
                    all_of=(
                        ConditionNode(product_name_contains=("lentil",)),
                        ConditionNode(labels_contains=("vegan",)),
                    )
                ),
                ConditionNode(taxonomy_node="food.pulses.lentils"),
            )
        )
        assert match_condition_node(node, _ctx())

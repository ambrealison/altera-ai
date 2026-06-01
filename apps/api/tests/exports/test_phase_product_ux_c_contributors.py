"""Phase Product-UX-C — Top-N product contributors extraction.

The report surfaces, for action-oriented guidance, the products that
most improve or most hurt the headline figures. These are derived from
the run's already-stored per-product rows (``rows_payload``) — no calc
formula is touched. These tests pin:

* PT positive ranks by plant protein desc; watch-out by animal desc.
* PT per-product attribution mirrors the engine (split → use split;
  composite no-split → 50/50; single-group → wholly plant or animal).
* WWF positive = target groups (FG1 plant, FG4, FG5, vegan/vegetarian
  composites); watch-out = FG7, FG1 animal, meat/seafood composites.
* Both lists cap at 10.
* Out-of-scope / unknown / malformed / missing rows are skipped, never
  raising — the report always renders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.exports.contributors import pt_contributors, wwf_contributors

_NOW = datetime.now(UTC)


def _uid(n: int) -> UUID:
    return UUID(int=n)


def _pt_product(n: int, *, name: str | None = None, category: str | None = "Épicerie") -> NormalizedProduct:
    return NormalizedProduct(
        id=_uid(n),
        upload_id=_uid(9001),
        project_id=_uid(9002),
        organisation_id=_uid(9003),
        row_number=n,
        external_product_id=f"P-{n:03d}",
        product_name=name or f"Produit PT {n}",
        retailer_category=category,
        weight_per_item_kg=Decimal("0.5"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("10")),
        created_at=_NOW,
    )


def _wwf_product(n: int, *, name: str | None = None, category: str | None = "Frais") -> NormalizedProduct:
    return NormalizedProduct(
        id=_uid(n),
        upload_id=_uid(9001),
        project_id=_uid(9002),
        organisation_id=_uid(9003),
        row_number=n,
        external_product_id=f"W-{n:03d}",
        product_name=name or f"Produit WWF {n}",
        retailer_category=category,
        is_own_brand=False,
        weight_per_item_kg=Decimal("0.5"),
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=Decimal("10"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=False,
        ),
        created_at=_NOW,
    )


def _pt_row(
    n: int,
    *,
    group: str,
    protein_kg: str,
    in_scope: bool = True,
    used_split: bool = False,
    plant: str | None = None,
    animal: str | None = None,
) -> dict:
    return {
        "run_id": str(_uid(1)),
        "product_id": str(_uid(n)),
        "in_scope": in_scope,
        "pt_group": group,
        "volume_kg": "5.00000000",
        "protein_pct": "20",
        "protein_kg": protein_kg,
        "used_per_product_split": used_split,
        "plant_protein_kg": plant,
        "animal_protein_kg": animal,
    }


def _wwf_row(
    n: int,
    *,
    food_group: str,
    weight_kg: str,
    in_scope: bool = True,
    is_composite: bool = False,
    bucket: str | None = None,
    label: str | None = None,
) -> dict:
    return {
        "run_id": str(_uid(1)),
        "product_id": str(_uid(n)),
        "in_scope": in_scope,
        "wwf_food_group": food_group,
        "wwf_subgroup_label": label,
        "weight_kg": weight_kg,
        "wwf_is_composite": is_composite,
        "wwf_composite_step1_bucket": bucket,
    }


# ---------------------------------------------------------------------------
# PT
# ---------------------------------------------------------------------------


def test_pt_positive_ranks_by_plant_protein_desc() -> None:
    rows = [
        _pt_row(1, group="plant_based_core", protein_kg="5"),
        _pt_row(2, group="plant_based_core", protein_kg="20"),
        _pt_row(3, group="plant_based_non_core", protein_kg="12"),
    ]
    products = [_pt_product(1), _pt_product(2), _pt_product(3)]
    positive, _ = pt_contributors(rows, products)
    assert [c.product_id for c in positive] == [str(_uid(2)), str(_uid(3)), str(_uid(1))]
    assert positive[0].plant_protein_kg == "20"
    assert positive[0].animal_protein_kg == "0"
    assert positive[0].rationale == "Cœur végétal"


def test_pt_watchout_ranks_by_animal_protein_desc() -> None:
    rows = [
        _pt_row(1, group="animal_core", protein_kg="8"),
        _pt_row(2, group="animal_core", protein_kg="30"),
        _pt_row(3, group="plant_based_core", protein_kg="50"),
    ]
    products = [_pt_product(1), _pt_product(2), _pt_product(3)]
    positive, watchout = pt_contributors(rows, products)
    # Only animal_core rows have animal protein
    assert [c.product_id for c in watchout] == [str(_uid(2)), str(_uid(1))]
    assert watchout[0].animal_protein_kg == "30"
    assert watchout[0].rationale == "Cœur animal"
    # The plant product is the sole positive entry.
    assert [c.product_id for c in positive] == [str(_uid(3))]


def test_pt_composite_without_split_is_50_50() -> None:
    rows = [_pt_row(1, group="composite_products", protein_kg="10")]
    products = [_pt_product(1)]
    positive, watchout = pt_contributors(rows, products)
    assert positive[0].plant_protein_kg == "5"
    assert watchout[0].animal_protein_kg == "5"
    assert "50/50" in positive[0].rationale
    # Same product appears in both lists (it contributes both halves).
    assert positive[0].product_id == watchout[0].product_id == str(_uid(1))


def test_pt_composite_with_per_product_split_uses_split() -> None:
    rows = [
        _pt_row(
            1,
            group="composite_products",
            protein_kg="10",
            used_split=True,
            plant="7",
            animal="3",
        )
    ]
    products = [_pt_product(1)]
    positive, watchout = pt_contributors(rows, products)
    assert positive[0].plant_protein_kg == "7"
    assert watchout[0].animal_protein_kg == "3"
    assert positive[0].rationale == "Composite — répartition par produit"


def test_pt_skips_out_of_scope_and_zero_protein() -> None:
    rows = [
        _pt_row(1, group="out_of_scope", protein_kg="0", in_scope=False),
        _pt_row(2, group="unknown", protein_kg="0", in_scope=False),
        _pt_row(3, group="plant_based_core", protein_kg="0"),
    ]
    products = [_pt_product(1), _pt_product(2), _pt_product(3)]
    positive, watchout = pt_contributors(rows, products)
    assert positive == []
    assert watchout == []


def test_pt_limit_is_ten() -> None:
    rows = [_pt_row(n, group="plant_based_core", protein_kg=str(n)) for n in range(1, 21)]
    products = [_pt_product(n) for n in range(1, 21)]
    positive, _ = pt_contributors(rows, products)
    assert len(positive) == 10
    # Highest protein first (product 20 has protein_kg=20).
    assert positive[0].product_id == str(_uid(20))


def test_pt_resilient_to_malformed_and_missing_rows() -> None:
    rows = [
        {"product_id": str(_uid(1))},  # no protein/group
        {"in_scope": True, "pt_group": "plant_based_core", "protein_kg": "oops",
         "product_id": str(_uid(2))},  # unparseable protein
        {"in_scope": True, "pt_group": "plant_based_core", "protein_kg": "9"},  # no product_id
        _pt_row(4, group="plant_based_core", protein_kg="9"),
    ]
    # Product 4 has no entry in the lookup → fallback name, no crash.
    positive, _ = pt_contributors(rows, [])
    assert len(positive) == 1
    assert positive[0].product_id == str(_uid(4))
    assert positive[0].product_name.startswith("Produit ")
    assert positive[0].retailer_category is None


def test_pt_joins_product_name_and_category() -> None:
    rows = [_pt_row(1, group="plant_based_core", protein_kg="9")]
    products = [_pt_product(1, name="Lentilles vertes", category="Légumineuses")]
    positive, _ = pt_contributors(rows, products)
    assert positive[0].product_name == "Lentilles vertes"
    assert positive[0].retailer_category == "Légumineuses"


# ---------------------------------------------------------------------------
# WWF
# ---------------------------------------------------------------------------


def test_wwf_positive_groups_ranked_by_weight() -> None:
    rows = [
        _wwf_row(1, food_group="FG4", weight_kg="100"),
        _wwf_row(2, food_group="FG1", weight_kg="200", label="legumes"),
        _wwf_row(3, food_group="FG5", weight_kg="150", label="whole_grain"),
        _wwf_row(4, food_group="FG1", weight_kg="80", label="red_meat"),  # watch-out
    ]
    products = [_wwf_product(n) for n in range(1, 5)]
    positive, watchout = wwf_contributors(rows, products)
    assert [c.product_id for c in positive] == [str(_uid(2)), str(_uid(3)), str(_uid(1))]
    assert positive[0].rationale == "Protéines végétales (FG1)"
    assert [c.product_id for c in watchout] == [str(_uid(4))]
    assert watchout[0].rationale == "Protéines animales (FG1)"


def test_wwf_composite_buckets_polarity() -> None:
    rows = [
        _wwf_row(1, food_group="FG1", weight_kg="10", is_composite=True, bucket="vegan"),
        _wwf_row(2, food_group="FG1", weight_kg="20", is_composite=True, bucket="vegetarian"),
        _wwf_row(3, food_group="FG1", weight_kg="30", is_composite=True, bucket="meat_based"),
        _wwf_row(4, food_group="FG1", weight_kg="40", is_composite=True, bucket="seafood_based"),
    ]
    products = [_wwf_product(n) for n in range(1, 5)]
    positive, watchout = wwf_contributors(rows, products)
    assert {c.product_id for c in positive} == {str(_uid(1)), str(_uid(2))}
    assert {c.product_id for c in watchout} == {str(_uid(3)), str(_uid(4))}
    assert watchout[0].product_id == str(_uid(4))  # seafood (40) > meat (30)


def test_wwf_fg7_is_watchout_and_neutral_groups_excluded() -> None:
    rows = [
        _wwf_row(1, food_group="FG7", weight_kg="55", label="animal_based_snack"),
        _wwf_row(2, food_group="FG2", weight_kg="999", label="cheese"),  # dairy: neither
        _wwf_row(3, food_group="FG3", weight_kg="999", label="animal_based_fat"),  # neither
        _wwf_row(4, food_group="FG6", weight_kg="999"),  # neither
    ]
    products = [_wwf_product(n) for n in range(1, 5)]
    positive, watchout = wwf_contributors(rows, products)
    assert positive == []
    assert [c.product_id for c in watchout] == [str(_uid(1))]
    assert watchout[0].rationale == "Snacks (FG7)"


def test_wwf_fg1_unknown_subgroup_skipped() -> None:
    rows = [_wwf_row(1, food_group="FG1", weight_kg="10", label=None)]
    positive, watchout = wwf_contributors(rows, [_wwf_product(1)])
    assert positive == []
    assert watchout == []


def test_wwf_fg5_whole_grain_rationale() -> None:
    rows = [
        _wwf_row(1, food_group="FG5", weight_kg="10", label="whole_grain"),
        _wwf_row(2, food_group="FG5", weight_kg="5", label="refined_grain"),
    ]
    positive, _ = wwf_contributors(rows, [_wwf_product(1), _wwf_product(2)])
    by_id = {c.product_id: c for c in positive}
    assert by_id[str(_uid(1))].rationale == "Céréales complètes (FG5)"
    assert by_id[str(_uid(2))].rationale == "Céréales (FG5)"


def test_wwf_limit_is_ten_and_skips_out_of_scope() -> None:
    rows = [_wwf_row(n, food_group="FG4", weight_kg=str(n)) for n in range(1, 21)]
    rows.append(_wwf_row(99, food_group="FG4", weight_kg="0", in_scope=False))
    products = [_wwf_product(n) for n in range(1, 21)]
    positive, _ = wwf_contributors(rows, products)
    assert len(positive) == 10
    assert positive[0].product_id == str(_uid(20))


def test_wwf_resilient_to_malformed_rows() -> None:
    rows = [
        {"in_scope": True, "wwf_food_group": "FG4", "weight_kg": "nope",
         "product_id": str(_uid(1))},  # unparseable weight
        {"in_scope": True, "wwf_food_group": "FG4", "weight_kg": "10"},  # no product_id
        _wwf_row(3, food_group="FG4", weight_kg="10"),
    ]
    positive, _ = wwf_contributors(rows, [])  # empty lookup → fallback names
    assert len(positive) == 1
    assert positive[0].product_id == str(_uid(3))
    assert positive[0].product_name.startswith("Produit ")


# ---------------------------------------------------------------------------
# Report-level wiring — contributors flow into the right section, and only
# the section matching the run's methodology is populated.
# ---------------------------------------------------------------------------


def _pt_summary_payload(run_id: UUID) -> dict:
    from altera_api.domain.protein_tracker import (
        ProteinTrackerCalculationSummary,
        ProteinTrackerGroup,
        ProteinTrackerGroupAggregate,
    )

    aggs = [
        ProteinTrackerGroupAggregate(
            pt_group=g, volume_kg=Decimal("100"), protein_kg=Decimal("20"), item_count=5
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    ]
    return ProteinTrackerCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_group=tuple(aggs),
        plant_protein_kg=Decimal("60"),
        animal_protein_kg=Decimal("40"),
        total_in_scope_protein_kg=Decimal("100"),
        plant_share_pct=Decimal("60"),
        animal_share_pct=Decimal("40"),
        rows_with_per_product_split=0,
        rows_protein_source_label=10,
        rows_protein_source_reference_db=5,
        out_of_scope_count=0,
        unknown_count=0,
        methodology_version="1.0.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    ).model_dump()


def _wwf_summary_payload(run_id: UUID) -> dict:
    from altera_api.domain.wwf import (
        WWFCalculationSummary,
        WWFFoodGroup,
        WWFFoodGroupAggregate,
    )

    fg_aggs = [
        WWFFoodGroupAggregate(
            food_group=fg,
            weight_kg=Decimal("100"),
            weight_kg_dairy_equiv=Decimal("100") if fg is WWFFoodGroup.FG2 else None,
            share_pct=Decimal("14"),
            phd_reference_share_pct=None,
        )
        for fg in (
            WWFFoodGroup.FG1,
            WWFFoodGroup.FG2,
            WWFFoodGroup.FG3,
            WWFFoodGroup.FG4,
            WWFFoodGroup.FG5,
            WWFFoodGroup.FG6,
            WWFFoodGroup.FG7,
        )
    ]
    return WWFCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_food_group=tuple(fg_aggs),
        total_sales_weight_in_scope_kg=Decimal("700"),
        composites_total_weight_kg=Decimal("40"),
        composites_meat_based_kg=Decimal("10"),
        composites_seafood_based_kg=Decimal("10"),
        composites_vegetarian_kg=Decimal("10"),
        composites_vegan_kg=Decimal("10"),
        whole_diet_plant_weight_kg=Decimal("400"),
        whole_diet_animal_weight_kg=Decimal("300"),
        out_of_scope_count=0,
        unknown_count=0,
        methodology_version="1.0.0",
        methodology_source_edition="WWF Food Practice 2024",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    ).model_dump()


def _run_record(*, project_id: UUID, org_id: UUID, methodology: Methodology, rows: list[dict], payload: dict):
    from uuid import uuid4

    from altera_api.api.state import RunRecord

    return RunRecord(
        id=uuid4(),
        project_id=project_id,
        methodology=methodology,
        started_at=_NOW,
        finished_at=_NOW,
        triggered_by=_uid(7777),
        rows_payload=rows,
        summary_payload=payload,
        rows_count=len(rows),
        organisation_id=org_id,
    )


def test_report_pt_run_populates_pt_contributors_only() -> None:
    from altera_api.api.state import InMemoryStore
    from altera_api.exports.report import build_report_document

    store = InMemoryStore()
    project = store.create_project(
        name="PT",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
    )
    products = [
        _pt_product(1, name="Lentilles"),
        _pt_product(2, name="Steak"),
    ]
    # Reparent products to the created project / org.
    products = [
        p.model_copy(update={"project_id": project.id, "organisation_id": project.organisation_id})
        for p in products
    ]
    store.add_products_bulk(products)

    rows = [
        _pt_row(1, group="plant_based_core", protein_kg="30"),
        _pt_row(2, group="animal_core", protein_kg="25"),
    ]
    run = _run_record(
        project_id=project.id,
        org_id=project.organisation_id,
        methodology=Methodology.PROTEIN_TRACKER,
        rows=rows,
        payload=_pt_summary_payload(_uid(1)),
    )

    doc = build_report_document(store, run, project)
    assert doc.wwf_section is None
    assert doc.pt_section is not None
    assert [c.product_name for c in doc.pt_section.top_positive_contributors] == ["Lentilles"]
    assert [c.product_name for c in doc.pt_section.top_watchout_contributors] == ["Steak"]


def test_report_wwf_run_populates_wwf_contributors_only() -> None:
    from altera_api.api.state import InMemoryStore
    from altera_api.exports.report import build_report_document

    store = InMemoryStore()
    project = store.create_project(
        name="WWF",
        methodologies_enabled=frozenset({Methodology.WWF}),
        reporting_period_label="2024",
    )
    products = [
        _wwf_product(1, name="Pois chiches"),
        _wwf_product(2, name="Bœuf haché"),
    ]
    products = [
        p.model_copy(update={"project_id": project.id, "organisation_id": project.organisation_id})
        for p in products
    ]
    store.add_products_bulk(products)

    rows = [
        _wwf_row(1, food_group="FG1", weight_kg="120", label="legumes"),
        _wwf_row(2, food_group="FG1", weight_kg="90", label="red_meat"),
    ]
    run = _run_record(
        project_id=project.id,
        org_id=project.organisation_id,
        methodology=Methodology.WWF,
        rows=rows,
        payload=_wwf_summary_payload(_uid(1)),
    )

    doc = build_report_document(store, run, project)
    assert doc.pt_section is None
    assert doc.wwf_section is not None
    assert [c.product_name for c in doc.wwf_section.top_positive_contributors] == ["Pois chiches"]
    assert [c.product_name for c in doc.wwf_section.top_watchout_contributors] == ["Bœuf haché"]

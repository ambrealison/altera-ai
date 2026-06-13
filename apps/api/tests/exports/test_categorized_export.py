"""Categorised retailer export — workbook builder + download route."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.exports.categorized_workbook import (
    ExportRow,
    build_categorized_workbook,
)
from altera_api.main import app


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


# ---------------------------------------------------------------------------
# Builder unit tests
# ---------------------------------------------------------------------------


def _rows() -> list[ExportRow]:
    # PTWWF025 mirrors the live demo25 golden: a vegan pizza that is PT
    # plant_based_non_core (NOT a PT composite) but a WWF Step-1 composite in
    # the vegan bucket, with FG1 as the schema-filler food group.
    return [
        ExportRow("PTWWF001", "Lentilles", "Légumineuses", "plant_based_core",
                  "deterministic", 1.0, "FG1", "legumes", None, "deterministic", 1.0),
        ExportRow("PTWWF007", "Steak bœuf", "Viande", "animal_core",
                  "deterministic", 1.0, "FG1", "red_meat", None, "deterministic", 1.0),
        ExportRow("PTWWF025", "Pizza fromage tomate vegan", "Plat",
                  "plant_based_non_core", "deterministic", 1.0, "FG1",
                  "alternative_protein_sources", "vegan", "deterministic", 1.0),
    ]


class TestWorkbookBuilder:
    def test_three_sheets_when_both_methodologies(self) -> None:
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True
        )
        wb = load_workbook(BytesIO(data))
        assert wb.sheetnames == ["Produits", "Analyse Protein Tracker", "Analyse WWF"]

    def test_pt_only_omits_wwf_sheet(self) -> None:
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=False
        )
        wb = load_workbook(BytesIO(data))
        assert "Analyse WWF" not in wb.sheetnames
        assert "Analyse Protein Tracker" in wb.sheetnames

    def test_english_sheet_names(self) -> None:
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True, lang="en"
        )
        wb = load_workbook(BytesIO(data))
        assert wb.sheetnames == ["Products", "Protein Tracker analysis", "WWF analysis"]

    def test_products_sheet_has_a_row_per_product(self) -> None:
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True
        )
        ws = load_workbook(BytesIO(data))["Produits"]
        # header + 3 products
        assert ws.max_row == 4
        names = [ws.cell(row=r, column=2).value for r in range(2, 5)]
        assert names == ["Lentilles", "Steak bœuf", "Pizza fromage tomate vegan"]

    def test_charts_are_embedded(self) -> None:
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True
        )
        wb = load_workbook(BytesIO(data))
        assert len(wb["Analyse Protein Tracker"]._charts) >= 1
        assert len(wb["Analyse WWF"]._charts) >= 1

    def test_composite_shows_composite_not_food_group(self) -> None:
        # The Pizza row is a vegan composite (filler FG1). Its WWF group cell
        # must read "Composite", never the FG1 food-group label, and the
        # bucket column carries the LOCALISED bucket ("Végane"), never the raw
        # enum value or the filler subgroup.
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True
        )
        ws = load_workbook(BytesIO(data))["Produits"]
        by_id = {
            ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row + 1)
        }
        pizza = by_id["PTWWF025"]
        assert ws.cell(row=pizza, column=7).value == "Composite"  # WWF group
        assert "FG1" not in str(ws.cell(row=pizza, column=7).value)
        # subgroup hidden for composites (openpyxl reads "" back as None)
        assert ws.cell(row=pizza, column=8).value in (None, "")
        assert ws.cell(row=pizza, column=9).value == "Végane"  # localised bucket

    def test_wwf_analysis_counts_composite_separately_not_filler_fg(self) -> None:
        # Regression guard: the WWF analysis distribution must tally the vegan
        # composite under a "Composite" category, NOT under its FG1 schema
        # filler. FG1 should reflect only the two genuine FG1 products.
        data = build_categorized_workbook(
            project_name="Demo", rows=_rows(), pt_enabled=True, wwf_enabled=True
        )
        ws = load_workbook(BytesIO(data))["Analyse WWF"]
        dist: dict[str, int] = {}
        for r in range(4, ws.max_row + 1):
            cat = ws.cell(row=r, column=1).value
            cnt = ws.cell(row=r, column=2).value
            if cat is not None:
                dist[str(cat)] = cnt
        assert dist.get("Composite") == 1
        assert dist.get("FG1 — Protéines") == 2  # Lentilles + Steak, NOT the pizza
        assert sum(dist.values()) == 3  # no double-counting


# ---------------------------------------------------------------------------
# Download route
# ---------------------------------------------------------------------------


def _seed_classified(store: InMemoryStore) -> str:
    project = store.create_project(
        name="Export demo",
        methodologies_enabled=frozenset(
            {Methodology.PROTEIN_TRACKER, Methodology.WWF}
        ),
        reporting_period_label="FY 2024",
        organisation_id=store.default_org_id,
        created_by=store.default_user_id,
    )
    upload_id = uuid4()
    now = datetime.now(UTC)
    pids: list = []
    for i in range(3):
        p = NormalizedProduct(
            id=uuid4(),
            project_id=project.id,
            upload_id=upload_id,
            organisation_id=store.default_org_id,
            row_number=i + 1,
            external_product_id=f"PTWWF{i + 1:03d}",
            product_name=f"Produit {i + 1}",
            brand=None,
            is_own_brand=False,
            retailer_category="Légumineuses",
            weight_per_item_kg=Decimal("0.15"),
            methodologies_enabled=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
            pt_fields=PTProductFields(items_purchased=Decimal("100")),
            wwf_fields=WWFProductFields(
                items_sold=Decimal("100"),
                retail_channel=RetailChannel.GROCERY_AMBIENT,
                is_own_brand=False,
            ),
            created_at=now,
        )
        store.add_product(p)
        pids.append(p.id)
        store.upsert_pt_classification(
            ProteinTrackerProductClassification(
                product_id=p.id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                rule_id="test.rule",
                updated_at=now,
            )
        )
        store.upsert_wwf_classification(
            WWFProductClassification(
                product_id=p.id,
                wwf_food_group=WWFFoodGroup.FG1,
                wwf_is_composite=False,
                fg1_subgroup=WWFFG1Subgroup.LEGUMES,
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                rule_id="test.rule",
                updated_at=now,
            )
        )
    return str(project.id)


class TestExportRoute:
    def test_download_returns_xlsx(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        project_id = _seed_classified(store)
        r = client.get(f"/api/v1/projects/{project_id}/export/categorized.xlsx")
        assert r.status_code == 200, r.text
        assert "spreadsheetml" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")
        wb = load_workbook(BytesIO(r.content))
        assert "Produits" in wb.sheetnames
        assert wb["Produits"].max_row == 4  # header + 3

    def test_english_lang_param(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        project_id = _seed_classified(store)
        r = client.get(
            f"/api/v1/projects/{project_id}/export/categorized.xlsx?lang=en"
        )
        assert r.status_code == 200
        wb = load_workbook(BytesIO(r.content))
        assert "Products" in wb.sheetnames

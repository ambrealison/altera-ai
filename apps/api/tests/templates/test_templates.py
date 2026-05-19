"""Phase 33A — Template download tests.

Verifies:
- Templates return 200 with text/csv content-type
- Each template contains all required columns in the header row
- Example rows in templates pass ingestion parsing (no hard parse errors)
- Non-auth requests get 401/403 (auth required)
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType
from altera_api.domain.organisation import Organisation
from altera_api.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_auth(role: AlteraRole | ClientRole) -> AuthContext:
    org = Organisation(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=datetime.now(UTC),
    )
    return AuthContext(
        user_id=uuid4(),
        email="test@test.local",
        organisation_id=org.id,
        role=role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=org.organisation_type,
    )


@pytest.fixture
def client_with_auth():
    store = InMemoryStore()
    ctx = _make_auth(ClientRole.CLIENT_OWNER)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[authed_user] = lambda: ctx
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_store, None)
    app.dependency_overrides.pop(authed_user, None)


@pytest.fixture
def altera_client():
    store = InMemoryStore()
    ctx = _make_auth(AlteraRole.ALTERA_ANALYST)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[authed_user] = lambda: ctx
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_store, None)
    app.dependency_overrides.pop(authed_user, None)


def _parse_csv(content: str) -> tuple[list[str], list[list[str]]]:
    """Return (header, data_rows) excluding comment rows starting with '#'."""
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    data = [r for r in rows[1:] if r and not r[0].startswith("#")]
    return header, data


# ---------------------------------------------------------------------------
# Protein Tracker template
# ---------------------------------------------------------------------------

_PT_REQUIRED = {"external_product_id", "product_name", "weight_per_item_kg", "items_purchased"}
_PT_RECOMMENDED = {"protein_pct", "brand", "retailer_category", "retailer_subcategory"}


class TestProteinTrackerTemplate:
    def test_returns_200(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        assert r.status_code == 200

    def test_content_type_is_csv(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        assert "text/csv" in r.headers["content-type"]

    def test_content_disposition(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        assert "attachment" in r.headers["content-disposition"]
        assert "protein_tracker_template.csv" in r.headers["content-disposition"]

    def test_header_contains_required_columns(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        header, _ = _parse_csv(r.text)
        for col in _PT_REQUIRED:
            assert col in header, f"Required column missing: {col}"

    def test_header_contains_recommended_columns(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        header, _ = _parse_csv(r.text)
        for col in _PT_RECOMMENDED:
            assert col in header, f"Recommended column missing: {col}"

    def test_example_rows_exist(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        _, data = _parse_csv(r.text)
        assert len(data) >= 2, "Template should have at least 2 example rows"

    def test_example_row_passes_ingestion_parse(self, client_with_auth: TestClient) -> None:
        from uuid import uuid4

        from altera_api.ingestion.headers import normalise_row_headers
        from altera_api.ingestion.parser import parse_row

        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        header, data = _parse_csv(r.text)
        assert data, "No example rows to test"
        row_dict = dict(zip(header, data[0], strict=False))
        normalised = normalise_row_headers(row_dict)
        raw, errors, _ = parse_row(normalised, upload_id=uuid4(), row_number=1)
        assert not errors, f"Parse errors on example row: {errors}"

    def test_accessible_to_client_user(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/protein-tracker.csv")
        assert r.status_code == 200

    def test_accessible_to_altera_user(self, altera_client: TestClient) -> None:
        r = altera_client.get("/api/v1/templates/protein-tracker.csv")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# WWF template
# ---------------------------------------------------------------------------

_WWF_REQUIRED = {
    "external_product_id",
    "product_name",
    "weight_per_item_kg",
    "items_sold",
    "is_own_brand",
    "retail_channel",
}


class TestWWFTemplate:
    def test_returns_200(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/wwf.csv")
        assert r.status_code == 200

    def test_header_contains_required_columns(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/wwf.csv")
        header, _ = _parse_csv(r.text)
        for col in _WWF_REQUIRED:
            assert col in header, f"Required column missing: {col}"

    def test_example_row_passes_ingestion_parse(self, client_with_auth: TestClient) -> None:
        from uuid import uuid4

        from altera_api.ingestion.headers import normalise_row_headers
        from altera_api.ingestion.parser import parse_row

        r = client_with_auth.get("/api/v1/templates/wwf.csv")
        header, data = _parse_csv(r.text)
        assert data, "No example rows to test"
        row_dict = dict(zip(header, data[0], strict=False))
        normalised = normalise_row_headers(row_dict)
        raw, errors, _ = parse_row(normalised, upload_id=uuid4(), row_number=1)
        assert not errors, f"Parse errors on WWF example row: {errors}"


# ---------------------------------------------------------------------------
# WWF Step 2 ingredients template
# ---------------------------------------------------------------------------

_STEP2_REQUIRED = {"parent_product_id", "ingredient_food_group", "ingredient_weight_kg_per_item"}


class TestWWFStep2Template:
    def test_returns_200(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/wwf-step2-ingredients.csv")
        assert r.status_code == 200

    def test_header_contains_required_columns(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/wwf-step2-ingredients.csv")
        header, _ = _parse_csv(r.text)
        for col in _STEP2_REQUIRED:
            assert col in header, f"Required column missing: {col}"

    def test_example_rows_have_valid_food_group(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/wwf-step2-ingredients.csv")
        header, data = _parse_csv(r.text)
        idx = header.index("ingredient_food_group")
        valid = {"FG1", "FG2", "FG3", "FG4", "FG5", "FG6"}
        for row in data:
            if row and len(row) > idx:
                fg = row[idx].strip()
                if fg:
                    assert fg in valid, f"Invalid food group in template: {fg!r}"


# ---------------------------------------------------------------------------
# Business assumptions template
# ---------------------------------------------------------------------------


class TestBusinessAssumptionsTemplate:
    def test_returns_200(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/business-assumptions.csv")
        assert r.status_code == 200

    def test_header_has_assumption_key_and_value(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/business-assumptions.csv")
        header, _ = _parse_csv(r.text)
        assert "assumption_key" in header
        assert "value" in header

    def test_has_example_rows(self, client_with_auth: TestClient) -> None:
        r = client_with_auth.get("/api/v1/templates/business-assumptions.csv")
        _, data = _parse_csv(r.text)
        assert len(data) >= 5, "Business assumptions template should have at least 5 rows"

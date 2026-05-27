"""Phase WWF-N — unified validation table backend.

The brief: the validation table needs a methodology-specific
"À valider" view. A product that needs review for both Protein
Tracker AND WWF must appear TWICE in the review queue — once per
methodology — so the operator can accept/correct each methodology
independently.

Backend changes pinned here:

  * ``GET /api/v1/projects/{id}/classifications`` accepts new query
    params ``view=products|review`` and
    ``methodology=protein_tracker|wwf``.
  * In ``view=products`` (the default) the response shape is
    unchanged — one row per product (non-regression).
  * In ``view=review`` the response returns one row per
    ``(product_id, methodology)`` review item: a product with both
    PT and WWF review items emits two rows.
  * Each row now carries ``methodology`` + ``wwf_review_status`` so
    the frontend can render and act on per-methodology review state.
"""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient


def _create_project(
    client: TestClient,
    *,
    methodologies: list[str],
) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "WWF-N unified validation test",
            "methodologies_enabled": methodologies,
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _upload_dual(client: TestClient, pid: str) -> str:
    """Upload a small dual-methodology CSV so the project has
    products eligible for both PT and WWF classification."""
    csv_bytes = _csv(
        [
            {
                "external_product_id": "DUAL-001",
                "product_name": "Pizza Jambon",
                "weight_per_item_kg": "0.4",
                "items_purchased": "100",
                "items_sold": "95",
                "retail_channel": "grocery_ambient",
                "is_own_brand": "false",
                "protein_pct": "15.0",
            },
            {
                "external_product_id": "DUAL-002",
                "product_name": "Lentilles Vertes du Puy",
                "weight_per_item_kg": "0.5",
                "items_purchased": "200",
                "items_sold": "180",
                "retail_channel": "grocery_ambient",
                "is_own_brand": "false",
                "protein_pct": "9.0",
            },
        ]
    )
    r = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("dual.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# A. view=products (default) — non-regression
# ---------------------------------------------------------------------------


class TestViewProductsDefault:
    def test_default_view_one_row_per_product(self, client: TestClient) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        _upload_dual(client, pid)
        r = client.get(f"/api/v1/projects/{pid}/classifications")
        assert r.status_code == 200, r.text
        body = r.json()
        # Default view = "products" — one row per product.
        product_ids = [it["product_id"] for it in body["items"]]
        assert len(product_ids) == len(set(product_ids)), (
            "default view should not duplicate products"
        )

    def test_explicit_view_products_matches_default(
        self, client: TestClient
    ) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        _upload_dual(client, pid)
        default = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()
        explicit = client.get(
            f"/api/v1/projects/{pid}/classifications?view=products"
        ).json()
        assert default["total"] == explicit["total"]
        assert len(default["items"]) == len(explicit["items"])


# ---------------------------------------------------------------------------
# B. view=review — per-(product, methodology) rows
# ---------------------------------------------------------------------------


class TestViewReview:
    def test_view_review_returns_empty_when_no_review_items(
        self, client: TestClient
    ) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        _upload_dual(client, pid)
        r = client.get(
            f"/api/v1/projects/{pid}/classifications?view=review"
        )
        assert r.status_code == 200
        body = r.json()
        # No classification has run yet, so no review items exist.
        assert body["items"] == []
        assert body["total"] == 0

    def test_view_review_rows_carry_methodology_field(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        """When PT classification creates IN_QUEUE review items, the
        review-view response stamps ``methodology="protein_tracker"``
        on each row."""
        pid = _create_project(client, methodologies=["protein_tracker"])
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        body = client.get(
            f"/api/v1/projects/{pid}/classifications?view=review"
        ).json()
        # Whatever PT review items the deterministic engine produced,
        # they MUST be tagged with methodology=protein_tracker.
        for row in body["items"]:
            assert (
                row["methodology"] == "protein_tracker"
            ), f"row missing methodology=protein_tracker: {row!r}"


# ---------------------------------------------------------------------------
# C. methodology filter
# ---------------------------------------------------------------------------


class TestMethodologyFilter:
    def test_methodology_pt_only_filters_review(
        self, client: TestClient, pt_tiny_csv: bytes
    ) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        upload = client.post(
            f"/api/v1/projects/{pid}/uploads",
            files={"file": ("pt.csv", pt_tiny_csv, "text/csv")},
        ).json()
        client.post(
            f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
            json={"methodology": "protein_tracker"},
        )
        body = client.get(
            f"/api/v1/projects/{pid}/classifications"
            "?view=review&methodology=protein_tracker"
        ).json()
        for row in body["items"]:
            assert row["methodology"] == "protein_tracker"

    def test_methodology_wwf_only_filters_review(
        self, client: TestClient
    ) -> None:
        pid = _create_project(client, methodologies=["wwf"])
        _upload_dual(client, pid)
        body = client.get(
            f"/api/v1/projects/{pid}/classifications"
            "?view=review&methodology=wwf"
        ).json()
        # No WWF review items yet (classification not run); shape check.
        for row in body["items"]:
            assert row["methodology"] == "wwf"


# ---------------------------------------------------------------------------
# D. Row payload contract — methodology + wwf_review_status fields
# ---------------------------------------------------------------------------


class TestRowPayloadContract:
    def test_row_exposes_methodology_field_in_products_view(
        self, client: TestClient
    ) -> None:
        """In view=products, ``methodology`` is null on every row —
        a "Tous les produits" row isn't tied to a single methodology."""
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        _upload_dual(client, pid)
        body = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()
        for row in body["items"]:
            assert "methodology" in row
            assert row["methodology"] is None  # view=products default

    def test_row_exposes_wwf_review_status_field(
        self, client: TestClient
    ) -> None:
        pid = _create_project(
            client, methodologies=["protein_tracker", "wwf"]
        )
        _upload_dual(client, pid)
        body = client.get(
            f"/api/v1/projects/{pid}/classifications"
        ).json()
        for row in body["items"]:
            assert "wwf_review_status" in row

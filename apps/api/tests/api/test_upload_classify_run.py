"""End-to-end happy path: create project → upload CSV → classify
→ review unknown items → run → export.
"""
from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient


def _create_project(client: TestClient, methodology: str = "protein_tracker") -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "FY 2024",
            "methodologies_enabled": [methodology],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_upload_returns_validation_report(client: TestClient, pt_tiny_csv: bytes) -> None:
    pid = _create_project(client)
    r = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ready_for_classification"
    assert body["row_count"] == 12
    assert body["products_count"] == 12
    assert body["errors"] == []


def test_upload_drops_commercial_columns(client: TestClient) -> None:
    pid = _create_project(client)
    csv_bytes = (
        b"external_product_id,product_name,weight_per_item_kg,revenue,"
        b"items_purchased,protein_pct\n"
        b"P-001,Lentil Soup,0.4,99999.99,1000,4.5\n"
    )
    r = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("retailer.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 201
    body = r.json()
    assert "revenue" in body["dropped_columns"]
    assert body["products_count"] == 1


def test_classify_upload_routes_unknowns_to_review(
    client: TestClient, pt_tiny_csv: bytes
) -> None:
    pid = _create_project(client)
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    ).json()
    r = client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["methodology"] == "protein_tracker"
    assert body["matched"] + body["pass_through"] + body["rule_collision"] == 12
    # The bundled rules cover the tiny fixture well; we don't constrain
    # the queued count, just that the totals add up.
    assert body["queued_for_review"] == body["pass_through"] + body["rule_collision"]


def test_review_listing_includes_queue_items(
    client: TestClient, pt_tiny_csv: bytes
) -> None:
    pid = _create_project(client)
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    r = client.get(f"/api/v1/projects/{pid}/review")
    assert r.status_code == 200
    items = r.json()
    assert all(i["methodology"] == "protein_tracker" for i in items)
    assert all(i["status"] == "in_queue" for i in items)


def test_reviewer_change_decision_promotes_classification(
    client: TestClient, pt_tiny_csv: bytes
) -> None:
    pid = _create_project(client)
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    queue = client.get(f"/api/v1/projects/{pid}/review").json()
    if not queue:
        return  # nothing to review for this dataset
    item = queue[0]
    r = client.post(
        f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
        json={
            "decision": "changed",
            "to_category": "plant_based_core",
            "reason": "reviewed manually",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_category"] == "plant_based_core"


def test_full_pipeline_run_and_export(
    client: TestClient, pt_tiny_csv: bytes
) -> None:
    pid = _create_project(client)
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "protein_tracker"},
    )
    # Force all unknown items to a real category so the run has scope.
    queue = client.get(f"/api/v1/projects/{pid}/review").json()
    for item in queue:
        client.post(
            f"/api/v1/projects/{pid}/review/{item['product_id']}/protein_tracker/decision",
            json={"decision": "changed", "to_category": "plant_based_core"},
        )

    r = client.post(
        f"/api/v1/projects/{pid}/runs",
        json={"methodology": "protein_tracker"},
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["methodology"] == "protein_tracker"
    assert run["rows_count"] == 12

    # CSV export
    csv_r = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/export?fmt=csv")
    assert csv_r.status_code == 200
    assert csv_r.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(csv_r.content.decode("utf-8-sig")))
    rows = list(reader)
    assert len(rows) == 12

    # JSON export
    json_r = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/export?fmt=json")
    assert json_r.status_code == 200
    payload = json_r.json()
    assert payload["run"]["methodology"] == "protein_tracker"
    assert len(payload["rows"]) == 12

    # Markdown export
    md_r = client.get(f"/api/v1/projects/{pid}/runs/{run['id']}/export?fmt=md")
    assert md_r.status_code == 200
    assert b"# Protein Tracker report" in md_r.content


def test_wwf_pipeline_smoke(client: TestClient, wwf_tiny_csv: bytes) -> None:
    pid = _create_project(client, methodology="wwf")
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("wwf_tiny.csv", wwf_tiny_csv, "text/csv")},
    ).json()
    assert upload["status"] == "ready_for_classification"
    classify = client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "wwf"},
    )
    assert classify.status_code == 200
    # Some products match the bundled WWF rules; rest go to review.
    assert classify.json()["matched"] >= 1


def test_classify_with_disabled_methodology_rejected(
    client: TestClient, pt_tiny_csv: bytes
) -> None:
    pid = _create_project(client, methodology="protein_tracker")
    upload = client.post(
        f"/api/v1/projects/{pid}/uploads",
        files={"file": ("pt_tiny.csv", pt_tiny_csv, "text/csv")},
    ).json()
    r = client.post(
        f"/api/v1/projects/{pid}/uploads/{upload['id']}/classify",
        json={"methodology": "wwf"},
    )
    assert r.status_code == 400


def test_export_404_when_run_missing(client: TestClient) -> None:
    pid = _create_project(client)
    r = client.get(
        f"/api/v1/projects/{pid}/runs/00000000-0000-0000-0000-00000000eeee/export?fmt=json"
    )
    assert r.status_code == 404

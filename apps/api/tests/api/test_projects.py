"""Project CRUD + listing routes."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_then_list_project(client: TestClient) -> None:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "FY 2024",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "FY 2024"
    assert body["methodologies_enabled"] == ["protein_tracker"]
    assert body["upload_count"] == 0
    project_id = body["id"]

    lst = client.get("/api/v1/projects")
    assert lst.status_code == 200
    items = lst.json()
    assert len(items) == 1
    assert items[0]["id"] == project_id


def test_get_project_404(client: TestClient) -> None:
    r = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


def test_create_project_requires_methodology(client: TestClient) -> None:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "X",
            "methodologies_enabled": [],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 422


def test_create_project_both_methodologies(client: TestClient) -> None:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Both",
            "methodologies_enabled": ["protein_tracker", "wwf"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    assert set(r.json()["methodologies_enabled"]) == {"protein_tracker", "wwf"}

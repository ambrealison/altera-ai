"""Smoke tests for the API skeleton."""

from __future__ import annotations

from fastapi.testclient import TestClient

from altera_api.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_app_and_phase() -> None:
    response = client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert body["app_name"] == "altera-ai-api"
    assert body["app_version"] == "0.0.1"
    assert body["build_phase"] == "phase_13c_supabase_auth"
    assert isinstance(body["build_phase_description"], str)
    assert body["build_phase_description"]

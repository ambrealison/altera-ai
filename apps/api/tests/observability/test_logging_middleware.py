"""Tests for RequestLoggingMiddleware.

Verifies:
- Authorization and Cookie headers are never captured in log output.
- X-Request-ID is echoed back in the response.
- A generated request_id is assigned when the header is absent.
- The path is present in the log record.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from altera_api.observability import RequestLoggingMiddleware, configure_logging


@pytest.fixture()
def logged_app(caplog: pytest.LogCaptureFixture) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ping")
    def ping() -> dict:
        return {"pong": True}

    configure_logging("DEBUG")
    return app


def test_auth_header_not_in_log_output(logged_app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(logged_app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO):
        client.get("/ping", headers={"Authorization": "Bearer secret-token"})

    full_output = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-token" not in full_output
    assert "Bearer" not in full_output


def test_request_id_echoed_in_response_header(logged_app: FastAPI) -> None:
    client = TestClient(logged_app, raise_server_exceptions=False)
    resp = client.get("/ping", headers={"x-request-id": "test-req-abc"})
    assert resp.headers.get("x-request-id") == "test-req-abc"


def test_request_id_generated_when_absent(logged_app: FastAPI) -> None:
    client = TestClient(logged_app, raise_server_exceptions=False)
    resp = client.get("/ping")
    assert resp.headers.get("x-request-id") is not None
    assert len(resp.headers["x-request-id"]) > 0


def test_path_present_in_log_record(logged_app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(logged_app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO):
        client.get("/ping")

    paths = [r.__dict__.get("path") for r in caplog.records]
    assert "/ping" in paths


def test_cookie_header_not_in_log_output(logged_app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(logged_app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO):
        client.get("/ping", headers={"Cookie": "session=abc123"})

    full_output = " ".join(r.getMessage() for r in caplog.records)
    assert "abc123" not in full_output

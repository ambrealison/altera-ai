"""Tests for optional Sentry integration.

Verifies:
- ``init_sentry()`` does not crash when SENTRY_DSN is absent.
- ``init_sentry()`` does not crash when sentry-sdk is not installed.
- ``_before_send`` strips the Authorization header from events.
- ``_before_send`` strips the Cookie header from events.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from altera_api.observability.sentry import _before_send, init_sentry


def test_init_sentry_no_dsn_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    init_sentry()  # should return silently without error


def test_init_sentry_empty_dsn_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")
    init_sentry()  # should return silently without error


def test_init_sentry_disabled_when_sdk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.io/123")
    with patch("altera_api.observability.sentry._SENTRY_AVAILABLE", False):
        with patch("altera_api.observability.sentry._sentry_sdk", None):
            init_sentry()  # should log warning and return, not raise


def test_before_send_strips_authorization_header() -> None:
    event: dict = {"request": {"headers": {"authorization": "Bearer tok", "content-type": "application/json"}}}
    result = _before_send(event, {})
    assert result is not None
    assert "authorization" not in result["request"]["headers"]
    assert result["request"]["headers"]["content-type"] == "application/json"


def test_before_send_strips_cookie_header() -> None:
    event: dict = {"request": {"headers": {"cookie": "session=abc", "accept": "*/*"}}}
    result = _before_send(event, {})
    assert result is not None
    assert "cookie" not in result["request"]["headers"]


def test_before_send_returns_event_unchanged_when_no_sensitive_headers() -> None:
    event: dict = {"request": {"headers": {"content-type": "application/json"}}, "extra": "data"}
    result = _before_send(event, {})
    assert result == event


def test_before_send_handles_missing_request_key() -> None:
    event: dict = {"message": "no request key"}
    result = _before_send(event, {})
    assert result is not None

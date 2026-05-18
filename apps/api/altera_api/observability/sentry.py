"""Optional Sentry integration.

Sentry is enabled only when ``SENTRY_DSN`` is set in the environment.
The ``sentry-sdk`` package is not a hard dependency — if it is not
installed the import silently falls back to a no-op.

``_before_send`` strips the ``Authorization`` header from all events so
bearer tokens never appear in Sentry.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import sentry_sdk as _sentry_sdk  # type: ignore[import-untyped]

    _SENTRY_AVAILABLE = True
except ImportError:
    _sentry_sdk = None  # type: ignore[assignment]
    _SENTRY_AVAILABLE = False


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip Authorization headers before the event reaches Sentry."""
    try:
        request = event.get("request", {})
        headers = request.get("headers", {})
        for key in list(headers):
            if key.lower() in {"authorization", "cookie"}:
                headers.pop(key)
    except Exception:  # noqa: BLE001
        pass
    return event


def init_sentry() -> None:
    """Initialise Sentry if ``SENTRY_DSN`` is configured.

    Safe to call when ``sentry-sdk`` is not installed — logs a debug
    message and returns without error.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry.disabled: SENTRY_DSN not set")
        return

    if not _SENTRY_AVAILABLE:
        logger.warning(
            "sentry.disabled: SENTRY_DSN is set but sentry-sdk is not installed; "
            "run `pip install sentry-sdk` to enable Sentry"
        )
        return

    environment = os.getenv("SENTRY_ENVIRONMENT", "production")
    try:
        traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    except ValueError:
        traces_sample_rate = 0.05

    _sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        before_send=_before_send,
        send_default_pii=False,
    )
    logger.info("sentry.initialized", extra={"environment": environment})

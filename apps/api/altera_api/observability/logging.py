"""Structured JSON logging with per-request context propagation.

Usage
-----
Call ``configure_logging()`` once at application startup (in the lifespan
handler). Each request sets its context with ``set_request_context()`` so
every log line emitted during that request carries the same fields.

    from altera_api.observability.logging import configure_logging, get_logger

    logger = get_logger(__name__)
    logger.info("upload.accepted", extra={"rows": 42})
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_org_id_var: ContextVar[str] = ContextVar("org_id", default="-")
_user_id_var: ContextVar[str] = ContextVar("user_id", default="-")


def set_request_context(
    *,
    request_id: str,
    org_id: str = "-",
    user_id: str = "-",
) -> None:
    """Set per-request context fields on the current async/thread context."""
    _request_id_var.set(request_id)
    _org_id_var.set(org_id)
    _user_id_var.set(user_id)


class _ContextFilter(logging.Filter):
    """Injects request_id / org_id / user_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        record.org_id = _org_id_var.get()  # type: ignore[attr-defined]
        record.user_id = _user_id_var.get()  # type: ignore[attr-defined]
        return True


class _JsonFormatter(logging.Formatter):
    """Format each log record as a single-line JSON object."""

    _STDLIB_ATTRS = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
            "request_id": getattr(record, "request_id", "-"),
            "org_id": getattr(record, "org_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        # Extra fields attached via logger.info("...", extra={...})
        for key, value in record.__dict__.items():
            if key not in self._STDLIB_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output to stdout.

    Idempotent — safe to call multiple times (e.g. pytest reruns the
    lifespan in the same process). Will not add a second handler if one
    with ``_JsonFormatter`` is already installed.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)

    # Suppress noisy third-party loggers.
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger pre-wired with the context filter."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, _ContextFilter) for f in logger.filters):
        logger.addFilter(_ContextFilter())
    return logger


# Module-level convenience logger for one-off use.
_startup_logger = get_logger(__name__)
_startup_start = time.monotonic()

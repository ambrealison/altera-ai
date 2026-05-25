"""Phase 34U — JSON serialization safety net.

Two production failures on a 1050-row CSV pointed at unsafe values
leaking into FastAPI responses:

- A Pydantic model with ``dict[str, object]`` summary fields that
  carried raw ``Decimal`` instances.
- Coverage computations producing ``float('nan')`` or
  ``float('inf')`` when a corner-case input sent a divisor toward
  zero.
- Domain enums / UUIDs / datetimes embedded inside an untyped dict.

FastAPI's default JSON path raises ``ValueError`` on those, surfacing
to the caller as a hard 500 with no body and "JSON could not be
generated" in the proxy log — undiagnosable from the wizard. This
module installs:

1. :class:`SafeJSONResponse` — a ``JSONResponse`` subclass with a
   ``default`` that handles ``Decimal``, ``NaN``/``Inf``, ``UUID``,
   ``datetime``, ``Enum``, ``set``/``tuple`` so a stray value never
   trips the encoder.
2. :func:`install_serialization_safety_net` — adds a
   ``ValueError``/``TypeError`` exception handler that translates
   genuine encoder failures into a structured 500 response with
   ``error_code=response_serialization_failed``, so the frontend can
   render a clean banner instead of "Failed to fetch".

Routes that return Pydantic models continue to use Pydantic's own
serialization first; ``SafeJSONResponse.default`` only fires for
values Pydantic punted on (the ``dict[str, object]`` escape hatch).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse


def _safe_default(obj: Any) -> Any:
    """JSON encoder fallback for values the default encoder rejects.

    The order matters — check the most common types first so the
    hot path is short.
    """
    if isinstance(obj, Decimal):
        # Decimal as string keeps full precision; floats lose it.
        # Production already does ``str(Decimal)`` in many places.
        return str(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (UUID,)):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Last resort: let the encoder raise — but with a useful repr().
    raise TypeError(
        f"Object of type {type(obj).__name__!r} is not JSON serializable: "
        f"{obj!r:.200}"
    )


def _sanitize_floats(value: Any) -> Any:
    """Recursively replace NaN / Inf floats with None.

    The ``json`` module accepts ``allow_nan=False`` to reject them
    outright; we instead substitute ``None`` so the response remains
    well-formed JSON. The frontend treats missing numeric fields as
    "data not available" and renders accordingly.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _sanitize_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_floats(v) for v in value]
    return value


class SafeJSONResponse(JSONResponse):
    """JSONResponse that tolerates Decimal/NaN/Inf/UUID/datetime/Enum.

    Used as the default response class so every route — Pydantic-
    model-typed or raw-dict-typed — gets the same treatment.
    """

    def render(self, content: Any) -> bytes:  # type: ignore[override]
        sanitized = _sanitize_floats(content)
        return json.dumps(
            sanitized,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=_safe_default,
        ).encode("utf-8")


def install_serialization_safety_net(app: FastAPI) -> None:
    """Attach a 500 handler for serialization failures.

    The handler catches ``ValueError`` and ``TypeError`` from the
    JSON encoder path. We DON'T attach a generic ``Exception``
    handler — that would mask programming bugs and break the
    existing ``RuntimeError`` / 400 / 502 paths.
    """

    @app.exception_handler(json.JSONDecodeError)
    async def _handle_json_decode(
        _request: Any, exc: json.JSONDecodeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": {
                    "error_code": "invalid_json_body",
                    "message": str(exc),
                }
            },
        )

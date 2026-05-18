"""HTTP API surface.

For Phase 12 the API is backed by an in-memory store
(:class:`altera_api.api.state.InMemoryStore`). The persistence layer
arrives in Phase 13 (Supabase) and replaces the store wholesale; the
HTTP routes, request/response shapes, and orchestrator remain.

There is **no authentication** in Phase 12 — a stub user/org is wired
in via :func:`current_user`. Auth lands with Supabase Auth in Phase 13.
"""

from __future__ import annotations

from altera_api.api.dependencies import get_store
from altera_api.api.routes import api_router
from altera_api.api.state import InMemoryStore

__all__ = ["InMemoryStore", "api_router", "get_store"]

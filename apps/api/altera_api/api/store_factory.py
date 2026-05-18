"""Active store / repository factory.

Re-exports ``get_repository()`` as ``get_store()`` so existing call sites
(routes, auth, dependencies) keep working without changes to their import
paths.  Tests continue to override via
``app.dependency_overrides[get_store] = lambda: fresh_store``.

The per-request JWT-scoped ``get_data_store`` dependency lives in
``altera_api.api.dependencies`` to avoid a circular import (auth
dependency imports get_store; get_data_store needs auth dependency).
"""

from __future__ import annotations

from altera_api.persistence.factory import get_repository
from altera_api.persistence.protocol import StoreProtocol


def get_store() -> StoreProtocol:
    """Singleton repository.  Override in tests via dependency_overrides."""
    return get_repository()

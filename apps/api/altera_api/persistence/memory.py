"""MemoryRepository — the in-memory StoreProtocol implementation.

InMemoryStore satisfies StoreProtocol structurally (duck-typed), so no
wrapper class is needed — this module simply re-exports it under the
canonical repository name.
"""
from altera_api.api.state import InMemoryStore

MemoryRepository = InMemoryStore

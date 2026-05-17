"""Persistence package — StoreProtocol + repository implementations."""
from altera_api.persistence.memory import MemoryRepository
from altera_api.persistence.protocol import StoreProtocol

__all__ = ["MemoryRepository", "StoreProtocol"]

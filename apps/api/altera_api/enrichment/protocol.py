"""Provider protocol for nutrition enrichment (Phase 23A).

Defines the interface every enrichment source must satisfy. Phase 23A
ships placeholder implementations only — no real external API calls are
made. All external providers (Open Food Facts, CIQUAL, OQALI, NEVO) are
registered in the source registry with ``is_available=False``.

To add a new provider:
  1. Create a class that satisfies ``NutritionEnrichmentProvider``.
  2. Set ``source`` and ``is_available`` appropriately.
  3. Implement ``enrich()`` — never raise; return a FAILED record on error.
  4. Register the source in ``registry.ENRICHMENT_SOURCE_REGISTRY``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from altera_api.domain.enrichment import NutritionEnrichmentRecord, NutritionEnrichmentSource


@runtime_checkable
class NutritionEnrichmentProvider(Protocol):
    """Interface for a nutrition data source used for enrichment.

    Implementations must be safe to call concurrently. They must never
    modify ``NormalizedProduct`` objects; results are returned as
    ``NutritionEnrichmentRecord`` objects and stored by the caller.
    """

    @property
    def source(self) -> NutritionEnrichmentSource:
        """The source enum value this provider supplies."""
        ...

    @property
    def is_available(self) -> bool:
        """Whether this provider has a working implementation.

        ``False`` for all external providers in Phase 23A. Callers must
        check this before invoking ``enrich()``.
        """
        ...

    def enrich(
        self,
        product_id: UUID,
        nutrient: str,
        *,
        now: datetime,
        created_by: UUID | None = None,
    ) -> NutritionEnrichmentRecord | None:
        """Attempt to find an enriched value for ``nutrient`` on ``product_id``.

        Returns:
            A ``NutritionEnrichmentRecord`` if the source has data, or
            ``None`` if the product is unknown to this source.

        Never raises; if the source call fails internally, return a
        record with ``status=FAILED`` and a descriptive ``rationale``.
        """
        ...

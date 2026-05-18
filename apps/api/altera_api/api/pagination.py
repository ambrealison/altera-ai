"""Pagination helpers.

List endpoints that may return large result sets support ``limit`` and
``offset`` query parameters and return a ``Page[T]`` envelope:

    {
        "items": [...],
        "total": 150,
        "limit": 50,
        "offset": 0
    }

Usage in a route::

    @router.get("/things", response_model=Page[ThingResponse])
    def list_things(
        pagination: Annotated[PaginationParams, Depends()],
        ...
    ) -> Page[ThingResponse]:
        all_items = store.list_all_things()
        return paginate(all_items, pagination)

For endpoints that build response objects from raw domain records use the
two-step variant::

    all_records = store.list_records()
    page_records = pagination.slice(all_records)
    return Page(
        items=[_to_response(r) for r in page_records],
        total=len(all_records),
        limit=pagination.limit,
        offset=pagination.offset,
    )
"""

from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    """Paginated list response envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int


class PaginationParams:
    """FastAPI-injectable pagination parameters.

    Use as a ``Depends()`` argument::

        pagination: Annotated[PaginationParams, Depends()]
    """

    def __init__(
        self,
        limit: int = Query(
            default=_DEFAULT_LIMIT,
            ge=1,
            le=_MAX_LIMIT,
            description=f"Maximum number of items to return (1–{_MAX_LIMIT}).",
        ),
        offset: int = Query(
            default=0,
            ge=0,
            description="Number of items to skip before returning results.",
        ),
    ) -> None:
        self.limit = limit
        self.offset = offset

    def slice(self, items: list) -> list:
        """Return the requested window of *items*."""
        return items[self.offset : self.offset + self.limit]


def paginate(items: list[T], pagination: PaginationParams) -> Page[T]:
    """Convenience wrapper: slice *items* and wrap in a ``Page``."""
    return Page(
        items=pagination.slice(items),
        total=len(items),
        limit=pagination.limit,
        offset=pagination.offset,
    )

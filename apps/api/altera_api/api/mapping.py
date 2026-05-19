"""Column mapping preview endpoint (Phase 33B).

POST /api/v1/uploads/preview-mapping
  Body: {"headers": ["SKU", "Product Name", ...]}
  Response: MappingPreviewResult

Requires authentication. No project-level permission needed — the
response is a pure inference over the submitted header list and the
server-side synonym registry; no tenant data is read or written.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from altera_api.auth import authed_user  # noqa: F401 — imported for the Depends side-effect
from altera_api.ingestion.mapping import MappingPreviewRequest, MappingPreviewResult, infer_mapping

mapping_router = APIRouter(tags=["uploads"])


@mapping_router.post(
    "/api/v1/uploads/preview-mapping",
    response_model=MappingPreviewResult,
)
def preview_mapping(
    body: MappingPreviewRequest,
    _auth: Annotated[object, Depends(authed_user)],
) -> MappingPreviewResult:
    """Infer canonical field mappings for a list of raw CSV headers.

    Returns a suggested mapping for each header based on the server-side
    synonym registry, along with lists of missing required fields for PT
    and WWF, and any duplicate normalised headers in the uploaded file.
    """
    return infer_mapping(body.headers, body.methodologies)

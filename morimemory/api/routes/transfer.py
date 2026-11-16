"""Memory table import/export endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...engine.types import Principal
from ...services.import_export import TransferService
from ..deps import get_transfer, require_admin, require_principal
from ..query import build_memory_filters
from ..responses import ok
from ..schemas import ImportRequest

router = APIRouter(prefix="/transfer", tags=["transfer"])


@router.get("/export")
async def export_memories(
    filters=Depends(build_memory_filters),
    format: str = Query(default="json", pattern="^(json|csv)$"),
    principal: Principal = Depends(require_principal),
    service: TransferService = Depends(get_transfer),
) -> Response:
    content, media_type, filename = await service.export(filters, principal, fmt=format)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_memories(
    payload: ImportRequest,
    principal: Principal = Depends(require_admin),
    service: TransferService = Depends(get_transfer),
) -> dict:
    result = await service.import_payload(
        payload.payload,
        principal,
        fmt=payload.format,
        target_space_id=payload.target_space_id,
        deduplicate=payload.deduplicate,
    )
    return ok(result, message="memory import completed")

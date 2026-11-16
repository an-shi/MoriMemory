"""Graph projection, local expansion and path tracing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...domain import AccessMode
from ...engine.graph import GraphService
from ...engine.types import Principal
from ..deps import get_graph, require_principal
from ..query import build_memory_filters
from ..responses import ok

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("")
async def graph(
    filters=Depends(build_memory_filters),
    center_id: str | None = Query(default=None),
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=150, ge=1, le=500),
    mode: str = Query(default=AccessMode.MANAGE),
    principal: Principal = Depends(require_principal),
    service: GraphService = Depends(get_graph),
) -> dict:
    data = await service.graph(
        principal,
        center_id=center_id,
        depth=depth,
        limit=limit,
        filters=filters,
        mode=mode,
    )
    return ok(data)


@router.get("/path")
async def path(
    source_id: str = Query(...),
    target_id: str = Query(...),
    max_depth: int = Query(default=6, ge=1, le=10),
    mode: str = Query(default=AccessMode.MANAGE),
    principal: Principal = Depends(require_principal),
    service: GraphService = Depends(get_graph),
) -> dict:
    return ok(
        await service.path(
            source_id,
            target_id,
            principal,
            max_depth=max_depth,
            mode=mode,
        )
    )

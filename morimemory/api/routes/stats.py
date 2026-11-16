"""Statistics overview."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...domain import AccessMode
from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_principal
from ..responses import ok
from ..serializers import event_to_dict, space_to_dict

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
async def stats(
    mode: str = Query(default=AccessMode.MANAGE),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    data = await engine.stats(principal, mode=mode)
    data["spaces_detail"] = [space_to_dict(space) for space in data.get("spaces_detail", [])]
    data["recent_events"] = [event_to_dict(event) for event in data.get("recent_events", [])]
    return ok(data)


@router.get("/overview")
async def overview(
    mode: str = Query(default=AccessMode.MANAGE),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    return await stats(mode=mode, principal=principal, engine=engine)

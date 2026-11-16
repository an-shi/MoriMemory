"""Audit event listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_principal
from ..responses import ok
from ..serializers import event_to_dict, space_to_dict

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    memory_id: str | None = Query(default=None),
    event_type: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    entries = await engine.recent_events(
        principal, limit=limit, offset=offset, memory_id=memory_id, event_types=event_type
    )
    return ok(
        {
            "items": [
                {**event_to_dict(entry["event"]), "space": space_to_dict(entry["space"])}
                for entry in entries
            ],
            "limit": limit,
            "offset": offset,
        }
    )

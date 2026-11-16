"""Memory space management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...domain import AccessMode
from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_admin, require_principal
from ..responses import ok
from ..schemas import SpaceCreateRequest, SpaceUpdateRequest
from ..serializers import space_to_dict

router = APIRouter(prefix="/spaces", tags=["spaces"])


@router.get("")
async def list_spaces(
    mode: str = Query(default=AccessMode.MANAGE),
    kind: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    entries = await engine.list_spaces(principal, mode=mode, kinds=kind)
    return ok(
        {
            "items": [
                {
                    **space_to_dict(entry["space"], memory_count=entry["memory_count"]),
                    "writable": entry["writable"],
                }
                for entry in entries
            ]
        }
    )


@router.post("")
async def create_space(
    payload: SpaceCreateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    space = await engine.create_space(payload.model_dump(), principal)
    return ok(space_to_dict(space), message="memory space created")


@router.patch("/{space_id}")
async def update_space(
    space_id: str,
    payload: SpaceUpdateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    space = await engine.update_space(space_id, payload.model_dump(exclude_none=True), principal)
    return ok(space_to_dict(space), message="memory space updated")


@router.delete("/{space_id}")
async def delete_space(
    space_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.delete_space(space_id, principal)
    return ok({}, message="memory space deleted")

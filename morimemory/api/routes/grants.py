"""User-to-character memory sharing grants."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_admin, require_principal
from ..responses import ok
from ..schemas import GrantCreateRequest
from ..serializers import grant_to_dict, memory_to_dict, space_to_dict

router = APIRouter(prefix="/grants", tags=["grants"])


@router.get("")
async def list_grants(
    space_id: str | None = Query(default=None),
    grantee_id: str | None = Query(default=None),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    grants = await engine.list_grants(principal, space_id=space_id, grantee_id=grantee_id)
    return ok({"items": [grant_to_dict(grant) for grant in grants]})


@router.post("")
async def create_grant(
    payload: GrantCreateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    result = await engine.create_grant(payload.model_dump(), principal)
    return ok(
        {
            "grant": grant_to_dict(result["grant"]),
            "space": space_to_dict(result["space"]),
            "memory": memory_to_dict(result["memory"]) if result["memory"] else None,
        },
        message="memory access granted",
    )


@router.delete("/{grant_id}")
async def revoke_grant(
    grant_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.revoke_grant(grant_id, principal)
    return ok({}, message="grant revoked")

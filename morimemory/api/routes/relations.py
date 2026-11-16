"""Manual relation management."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...domain import AccessMode
from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_admin
from ..responses import ok
from ..schemas import RelationCreateRequest
from ..serializers import relation_to_dict

router = APIRouter(prefix="/relations", tags=["relations"])


@router.post("")
async def create_relation(
    payload: RelationCreateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    relation = await engine.add_relation(
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation_type=payload.relation_type,
        strength=payload.strength,
        confidence=payload.confidence,
        attributes=payload.attributes,
        principal=principal,
        mode=AccessMode.MANAGE,
    )
    return ok(relation_to_dict(relation), message="relation created")


@router.delete("/{relation_id}")
async def delete_relation(
    relation_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.delete_relation(relation_id, principal, mode=AccessMode.MANAGE)
    return ok({}, message="relation deleted")

"""Entity listing for graph and debugging tools."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...config import ConfigManager
from ...db.database import Database
from ...db.repository import MemoryRepository
from ...domain import AccessMode
from ...engine.access import SpaceAccessService
from ...engine.types import Principal
from ..deps import get_config, get_database, require_principal
from ..responses import ok
from ..serializers import entity_to_dict

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("")
async def list_entities(
    limit: int = Query(default=200, ge=1, le=1000),
    space_id: str | None = Query(default=None),
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
    config: ConfigManager = Depends(get_config),
) -> dict:
    async with database.session() as session:
        repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
        scope = await SpaceAccessService(config).resolve(repository, principal, AccessMode.MANAGE)
        space_ids = [space_id] if space_id and space_id in scope.space_ids else scope.space_ids
        entities = await repository.list_entities(space_ids=space_ids, limit=limit)
        return ok({"items": [entity_to_dict(entity) for entity in entities]})

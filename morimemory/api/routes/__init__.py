"""API router assembly."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    backups,
    config,
    entities,
    events,
    grants,
    graph,
    health,
    ingest,
    maintenance,
    memories,
    relations,
    search,
    spaces,
    stats,
    tasks,
    transfer,
)

API_V1_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_V1_PREFIX)
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(stats.router)
api_router.include_router(spaces.router)
api_router.include_router(grants.router)
api_router.include_router(entities.router)
api_router.include_router(memories.router)
api_router.include_router(relations.router)
api_router.include_router(search.router)
api_router.include_router(graph.router)
api_router.include_router(ingest.router)
api_router.include_router(events.router)
api_router.include_router(tasks.router)
api_router.include_router(maintenance.router)
api_router.include_router(transfer.router)
api_router.include_router(backups.router)

__all__ = ["API_V1_PREFIX", "api_router"]

"""Maintenance operations: decay, consolidation and index rebuilds."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...domain import TaskType
from ...engine.maintenance import MaintenanceService
from ...engine.tasks import TaskRunner
from ...engine.types import Principal
from ..deps import get_maintenance, get_task_runner, require_admin
from ..responses import ok
from ..schemas import MaintenanceRequest

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post("/decay")
async def decay(
    _principal: Principal = Depends(require_admin),
    service: MaintenanceService = Depends(get_maintenance),
) -> dict:
    return ok(await service.apply_decay(), message="memory decay scan completed")


@router.post("/consolidate")
async def consolidate(
    payload: MaintenanceRequest,
    principal: Principal = Depends(require_admin),
    service: MaintenanceService = Depends(get_maintenance),
) -> dict:
    return ok(
        await service.consolidate(principal, space_id=payload.space_id),
        message="memory consolidation completed",
    )


@router.post("/rebuild-index")
async def rebuild_index(
    payload: MaintenanceRequest,
    _principal: Principal = Depends(require_admin),
    service: MaintenanceService = Depends(get_maintenance),
    runner: TaskRunner = Depends(get_task_runner),
) -> dict:
    if payload.async_mode:
        task_id = await runner.enqueue(
            TaskType.REBUILD_INDEX,
            {"include_embeddings": payload.include_embeddings},
            priority=20,
            dedupe_key="maintenance:rebuild-index",
        )
        return ok({"task_id": task_id, "async": True}, message="index rebuild queued")
    return ok(
        await service.rebuild_indexes(include_embeddings=payload.include_embeddings),
        message="indexes rebuilt",
    )


@router.post("/reembed")
async def reembed(
    payload: MaintenanceRequest,
    _principal: Principal = Depends(require_admin),
    service: MaintenanceService = Depends(get_maintenance),
    runner: TaskRunner = Depends(get_task_runner),
) -> dict:
    if payload.async_mode:
        task_id = await runner.enqueue(
            TaskType.REEMBED_ALL,
            {"limit": payload.limit},
            priority=10,
            dedupe_key="maintenance:reembed",
        )
        return ok({"task_id": task_id, "async": True}, message="re-embedding queued")
    return ok(
        await service.reembed_all(limit=payload.limit),
        message="re-embedding completed",
    )


@router.post("/embed/{memory_id}")
async def embed_one(
    memory_id: str,
    _principal: Principal = Depends(require_admin),
    service: MaintenanceService = Depends(get_maintenance),
) -> dict:
    return ok(await service.embed_memory(memory_id), message="embedding generated")

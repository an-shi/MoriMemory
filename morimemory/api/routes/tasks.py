"""Background task inspection and retry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...db.database import Database
from ...db.repository import MemoryRepository
from ...engine.types import Principal
from ..deps import get_database, require_admin, require_principal
from ..responses import ok
from ..serializers import task_to_dict

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    status: list[str] | None = Query(default=None),
    task_type: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=200),
    _principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
) -> dict:
    async with database.session() as session:
        repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
        tasks, total = await repository.list_tasks(
            statuses=status,
            task_types=task_type,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        return ok(
            {
                "items": [task_to_dict(task) for task in tasks],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    _principal: Principal = Depends(require_admin),
    database: Database = Depends(get_database),
) -> dict:
    async with database.session() as session:
        repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
        task = await repository.retry_task(task_id)
        if task is None:
            from ...errors import NotFoundError

            raise NotFoundError("task not found")
        return ok(task_to_dict(task), message="task rescheduled")

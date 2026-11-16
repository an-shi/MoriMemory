"""Memory CRUD, evidence, versions and lifecycle actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...db.database import Database
from ...db.repository import MemoryRepository
from ...domain import AccessMode
from ...engine.access import SpaceAccessService
from ...engine.core import MemoryEngine
from ...engine.types import MemoryCandidate, Principal
from ...errors import ValidationError
from ..deps import get_database, get_engine, require_admin, require_principal
from ..query import build_memory_filters
from ..responses import ok
from ..schemas import (
    BulkActionRequest,
    EvidenceCreateRequest,
    MemoryCreateRequest,
    MemoryUpdateRequest,
    MergeRequest,
    ReinforceRequest,
    RollbackRequest,
)
from ..serializers import (
    entity_to_dict,
    evidence_to_dict,
    memory_to_dict,
    relation_to_dict,
    version_to_dict,
)

router = APIRouter(prefix="/memories", tags=["memories"])

_SORT_FIELDS = {
    "created_at",
    "updated_at",
    "occurred_at",
    "importance",
    "confidence",
    "retention",
    "access_count",
    "reinforcement_count",
    "title",
    "memory_type",
    "category",
    "status",
}


async def _serialize_details(engine: MemoryEngine, memory_id: str, principal: Principal) -> dict:
    details = await engine.get_memory_details(memory_id, principal)
    return {
        **memory_to_dict(details.memory, space=details.space),
        "evidence": [evidence_to_dict(item) for item in details.evidence],
        "versions": [version_to_dict(item) for item in details.versions],
        "relations": [relation_to_dict(relation, other) for relation, other in details.relations],
        "entities": [entity_to_dict(entity) for entity in details.entities],
        "versions_total": details.versions_total,
    }


@router.get("")
async def list_memories(
    filters=Depends(build_memory_filters),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=200),
    sort_by: str = Query(default="updated_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    mode: str = Query(default=AccessMode.MANAGE),
    focus_id: str | None = Query(default=None),
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
    database: Database = Depends(get_database),
) -> dict:
    if sort_by not in _SORT_FIELDS:
        raise ValidationError(f"unsupported sort field: {sort_by}")
    actual_focus = None
    if focus_id:
        ids, _scope = await engine.list_memory_ids(
            filters,
            principal,
            sort_by=sort_by,
            sort_order=sort_order,
            mode=mode,
        )
        if focus_id in ids:
            page = ids.index(focus_id) // per_page + 1
            actual_focus = focus_id

    memories, total, scope, spaces = await engine.list_memories(
        filters,
        principal,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
        mode=mode,
    )
    counts: dict[str, dict[str, int]] = {}
    if memories:
        async with database.session() as session:
            repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
            counts = await repository.counts_for_memories([memory.id for memory in memories])
    items = [
        memory_to_dict(
            memory,
            space=spaces.get(memory.space_id),
            evidence_count=counts.get(memory.id, {}).get("evidence_count", 0),
            relation_count=counts.get(memory.id, {}).get("relation_count", 0),
        )
        for memory in memories
    ]
    return ok(
        {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "focus_id": actual_focus,
            "scope": SpaceAccessService.describe(scope),
        }
    )


@router.post("")
async def create_memory(
    payload: MemoryCreateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    candidate = MemoryCandidate.model_validate(
        payload.model_dump(exclude={"space_id", "deduplicate", "force"})
    )
    result = await engine.create_memory(
        candidate,
        principal,
        space_id=payload.space_id,
        mode=AccessMode.MANAGE,
        deduplicate=payload.deduplicate,
        force=payload.force,
    )
    return ok(
        {
            "action": result.action,
            "memory": memory_to_dict(result.memory, space=result.space),
            "duplicate_of": result.duplicate_of,
            "embedding_pending": result.embedding_pending,
            "relations": [relation_to_dict(relation) for relation in result.relations],
        },
        message="memory reinforcement recorded"
        if result.action == "reinforced"
        else "memory created",
    )


@router.post("/bulk")
async def bulk_action(
    payload: BulkActionRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    result = await engine.bulk_action(payload.ids, payload.action, principal)
    return ok(result, message=f"bulk {payload.action} completed")


@router.get("/{memory_id}")
async def get_memory(
    memory_id: str,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    return ok(await _serialize_details(engine, memory_id, principal))


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.update_memory(
        memory_id,
        payload.model_dump(exclude_none=True),
        principal,
        mode=AccessMode.MANAGE,
    )
    return ok(await _serialize_details(engine, memory_id, principal), message="memory updated")


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.delete_memory(memory_id, principal, mode=AccessMode.MANAGE)
    return ok({}, message="memory permanently deleted")


@router.post("/{memory_id}/archive")
async def archive_memory(
    memory_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.archive_memory(memory_id, principal, mode=AccessMode.MANAGE)
    return ok(await _serialize_details(engine, memory_id, principal), message="memory archived")


@router.post("/{memory_id}/restore")
async def restore_memory(
    memory_id: str,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.restore_memory(memory_id, principal, mode=AccessMode.MANAGE)
    return ok(await _serialize_details(engine, memory_id, principal), message="memory restored")


@router.post("/{memory_id}/reinforce")
async def reinforce_memory(
    memory_id: str,
    payload: ReinforceRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.reinforce_memory(
        memory_id,
        principal,
        evidence=payload.evidence,
        mode=AccessMode.MANAGE,
    )
    return ok(await _serialize_details(engine, memory_id, principal), message="memory reinforced")


@router.post("/{memory_id}/merge")
async def merge_memory(
    memory_id: str,
    payload: MergeRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    result = await engine.merge_memories(
        payload.source_id, memory_id, principal, mode=AccessMode.MANAGE
    )
    return ok(
        {
            "source": memory_to_dict(result["source"]),
            "target": memory_to_dict(result["target"]),
            "relation": relation_to_dict(result["relation"]),
        },
        message="memory merged",
    )


@router.post("/{memory_id}/rollback")
async def rollback_memory(
    memory_id: str,
    payload: RollbackRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    await engine.rollback_version(memory_id, payload.version, principal, mode=AccessMode.MANAGE)
    return ok(await _serialize_details(engine, memory_id, principal), message="memory rolled back")


@router.get("/{memory_id}/versions")
async def list_versions(
    memory_id: str,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    details = await engine.get_memory_details(memory_id, principal)
    return ok(
        {
            "items": [version_to_dict(item) for item in details.versions],
            "total": details.versions_total,
        }
    )


@router.get("/{memory_id}/evidence")
async def list_evidence(
    memory_id: str,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    details = await engine.get_memory_details(memory_id, principal)
    return ok({"items": [evidence_to_dict(item) for item in details.evidence]})


@router.post("/{memory_id}/evidence")
async def add_evidence(
    memory_id: str,
    payload: EvidenceCreateRequest,
    principal: Principal = Depends(require_admin),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    record = await engine.add_evidence(
        memory_id,
        payload,
        principal,
        reinforce=payload.reinforce,
        mode=AccessMode.MANAGE,
    )
    return ok(evidence_to_dict(record), message="evidence added")


@router.get("/{memory_id}/relations")
async def list_relations(
    memory_id: str,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    details = await engine.get_memory_details(memory_id, principal)
    return ok(
        {"items": [relation_to_dict(relation, other) for relation, other in details.relations]}
    )

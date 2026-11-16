"""Conversation ingestion for MoriBot's post-reply memory pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...engine.core import MemoryEngine
from ...engine.types import Principal
from ..deps import get_engine, require_principal
from ..responses import ok
from ..schemas import IngestRequest

router = APIRouter(tags=["ingest"])


async def _ingest(
    payload: IngestRequest,
    principal: Principal,
    engine: MemoryEngine,
) -> dict:
    result = await engine.ingest_conversation(
        payload.messages,
        principal,
        conversation_id=payload.conversation_id,
        character_id=payload.character_id,
        async_mode=payload.async_mode,
    )
    return ok(
        {
            "task_id": result.task_id,
            "async": result.async_mode,
            "candidates": result.candidates,
            "created": result.created,
            "reinforced": result.reinforced,
            "memory_ids": result.memories,
            "spaces": result.spaces,
        },
        message="conversation queued for memory extraction"
        if result.async_mode
        else "conversation memory extraction completed",
    )


@router.post("/ingest")
async def ingest(
    payload: IngestRequest,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    return await _ingest(payload, principal, engine)


@router.post("/conversations/ingest")
async def ingest_conversation(
    payload: IngestRequest,
    principal: Principal = Depends(require_principal),
    engine: MemoryEngine = Depends(get_engine),
) -> dict:
    return await _ingest(payload, principal, engine)

"""Hybrid search and context assembly endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.database import Database
from ...db.repository import MemoryRepository
from ...engine.access import SpaceAccessService
from ...engine.retrieval import RetrievalHit, RetrievalService, estimate_tokens
from ...engine.types import Principal
from ..deps import get_database, get_retrieval, require_principal
from ..responses import ok
from ..schemas import ContextRequest, SearchRequest
from ..serializers import memory_to_dict

router = APIRouter(tags=["search"])


def _hit_payload(hit: RetrievalHit, space=None) -> dict:
    return {
        **memory_to_dict(hit.memory, space=space),
        "score": hit.score,
        "scores": {
            "semantic": hit.semantic,
            "keyword": hit.keyword,
            "importance": hit.importance,
            "confidence": hit.confidence,
            "recency": hit.recency,
            "strength": hit.strength,
            "relation_boost": hit.relation_boost,
        },
        "reasons": hit.reasons,
        "token_estimate": hit.token_estimate,
    }


def _search_kwargs(payload: SearchRequest) -> dict:
    return {
        "mode": payload.mode,
        "limit": payload.limit,
        "token_budget": payload.token_budget,
        "space_id": payload.space_id,
        "space_kind": payload.space_kind,
        "memory_types": payload.memory_types,
        "categories": payload.categories,
        "statuses": payload.statuses,
        "min_importance": payload.min_importance,
        "min_confidence": payload.min_confidence,
        "tags": payload.tags,
        "created_after": payload.created_after,
        "created_before": payload.created_before,
        "occurred_after": payload.occurred_after,
        "occurred_before": payload.occurred_before,
        "record_access": payload.record_access,
        "expand_relations": payload.expand_relations,
    }


@router.post("/search")
async def search(
    payload: SearchRequest,
    principal: Principal = Depends(require_principal),
    retrieval: RetrievalService = Depends(get_retrieval),
    database: Database = Depends(get_database),
) -> dict:
    result = await retrieval.search(payload.query, principal, **_search_kwargs(payload))
    spaces = {}
    if result.hits:
        async with database.session() as session:
            repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
            spaces = {
                space.id: space
                for space in await repository.list_spaces(
                    ids=list({hit.memory.space_id for hit in result.hits})
                )
            }
    return ok(
        {
            "query": result.query,
            "items": [_hit_payload(hit, spaces.get(hit.memory.space_id)) for hit in result.hits],
            "candidates": result.candidates,
            "tokens_used": result.tokens_used,
            "token_budget": result.token_budget,
            "truncated": result.truncated,
            "scope": SpaceAccessService.describe(result.scope),
        }
    )


@router.post("/context")
async def context(
    payload: ContextRequest,
    principal: Principal = Depends(require_principal),
    retrieval: RetrievalService = Depends(get_retrieval),
    database: Database = Depends(get_database),
) -> dict:
    kwargs = _search_kwargs(payload)
    result, text = await retrieval.build_context(payload.query, principal, **kwargs)
    spaces = {}
    if result.hits:
        async with database.session() as session:
            repository = MemoryRepository(session, fts_enabled=database.fts_enabled)
            spaces = {
                space.id: space
                for space in await repository.list_spaces(
                    ids=list({hit.memory.space_id for hit in result.hits})
                )
            }
    return ok(
        {
            "query": result.query,
            "context": text,
            "context_tokens": estimate_tokens(text),
            "items": [_hit_payload(hit, spaces.get(hit.memory.space_id)) for hit in result.hits],
            "tokens_used": result.tokens_used,
            "token_budget": result.token_budget,
            "truncated": result.truncated,
            "scope": SpaceAccessService.describe(result.scope),
        }
    )

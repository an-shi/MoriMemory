"""Shared query parameter helpers."""

from __future__ import annotations

from datetime import datetime

from fastapi import Query

from ..db.repository import MemoryFilters


def build_memory_filters(
    q: str | None = Query(default=None),
    space_id: str | None = Query(default=None),
    space_ids: list[str] | None = Query(default=None),
    space_kind: str | None = Query(default=None),
    memory_type: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    source_kind: list[str] | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    min_importance: float | None = Query(default=None, ge=0, le=1),
    max_importance: float | None = Query(default=None, ge=0, le=1),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    max_confidence: float | None = Query(default=None, ge=0, le=1),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    occurred_after: datetime | None = Query(default=None),
    occurred_before: datetime | None = Query(default=None),
    pinned: bool | None = Query(default=None),
    include_deleted: bool = Query(default=False),
) -> MemoryFilters:
    return MemoryFilters(
        q=q,
        space_id=space_id,
        space_ids=space_ids,
        space_kind=space_kind,
        memory_types=memory_type,
        categories=category,
        statuses=status,
        source_kinds=source_kind,
        tags=tags,
        min_importance=min_importance,
        max_importance=max_importance,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
        created_after=created_after,
        created_before=created_before,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
        pinned=pinned,
        include_deleted=include_deleted,
    )


def parse_mode(value: str, allowed: set[str], default: str = "manage") -> str:
    return value if value in allowed else default

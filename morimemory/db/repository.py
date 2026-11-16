"""Data access helpers for spaces, memories and lifecycle metadata."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, and_, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    MemorySpaceKind,
    MemoryStatus,
    MemoryType,
    RelationType,
    SourceKind,
    TaskStatus,
)
from .fts import fts_delete, fts_search, fts_upsert
from .models import (
    AsyncTask,
    Entity,
    Memory,
    MemoryEmbedding,
    MemoryEntity,
    MemoryEvent,
    MemoryEvidence,
    MemoryGrant,
    MemoryRelation,
    MemorySpace,
    MemoryVersion,
    new_id,
    utcnow,
)


def naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def memory_snapshot(memory: Memory) -> dict[str, Any]:
    """JSON-safe snapshot used for version history."""

    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "id": memory.id,
        "space_id": memory.space_id,
        "title": memory.title,
        "content": memory.content,
        "memory_type": memory.memory_type,
        "category": memory.category,
        "source_kind": memory.source_kind,
        "status": memory.status,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "retention": memory.retention,
        "access_count": memory.access_count,
        "reinforcement_count": memory.reinforcement_count,
        "contradiction_count": memory.contradiction_count,
        "pinned": memory.pinned,
        "tags": list(memory.tags or []),
        "attributes": dict(memory.attributes or {}),
        "language": memory.language,
        "content_hash": memory.content_hash,
        "occurred_at": iso(memory.occurred_at),
        "last_accessed_at": iso(memory.last_accessed_at),
        "last_reinforced_at": iso(memory.last_reinforced_at),
        "expires_at": iso(memory.expires_at),
        "version": memory.version,
        "created_at": iso(memory.created_at),
        "updated_at": iso(memory.updated_at),
    }


@dataclass(slots=True)
class MemoryFilters:
    """Server-side filters shared by listing, export and graph queries."""

    space_ids: list[str] | None = None
    space_id: str | None = None
    space_kind: str | None = None
    q: str | None = None
    memory_types: list[str] | None = None
    categories: list[str] | None = None
    statuses: list[str] | None = None
    source_kinds: list[str] | None = None
    tags: list[str] | None = None
    min_importance: float | None = None
    min_confidence: float | None = None
    max_importance: float | None = None
    max_confidence: float | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    pinned: bool | None = None
    include_deleted: bool = False
    ids: list[str] | None = None


class MemoryRepository:
    """Thin repository over an ``AsyncSession``.

    Services compose these primitives so the same transaction can cover
    memory writes, evidence, versions, relations and audit events.
    """

    def __init__(self, session: AsyncSession, *, fts_enabled: bool = True) -> None:
        self.session = session
        self.fts_enabled = fts_enabled

    # -- Spaces ---------------------------------------------------------

    async def get_space(self, space_id: str) -> MemorySpace | None:
        return await self.session.get(MemorySpace, space_id)

    async def get_space_by_key(self, key: str) -> MemorySpace | None:
        result = await self.session.execute(select(MemorySpace).where(MemorySpace.key == key))
        return result.scalar_one_or_none()

    async def list_spaces(
        self,
        *,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        ids: list[str] | None = None,
    ) -> list[MemorySpace]:
        statement = select(MemorySpace)
        if kinds:
            statement = statement.where(MemorySpace.kind.in_(kinds))
        if statuses:
            statement = statement.where(MemorySpace.status.in_(statuses))
        if ids is not None:
            if not ids:
                return []
            statement = statement.where(MemorySpace.id.in_(ids))
        statement = statement.order_by(MemorySpace.kind, MemorySpace.name)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    @staticmethod
    def space_key(
        kind: str,
        *,
        user_id: str | None = None,
        character_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        if kind == MemorySpaceKind.USER:
            return f"user:{user_id or 'local'}"
        if kind == MemorySpaceKind.CHARACTER:
            return f"character:{character_id or 'default'}"
        if kind == MemorySpaceKind.CONVERSATION:
            return f"conversation:{conversation_id or 'default'}"
        return f"{kind}:{user_id or character_id or conversation_id or 'default'}"

    async def ensure_space(
        self,
        kind: str,
        *,
        user_id: str | None = None,
        character_id: str | None = None,
        conversation_id: str | None = None,
        name: str = "",
        description: str = "",
    ) -> MemorySpace:
        key = self.space_key(
            kind,
            user_id=user_id,
            character_id=character_id,
            conversation_id=conversation_id,
        )
        existing = await self.get_space_by_key(key)
        if existing is not None:
            return existing
        labels = {
            MemorySpaceKind.USER: f"用户 · {user_id or 'local'}",
            MemorySpaceKind.CHARACTER: f"角色 · {character_id or 'default'}",
            MemorySpaceKind.CONVERSATION: f"会话 · {conversation_id or 'default'}",
        }
        space = MemorySpace(
            id=new_id(),
            kind=kind,
            key=key,
            name=name or labels.get(kind, key),
            description=description,
            user_id=user_id,
            character_id=character_id,
            conversation_id=conversation_id,
            status="active",
            settings={},
        )
        self.session.add(space)
        await self.session.flush()
        return space

    async def create_space(self, values: dict[str, Any]) -> MemorySpace:
        kind = str(values["kind"])
        key = str(
            values.get("key")
            or self.space_key(
                kind,
                user_id=values.get("user_id"),
                character_id=values.get("character_id"),
                conversation_id=values.get("conversation_id"),
            )
        )
        space = MemorySpace(
            id=values.get("id") or new_id(),
            kind=kind,
            key=key,
            name=values.get("name", ""),
            description=values.get("description", ""),
            user_id=values.get("user_id"),
            character_id=values.get("character_id"),
            conversation_id=values.get("conversation_id"),
            status=values.get("status", "active"),
            settings=values.get("settings") or {},
        )
        self.session.add(space)
        await self.session.flush()
        return space

    async def delete_space(self, space: MemorySpace) -> None:
        memory_ids = (
            await self.session.execute(select(Memory.id).where(Memory.space_id == space.id))
        ).scalars()
        if self.fts_enabled:
            for memory_id in list(memory_ids):
                await fts_delete(self.session, memory_id)
        await self.session.delete(space)
        await self.session.flush()

    # -- Memories -------------------------------------------------------

    async def get_memory(self, memory_id: str, *, include_deleted: bool = False) -> Memory | None:
        memory = await self.session.get(Memory, memory_id)
        if memory is None:
            return None
        if not include_deleted and memory.deleted_at is not None:
            return None
        return memory

    async def create_memory(self, values: dict[str, Any]) -> Memory:
        memory = Memory(**values)
        self.session.add(memory)
        await self.session.flush()
        await self._index_memory(memory)
        return memory

    async def update_memory(self, memory: Memory, values: dict[str, Any]) -> Memory:
        content_changed = "content" in values or "title" in values or "tags" in values
        for key, value in values.items():
            if hasattr(memory, key):
                setattr(memory, key, value)
        memory.updated_at = utcnow()
        if content_changed:
            await self.session.flush()
            await self._index_memory(memory)
        return memory

    async def delete_memory(self, memory: Memory) -> dict[str, Any]:
        snapshot = memory_snapshot(memory)
        if self.fts_enabled:
            await fts_delete(self.session, memory.id)
        memory_id = memory.id
        space_id = memory.space_id
        await self.session.delete(memory)
        await self.session.flush()
        return {"memory_id": memory_id, "space_id": space_id, "snapshot": snapshot}

    async def _index_memory(self, memory: Memory) -> None:
        if not self.fts_enabled:
            return
        await fts_upsert(
            self.session,
            memory_id=memory.id,
            space_id=memory.space_id,
            title=memory.title,
            content=memory.content,
            tags=list(memory.tags or []),
        )

    async def reindex_memory(self, memory: Memory) -> None:
        await self._index_memory(memory)

    async def _query_conditions(self, filters: MemoryFilters) -> list[Any]:
        conditions: list[Any] = []
        if not filters.include_deleted:
            conditions.append(Memory.deleted_at.is_(None))
        if filters.space_id:
            conditions.append(Memory.space_id == filters.space_id)
        if filters.space_ids is not None:
            if not filters.space_ids:
                return [Memory.id.is_(None)]
            conditions.append(Memory.space_id.in_(filters.space_ids))
        if filters.space_kind:
            space_subquery = select(MemorySpace.id).where(MemorySpace.kind == filters.space_kind)
            conditions.append(Memory.space_id.in_(space_subquery))
        if filters.ids is not None:
            if not filters.ids:
                return [Memory.id.is_(None)]
            conditions.append(Memory.id.in_(filters.ids))
        if filters.memory_types:
            conditions.append(Memory.memory_type.in_(filters.memory_types))
        if filters.categories:
            conditions.append(Memory.category.in_(filters.categories))
        if filters.statuses:
            conditions.append(Memory.status.in_(filters.statuses))
        if filters.source_kinds:
            conditions.append(Memory.source_kind.in_(filters.source_kinds))
        if filters.min_importance is not None:
            conditions.append(Memory.importance >= filters.min_importance)
        if filters.max_importance is not None:
            conditions.append(Memory.importance <= filters.max_importance)
        if filters.min_confidence is not None:
            conditions.append(Memory.confidence >= filters.min_confidence)
        if filters.max_confidence is not None:
            conditions.append(Memory.confidence <= filters.max_confidence)
        if filters.created_after:
            conditions.append(Memory.created_at >= naive(filters.created_after))
        if filters.created_before:
            conditions.append(Memory.created_at <= naive(filters.created_before))
        if filters.occurred_after:
            conditions.append(Memory.occurred_at >= naive(filters.occurred_after))
        if filters.occurred_before:
            conditions.append(Memory.occurred_at <= naive(filters.occurred_before))
        if filters.pinned is not None:
            conditions.append(Memory.pinned.is_(filters.pinned))
        for tag in filters.tags or []:
            pattern = f"%{tag.lower()}%"
            conditions.append(func.lower(cast(Memory.tags, Text)).like(pattern))
        if filters.q:
            q = filters.q.strip()
            if q:
                if self.fts_enabled:
                    matches = await fts_search(self.session, q, limit=2000, match_all=True)
                    if not matches:
                        return [Memory.id.is_(None)]
                    conditions.append(Memory.id.in_([memory_id for memory_id, _ in matches]))
                else:
                    pattern = f"%{q.lower()}%"
                    conditions.append(
                        or_(
                            func.lower(Memory.title).like(pattern),
                            func.lower(Memory.content).like(pattern),
                        )
                    )
        return conditions

    async def list_memories(
        self,
        filters: MemoryFilters,
        *,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> tuple[list[Memory], int]:
        conditions = await self._query_conditions(filters)
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Memory).where(and_(*conditions))
                )
            ).scalar_one()
        )
        sort_columns = {
            "created_at": Memory.created_at,
            "updated_at": Memory.updated_at,
            "occurred_at": Memory.occurred_at,
            "importance": Memory.importance,
            "confidence": Memory.confidence,
            "retention": Memory.retention,
            "access_count": Memory.access_count,
            "reinforcement_count": Memory.reinforcement_count,
            "title": Memory.title,
            "memory_type": Memory.memory_type,
            "category": Memory.category,
            "status": Memory.status,
        }
        column = sort_columns.get(sort_by, Memory.updated_at)
        order = column.desc() if sort_order != "asc" else column.asc()
        offset = max(0, (page - 1) * per_page)
        result = await self.session.execute(
            select(Memory)
            .where(and_(*conditions))
            .order_by(order, Memory.id)
            .offset(offset)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    async def list_memory_ids(
        self,
        filters: MemoryFilters,
        *,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[str]:
        conditions = await self._query_conditions(filters)
        sort_columns = {
            "created_at": Memory.created_at,
            "updated_at": Memory.updated_at,
            "occurred_at": Memory.occurred_at,
            "importance": Memory.importance,
            "confidence": Memory.confidence,
            "retention": Memory.retention,
            "access_count": Memory.access_count,
            "title": Memory.title,
        }
        column = sort_columns.get(sort_by, Memory.updated_at)
        order = column.desc() if sort_order != "asc" else column.asc()
        result = await self.session.execute(
            select(Memory.id).where(and_(*conditions)).order_by(order, Memory.id)
        )
        return [str(row[0]) for row in result.all()]

    async def find_by_hash(self, space_id: str, content_hash: str) -> Memory | None:
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.space_id == space_id,
                Memory.content_hash == content_hash,
                Memory.deleted_at.is_(None),
                Memory.status.in_([MemoryStatus.ACTIVE, MemoryStatus.DISPUTED]),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_memories(self, ids: Sequence[str]) -> dict[str, Memory]:
        if not ids:
            return {}
        result = await self.session.execute(select(Memory).where(Memory.id.in_(list(ids))))
        return {memory.id: memory for memory in result.scalars().all()}

    async def counts_for_memories(self, ids: Sequence[str]) -> dict[str, dict[str, int]]:
        if not ids:
            return {}
        ids = list(ids)
        evidence_rows = await self.session.execute(
            select(MemoryEvidence.memory_id, func.count())
            .where(MemoryEvidence.memory_id.in_(ids))
            .group_by(MemoryEvidence.memory_id)
        )
        relation_rows = await self.session.execute(
            select(MemoryRelation.source_id, MemoryRelation.target_id).where(
                or_(
                    MemoryRelation.source_id.in_(ids),
                    MemoryRelation.target_id.in_(ids),
                )
            )
        )
        counts: dict[str, dict[str, int]] = {
            memory_id: {"evidence_count": 0, "relation_count": 0} for memory_id in ids
        }
        for memory_id, count in evidence_rows.all():
            counts[str(memory_id)]["evidence_count"] = int(count)
        for source_id, target_id in relation_rows.all():
            if str(source_id) in counts:
                counts[str(source_id)]["relation_count"] += 1
            if str(target_id) in counts and str(target_id) != str(source_id):
                counts[str(target_id)]["relation_count"] += 1
        return counts

    async def keyword_search(
        self,
        query: str,
        *,
        space_ids: list[str],
        statuses: list[str] | None = None,
        limit: int = 120,
    ) -> list[tuple[str, float, float]]:
        """Return ``(memory_id, keyword_score, raw_rank)`` best-first."""
        if not space_ids:
            return []
        if self.fts_enabled:
            matches = await fts_search(self.session, query, limit=max(limit * 4, 100))
            if not matches:
                return []
            ids = [memory_id for memory_id, _ in matches]
            result = await self.session.execute(
                select(Memory.id).where(
                    Memory.id.in_(ids),
                    Memory.space_id.in_(space_ids),
                    Memory.deleted_at.is_(None),
                    *([Memory.status.in_(statuses)] if statuses else []),
                )
            )
            allowed = {str(row[0]) for row in result.all()}
            filtered = [(memory_id, rank) for memory_id, rank in matches if memory_id in allowed]
            if not filtered:
                return []
            best = min(rank for _, rank in filtered)
            worst = max(rank for _, rank in filtered)
            span = max(worst - best, 1e-9)
            return [
                (memory_id, 1.0 - ((rank - best) / span) * 0.8, rank)
                for memory_id, rank in filtered[:limit]
            ]

        pattern = f"%{query.lower()}%"
        result = await self.session.execute(
            select(Memory.id)
            .where(
                Memory.space_id.in_(space_ids),
                Memory.deleted_at.is_(None),
                *([Memory.status.in_(statuses)] if statuses else []),
                or_(
                    func.lower(Memory.title).like(pattern),
                    func.lower(Memory.content).like(pattern),
                ),
            )
            .limit(limit)
        )
        return [(str(row[0]), 0.6, 0.0) for row in result.all()]

    async def recent_memories(
        self,
        *,
        space_ids: list[str],
        statuses: list[str] | None = None,
        limit: int = 40,
    ) -> list[Memory]:
        if not space_ids:
            return []
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.space_id.in_(space_ids),
                Memory.deleted_at.is_(None),
                *([Memory.status.in_(statuses)] if statuses else []),
            )
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def all_active_for_decay(self, *, limit: int = 2000) -> list[Memory]:
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.deleted_at.is_(None),
                Memory.status == MemoryStatus.ACTIVE,
                Memory.pinned.is_(False),
            )
            .order_by(Memory.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_memories(self, space_id: str | None = None) -> int:
        statement = select(func.count()).select_from(Memory)
        if space_id:
            statement = statement.where(Memory.space_id == space_id)
        return int((await self.session.execute(statement)).scalar_one())

    async def episodic_unconsolidated(
        self,
        *,
        window_days: int = 30,
        limit: int = 500,
    ) -> list[Memory]:
        from datetime import timedelta

        cutoff = utcnow() - timedelta(days=max(1, window_days))
        consolidated = select(MemoryRelation.target_id).where(
            MemoryRelation.relation_type == RelationType.CONSOLIDATED_FROM
        )
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.deleted_at.is_(None),
                Memory.status == MemoryStatus.ACTIVE,
                Memory.memory_type == MemoryType.EPISODIC,
                Memory.created_at >= cutoff,
                Memory.id.not_in(consolidated),
            )
            .order_by(Memory.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def all_memories(self, *, limit: int = 5000) -> list[Memory]:
        result = await self.session.execute(
            select(Memory)
            .where(Memory.deleted_at.is_(None), Memory.status != MemoryStatus.DELETED)
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # -- Relations ------------------------------------------------------

    async def add_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: str,
        strength: float = 0.5,
        confidence: float = 0.6,
        directed: bool = True,
        attributes: dict[str, Any] | None = None,
    ) -> MemoryRelation:
        if source_id == target_id:
            raise ValueError("a memory cannot relate to itself")
        existing = await self.session.execute(
            select(MemoryRelation).where(
                MemoryRelation.source_id == source_id,
                MemoryRelation.target_id == target_id,
                MemoryRelation.relation_type == relation_type,
            )
        )
        relation = existing.scalar_one_or_none()
        if relation is not None:
            relation.strength = max(relation.strength, strength)
            relation.confidence = max(relation.confidence, confidence)
            if attributes:
                merged = dict(relation.attributes or {})
                merged.update(attributes)
                relation.attributes = merged
            relation.updated_at = utcnow()
            return relation
        relation = MemoryRelation(
            id=new_id(),
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            strength=strength,
            confidence=confidence,
            directed=directed,
            attributes=attributes or {},
        )
        self.session.add(relation)
        await self.session.flush()
        return relation

    async def get_relation(self, relation_id: str) -> MemoryRelation | None:
        return await self.session.get(MemoryRelation, relation_id)

    async def delete_relation(self, relation: MemoryRelation) -> None:
        await self.session.delete(relation)
        await self.session.flush()

    async def list_relations_for(self, memory_id: str) -> list[MemoryRelation]:
        result = await self.session.execute(
            select(MemoryRelation).where(
                or_(
                    MemoryRelation.source_id == memory_id,
                    MemoryRelation.target_id == memory_id,
                )
            )
        )
        return list(result.scalars().all())

    async def list_relations_among(
        self,
        memory_ids: list[str],
        *,
        relation_types: list[str] | None = None,
    ) -> list[MemoryRelation]:
        if not memory_ids:
            return []
        statement = select(MemoryRelation).where(
            MemoryRelation.source_id.in_(memory_ids),
            MemoryRelation.target_id.in_(memory_ids),
        )
        if relation_types:
            statement = statement.where(MemoryRelation.relation_type.in_(relation_types))
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_relations_from(self, memory_id: str) -> list[MemoryRelation]:
        result = await self.session.execute(
            select(MemoryRelation).where(MemoryRelation.source_id == memory_id)
        )
        return list(result.scalars().all())

    async def count_relations(self) -> int:
        return int(
            (
                await self.session.execute(select(func.count()).select_from(MemoryRelation))
            ).scalar_one()
        )

    async def rewire_relations(self, source_id: str, target_id: str) -> None:
        """Move relations from a merged source to its target, skipping conflicts."""
        relations = await self.list_relations_for(source_id)
        for relation in relations:
            other_id = relation.target_id if relation.source_id == source_id else relation.source_id
            if other_id == target_id:
                await self.session.delete(relation)
                continue
            if relation.source_id == source_id:
                relation.source_id = target_id
            else:
                relation.target_id = target_id
            if relation.source_id == relation.target_id:
                await self.session.delete(relation)
        await self.session.flush()

    # -- Evidence -------------------------------------------------------

    async def add_evidence(self, values: dict[str, Any]) -> MemoryEvidence:
        evidence = MemoryEvidence(
            id=values.get("id") or new_id(),
            **{key: value for key, value in values.items() if key != "id"},
        )
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def list_evidence(self, memory_id: str) -> list[MemoryEvidence]:
        result = await self.session.execute(
            select(MemoryEvidence)
            .where(MemoryEvidence.memory_id == memory_id)
            .order_by(MemoryEvidence.created_at.desc())
        )
        return list(result.scalars().all())

    async def move_evidence(self, source_id: str, target_id: str) -> int:
        result = await self.session.execute(
            update(MemoryEvidence)
            .where(MemoryEvidence.memory_id == source_id)
            .values(memory_id=target_id)
        )
        return int(result.rowcount or 0)

    async def count_evidence(self) -> int:
        return int(
            (
                await self.session.execute(select(func.count()).select_from(MemoryEvidence))
            ).scalar_one()
        )

    # -- Grants ---------------------------------------------------------

    async def add_grant(self, values: dict[str, Any]) -> MemoryGrant:
        grant = MemoryGrant(
            id=values.get("id") or new_id(),
            **{key: value for key, value in values.items() if key != "id"},
        )
        self.session.add(grant)
        await self.session.flush()
        return grant

    async def list_grants(
        self,
        *,
        grantee_kind: str | None = None,
        grantee_id: str | None = None,
        space_id: str | None = None,
        memory_id: str | None = None,
    ) -> list[MemoryGrant]:
        statement = select(MemoryGrant)
        if grantee_kind:
            statement = statement.where(MemoryGrant.grantee_kind == grantee_kind)
        if grantee_id:
            statement = statement.where(MemoryGrant.grantee_id == grantee_id)
        if space_id:
            statement = statement.where(MemoryGrant.space_id == space_id)
        if memory_id:
            statement = statement.where(MemoryGrant.memory_id == memory_id)
        result = await self.session.execute(statement.order_by(MemoryGrant.created_at.desc()))
        return list(result.scalars().all())

    async def get_grant(self, grant_id: str) -> MemoryGrant | None:
        return await self.session.get(MemoryGrant, grant_id)

    async def delete_grant(self, grant: MemoryGrant) -> None:
        await self.session.delete(grant)
        await self.session.flush()

    async def granted_space_ids(self, grantee_id: str) -> list[str]:
        now = utcnow()
        result = await self.session.execute(
            select(MemoryGrant.space_id).where(
                MemoryGrant.grantee_kind == "character",
                MemoryGrant.grantee_id == grantee_id,
                MemoryGrant.space_id.is_not(None),
                or_(MemoryGrant.expires_at.is_(None), MemoryGrant.expires_at > now),
            )
        )
        return [str(row[0]) for row in result.all() if row[0]]

    async def granted_memory_ids(self, grantee_id: str) -> list[str]:
        now = utcnow()
        result = await self.session.execute(
            select(MemoryGrant.memory_id).where(
                MemoryGrant.grantee_kind == "character",
                MemoryGrant.grantee_id == grantee_id,
                MemoryGrant.memory_id.is_not(None),
                or_(MemoryGrant.expires_at.is_(None), MemoryGrant.expires_at > now),
            )
        )
        return [str(row[0]) for row in result.all() if row[0]]

    # -- Versions -------------------------------------------------------

    async def add_version(
        self,
        memory: Memory,
        *,
        change_type: str = "update",
        changed_by: str = "system",
    ) -> MemoryVersion:
        return await self.add_version_snapshot(
            memory_id=memory.id,
            version=memory.version,
            snapshot=memory_snapshot(memory),
            change_type=change_type,
            changed_by=changed_by,
        )

    async def add_version_snapshot(
        self,
        *,
        memory_id: str,
        version: int,
        snapshot: dict[str, Any],
        change_type: str = "update",
        changed_by: str = "system",
    ) -> MemoryVersion:
        entry = MemoryVersion(
            id=new_id(),
            memory_id=memory_id,
            version=version,
            snapshot=snapshot,
            change_type=change_type,
            changed_by=changed_by,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_versions(self, memory_id: str) -> list[MemoryVersion]:
        result = await self.session.execute(
            select(MemoryVersion)
            .where(MemoryVersion.memory_id == memory_id)
            .order_by(MemoryVersion.version.desc())
        )
        return list(result.scalars().all())

    async def get_version(self, memory_id: str, version: int) -> MemoryVersion | None:
        result = await self.session.execute(
            select(MemoryVersion).where(
                MemoryVersion.memory_id == memory_id,
                MemoryVersion.version == version,
            )
        )
        return result.scalar_one_or_none()

    # -- Events ---------------------------------------------------------

    async def add_event(
        self,
        *,
        event_type: str,
        memory_id: str | None = None,
        space_id: str | None = None,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> MemoryEvent:
        event = MemoryEvent(
            id=new_id(),
            event_type=event_type,
            memory_id=memory_id,
            space_id=space_id,
            actor=actor,
            payload=payload or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(
        self,
        *,
        memory_id: str | None = None,
        space_id: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEvent]:
        statement = select(MemoryEvent)
        if memory_id:
            statement = statement.where(MemoryEvent.memory_id == memory_id)
        if space_id:
            statement = statement.where(MemoryEvent.space_id == space_id)
        if event_types:
            statement = statement.where(MemoryEvent.event_type.in_(event_types))
        statement = statement.order_by(MemoryEvent.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    # -- Entities -------------------------------------------------------

    async def upsert_entity(
        self,
        *,
        name: str,
        entity_type: str = "topic",
        space_id: str | None = None,
        aliases: list[str] | None = None,
        description: str = "",
    ) -> Entity:
        canonical_name = name.strip().lower()
        result = await self.session.execute(
            select(Entity).where(
                Entity.space_id == space_id,
                Entity.canonical_name == canonical_name,
                Entity.entity_type == entity_type,
            )
        )
        entity = result.scalar_one_or_none()
        if entity is not None:
            if aliases:
                entity.aliases = list(dict.fromkeys([*(entity.aliases or []), *aliases]))
            return entity
        entity = Entity(
            id=new_id(),
            space_id=space_id,
            name=name.strip(),
            canonical_name=canonical_name,
            entity_type=entity_type,
            aliases=list(dict.fromkeys(aliases or [])),
            description=description,
            attributes={},
        )
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def link_entity(self, memory_id: str, entity_id: str, role: str = "mentioned") -> None:
        existing = await self.session.get(MemoryEntity, (memory_id, entity_id))
        if existing is None:
            self.session.add(MemoryEntity(memory_id=memory_id, entity_id=entity_id, role=role))
            await self.session.flush()

    async def entities_for_memory(self, memory_id: str) -> list[Entity]:
        result = await self.session.execute(
            select(Entity)
            .join(MemoryEntity, MemoryEntity.entity_id == Entity.id)
            .where(MemoryEntity.memory_id == memory_id)
        )
        return list(result.scalars().all())

    async def list_entities(
        self, *, space_ids: list[str] | None = None, limit: int = 200
    ) -> list[Entity]:
        statement = select(Entity)
        if space_ids is not None:
            statement = statement.where(Entity.space_id.in_(space_ids))
        statement = statement.order_by(Entity.name).limit(limit)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    # -- Embeddings -----------------------------------------------------

    async def upsert_embedding(
        self,
        *,
        memory_id: str,
        provider: str,
        model: str,
        dimensions: int,
        vector: bytes,
        norm: float,
        content_hash: str,
    ) -> None:
        result = await self.session.execute(
            select(MemoryEmbedding).where(
                MemoryEmbedding.memory_id == memory_id,
                MemoryEmbedding.provider == provider,
                MemoryEmbedding.model == model,
            )
        )
        embedding = result.scalar_one_or_none()
        if embedding is None:
            embedding = MemoryEmbedding(
                id=new_id(),
                memory_id=memory_id,
                provider=provider,
                model=model,
                dimensions=dimensions,
                vector=vector,
                norm=norm,
                content_hash=content_hash,
            )
            self.session.add(embedding)
        else:
            embedding.vector = vector
            embedding.dimensions = dimensions
            embedding.norm = norm
            embedding.content_hash = content_hash
            embedding.updated_at = utcnow()
        await self.session.flush()

    async def get_embedding(
        self,
        memory_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> MemoryEmbedding | None:
        statement = select(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id)
        if provider:
            statement = statement.where(MemoryEmbedding.provider == provider)
        if model:
            statement = statement.where(MemoryEmbedding.model == model)
        result = await self.session.execute(statement.limit(1))
        return result.scalar_one_or_none()

    async def vector_candidates(
        self,
        *,
        provider: str,
        model: str,
        space_ids: list[str],
        statuses: list[str] | None = None,
    ) -> list[tuple[str, bytes, float]]:
        if not space_ids:
            return []
        statement = (
            select(MemoryEmbedding.memory_id, MemoryEmbedding.vector, MemoryEmbedding.norm)
            .join(Memory, Memory.id == MemoryEmbedding.memory_id)
            .where(
                MemoryEmbedding.provider == provider,
                MemoryEmbedding.model == model,
                Memory.space_id.in_(space_ids),
                Memory.deleted_at.is_(None),
            )
        )
        if statuses:
            statement = statement.where(Memory.status.in_(statuses))
        result = await self.session.execute(statement)
        return [(str(row[0]), bytes(row[1]), float(row[2])) for row in result.all()]

    async def delete_embeddings(self, memory_id: str) -> None:
        await self.session.execute(
            delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id)
        )
        await self.session.flush()

    async def count_embeddings(self) -> int:
        return int(
            (
                await self.session.execute(select(func.count()).select_from(MemoryEmbedding))
            ).scalar_one()
        )

    # -- Tasks ----------------------------------------------------------

    async def enqueue_task(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        dedupe_key: str | None = None,
        scheduled_at: datetime | None = None,
        max_attempts: int = 3,
    ) -> AsyncTask | None:
        if dedupe_key:
            existing = await self.session.execute(
                select(AsyncTask)
                .where(
                    AsyncTask.dedupe_key == dedupe_key,
                    AsyncTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
                )
                .limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                return None
        task = AsyncTask(
            id=new_id(),
            task_type=task_type,
            status=TaskStatus.PENDING,
            priority=priority,
            payload=payload,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
            scheduled_at=scheduled_at or utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def claim_task(self) -> AsyncTask | None:
        now = utcnow()
        candidate = await self.session.execute(
            select(AsyncTask.id)
            .where(
                AsyncTask.status == TaskStatus.PENDING,
                or_(AsyncTask.scheduled_at.is_(None), AsyncTask.scheduled_at <= now),
            )
            .order_by(AsyncTask.priority.desc(), AsyncTask.created_at.asc())
            .limit(1)
        )
        task_id = candidate.scalar_one_or_none()
        if task_id is None:
            return None
        result = await self.session.execute(
            update(AsyncTask)
            .where(AsyncTask.id == task_id, AsyncTask.status == TaskStatus.PENDING)
            .values(
                status=TaskStatus.RUNNING,
                attempts=AsyncTask.attempts + 1,
                started_at=now,
                updated_at=now,
            )
            .returning(AsyncTask)
        )
        return result.scalar_one_or_none()

    async def complete_task(
        self,
        task_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        task = await self.session.get(AsyncTask, task_id)
        if task is None:
            return
        task.status = TaskStatus.COMPLETED
        task.result = result or {}
        task.error = ""
        task.finished_at = utcnow()
        task.updated_at = utcnow()

    async def fail_task(self, task_id: str, error: str) -> None:
        task = await self.session.get(AsyncTask, task_id)
        if task is None:
            return
        task.error = error[:4000]
        task.updated_at = utcnow()
        if task.attempts >= task.max_attempts:
            task.status = TaskStatus.FAILED
            task.finished_at = utcnow()
        else:
            task.status = TaskStatus.PENDING
            task.scheduled_at = utcnow()

    async def get_task(self, task_id: str) -> AsyncTask | None:
        return await self.session.get(AsyncTask, task_id)

    async def list_tasks(
        self,
        *,
        statuses: list[str] | None = None,
        task_types: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AsyncTask], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(AsyncTask.status.in_(statuses))
        if task_types:
            conditions.append(AsyncTask.task_type.in_(task_types))
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(AsyncTask).where(and_(*conditions))
                )
            ).scalar_one()
        )
        result = await self.session.execute(
            select(AsyncTask)
            .where(and_(*conditions))
            .order_by(AsyncTask.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def retry_task(self, task_id: str) -> AsyncTask | None:
        task = await self.session.get(AsyncTask, task_id)
        if task is None:
            return None
        task.status = TaskStatus.PENDING
        task.error = ""
        task.attempts = 0
        task.scheduled_at = utcnow()
        task.finished_at = None
        task.updated_at = utcnow()
        return task

    async def recover_running_tasks(self) -> int:
        """Re-queue tasks left running by a previous process."""
        result = await self.session.execute(
            update(AsyncTask)
            .where(AsyncTask.status == TaskStatus.RUNNING)
            .values(
                status=TaskStatus.PENDING,
                error="recovered after restart",
                scheduled_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    # -- Statistics -----------------------------------------------------

    async def count_spaces(self) -> int:
        return int(
            (await self.session.execute(select(func.count()).select_from(MemorySpace))).scalar_one()
        )

    async def stats_counts(self, space_ids: list[str] | None = None) -> dict[str, Any]:
        memory_conditions: list[Any] = [Memory.deleted_at.is_(None)]
        if space_ids is not None:
            if not space_ids:
                return {
                    "total": 0,
                    "by_status": {},
                    "by_type": {},
                    "by_category": {},
                    "by_space_kind": {},
                    "relations": 0,
                    "evidence": 0,
                    "embeddings": 0,
                    "pending_consolidation": 0,
                    "spaces": 0,
                }
            memory_conditions.append(Memory.space_id.in_(space_ids))

        by_status = {
            str(row[0]): int(row[1])
            for row in (
                await self.session.execute(
                    select(Memory.status, func.count())
                    .where(and_(*memory_conditions))
                    .group_by(Memory.status)
                )
            ).all()
        }
        by_type = {
            str(row[0]): int(row[1])
            for row in (
                await self.session.execute(
                    select(Memory.memory_type, func.count())
                    .where(and_(*memory_conditions))
                    .group_by(Memory.memory_type)
                )
            ).all()
        }
        by_category = {
            str(row[0]): int(row[1])
            for row in (
                await self.session.execute(
                    select(Memory.category, func.count())
                    .where(and_(*memory_conditions))
                    .group_by(Memory.category)
                )
            ).all()
        }
        by_space_kind_rows = await self.session.execute(
            select(MemorySpace.kind, func.count(Memory.id))
            .join(Memory, Memory.space_id == MemorySpace.id)
            .where(and_(*memory_conditions))
            .group_by(MemorySpace.kind)
        )
        by_space_kind = {str(row[0]): int(row[1]) for row in by_space_kind_rows.all()}

        relation_conditions: list[Any] = []
        if space_ids is not None:
            relation_conditions.append(Memory.space_id.in_(space_ids))
        relation_count = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(MemoryRelation)
                    .join(Memory, Memory.id == MemoryRelation.source_id)
                    .where(*relation_conditions)
                )
            ).scalar_one()
        )

        consolidated = select(MemoryRelation.target_id).where(
            MemoryRelation.relation_type == RelationType.CONSOLIDATED_FROM
        )
        pending_conditions = [
            Memory.deleted_at.is_(None),
            Memory.status == MemoryStatus.ACTIVE,
            Memory.memory_type == MemoryType.EPISODIC,
            Memory.id.not_in(consolidated),
        ]
        if space_ids is not None:
            pending_conditions.append(Memory.space_id.in_(space_ids))
        pending = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Memory).where(and_(*pending_conditions))
                )
            ).scalar_one()
        )

        active_count = by_status.get(MemoryStatus.ACTIVE, 0)
        return {
            "total": sum(by_status.values()),
            "active": active_count,
            "by_status": by_status,
            "by_type": by_type,
            "by_category": by_category,
            "by_space_kind": by_space_kind,
            "relations": relation_count,
            "evidence": await self.count_evidence(),
            "embeddings": await self.count_embeddings(),
            "pending_consolidation": pending,
            "spaces": len(space_ids) if space_ids is not None else await self.count_spaces(),
        }

    async def status_breakdown(self) -> list[tuple[str, int]]:
        result = await self.session.execute(
            select(Memory.status, func.count())
            .where(Memory.deleted_at.is_(None))
            .group_by(Memory.status)
        )
        return [(str(row[0]), int(row[1])) for row in result.all()]


def source_defaults(source_kind: str) -> tuple[float, float]:
    """Conservative default (importance, confidence) per provenance class."""
    if source_kind == SourceKind.USER_EXPLICIT:
        return 0.65, 0.9
    if source_kind == SourceKind.BEHAVIOR_OBSERVATION:
        return 0.55, 0.7
    if source_kind == SourceKind.MODEL_SUMMARY:
        return 0.55, 0.75
    return 0.45, 0.55


def json_attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}

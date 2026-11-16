"""Graph projection and local expansion for the Memory Explorer."""

from __future__ import annotations

from collections import deque
from typing import Any

from ..config import ConfigManager
from ..db.database import Database
from ..db.models import Memory
from ..db.repository import MemoryFilters, MemoryRepository
from ..domain import AccessMode
from ..errors import NotFoundError
from .access import SpaceAccessService
from .types import Principal


class GraphService:
    def __init__(self, database: Database, config: ConfigManager) -> None:
        self.database = database
        self.config = config
        self.access = SpaceAccessService(config)

    def repository(self, session) -> MemoryRepository:
        return MemoryRepository(session, fts_enabled=self.database.fts_enabled)

    async def graph(
        self,
        principal: Principal,
        *,
        center_id: str | None = None,
        depth: int = 1,
        limit: int = 150,
        filters: MemoryFilters | None = None,
        mode: str = AccessMode.MANAGE,
    ) -> dict[str, Any]:
        filters = filters or MemoryFilters()
        depth = max(1, min(3, int(depth or 1)))
        limit = max(1, min(500, int(limit or 150)))

        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self.access.resolve(repository, principal, mode)
            allowed = set(scope.space_ids)
            if filters.space_id:
                filters.space_ids = [filters.space_id] if filters.space_id in allowed else []
            elif filters.space_ids is None:
                filters.space_ids = list(allowed)
            else:
                filters.space_ids = [item for item in filters.space_ids if item in allowed]
            if not filters.space_ids and not scope.granted_memory_ids:
                return {
                    "nodes": [],
                    "edges": [],
                    "total": 0,
                    "truncated": False,
                    "center_id": center_id,
                }

            filtered_ids = set(await repository.list_memory_ids(filters))
            filtered_ids.update(scope.granted_memory_ids)
            if center_id:
                if center_id not in filtered_ids:
                    raise NotFoundError("memory not found")
                node_ids = await self._expand(repository, center_id, depth, filtered_ids, limit)
                truncated = len(node_ids) >= limit
                total = len(node_ids)
            else:
                ordered = await repository.list_memory_ids(
                    filters, sort_by="importance", sort_order="desc"
                )
                node_ids = ordered[:limit]
                total = len(ordered)
                truncated = total > limit

            memories = await repository.get_memories(node_ids)
            spaces = {
                space.id: space
                for space in await repository.list_spaces(
                    ids=list({memory.space_id for memory in memories.values()})
                )
            }
            nodes = [
                self._node(memory, spaces.get(memory.space_id))
                for memory in memories.values()
                if memory.deleted_at is None
            ]
            relations = await repository.list_relations_among([node["id"] for node in nodes])
            edges = [
                {
                    "id": relation.id,
                    "source": relation.source_id,
                    "target": relation.target_id,
                    "relation_type": relation.relation_type,
                    "strength": relation.strength,
                    "confidence": relation.confidence,
                    "directed": relation.directed,
                    "attributes": relation.attributes or {},
                }
                for relation in relations
            ]
            return {
                "nodes": nodes,
                "edges": edges,
                "total": total,
                "truncated": truncated,
                "center_id": center_id,
                "depth": depth if center_id else None,
            }

    async def _expand(
        self,
        repository: MemoryRepository,
        center_id: str,
        depth: int,
        allowed_ids: set[str],
        limit: int,
    ) -> list[str]:
        seen = {center_id}
        frontier: deque[tuple[str, int]] = deque([(center_id, 0)])
        while frontier and len(seen) < limit:
            current, current_depth = frontier.popleft()
            if current_depth >= depth:
                continue
            relations = await repository.list_relations_for(current)
            for relation in relations:
                other = relation.target_id if relation.source_id == current else relation.source_id
                if other in seen or other not in allowed_ids:
                    continue
                seen.add(other)
                frontier.append((other, current_depth + 1))
                if len(seen) >= limit:
                    break
        return list(seen)

    @staticmethod
    def _node(memory: Memory, space) -> dict[str, Any]:
        return {
            "id": memory.id,
            "label": memory.title or memory.content[:40],
            "title": memory.title,
            "content": memory.content,
            "space_id": memory.space_id,
            "space_kind": space.kind if space else None,
            "space_name": space.name if space else None,
            "memory_type": memory.memory_type,
            "category": memory.category,
            "source_kind": memory.source_kind,
            "status": memory.status,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "retention": memory.retention,
            "reinforcement_count": memory.reinforcement_count,
            "access_count": memory.access_count,
            "pinned": memory.pinned,
            "tags": list(memory.tags or []),
            "occurred_at": memory.occurred_at.isoformat() if memory.occurred_at else None,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        }

    async def path(
        self,
        source_id: str,
        target_id: str,
        principal: Principal,
        *,
        max_depth: int = 6,
        mode: str = AccessMode.MANAGE,
    ) -> dict[str, Any]:
        if source_id == target_id:
            return {"found": True, "node_ids": [source_id], "edge_ids": [], "depth": 0}
        max_depth = max(1, min(10, int(max_depth or 6)))
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self.access.resolve(repository, principal, mode)
            source = await repository.get_memory(source_id)
            target = await repository.get_memory(target_id)
            if (
                source is None
                or target is None
                or not scope.can_read_memory(source)
                or not scope.can_read_memory(target)
            ):
                raise NotFoundError("memory not found")

            parents: dict[str, tuple[str, str]] = {}
            queue: deque[tuple[str, int]] = deque([(source_id, 0)])
            visited = {source_id}
            found = False
            while queue and not found:
                current, current_depth = queue.popleft()
                if current_depth >= max_depth:
                    continue
                relations = await repository.list_relations_for(current)
                for relation in relations:
                    other = (
                        relation.target_id if relation.source_id == current else relation.source_id
                    )
                    if other in visited:
                        continue
                    other_memory = await repository.get_memory(other)
                    if other_memory is None or not scope.can_read_memory(other_memory):
                        continue
                    visited.add(other)
                    parents[other] = (current, relation.id)
                    if other == target_id:
                        found = True
                        break
                    queue.append((other, current_depth + 1))

            if not found:
                return {"found": False, "node_ids": [], "edge_ids": []}
            node_ids = [target_id]
            edge_ids: list[str] = []
            cursor = target_id
            while cursor != source_id:
                previous, edge_id = parents[cursor]
                edge_ids.append(edge_id)
                node_ids.append(previous)
                cursor = previous
            node_ids.reverse()
            edge_ids.reverse()
            return {
                "found": True,
                "node_ids": node_ids,
                "edge_ids": edge_ids,
                "depth": len(edge_ids),
            }

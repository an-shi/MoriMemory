"""Memory maintenance: embedding, consolidation, decay and index rebuilds."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from loguru import logger

from ..config import ConfigManager
from ..db.database import Database
from ..db.fts import fts_reindex_all
from ..db.models import Memory, utcnow
from ..db.repository import MemoryRepository, naive
from ..domain import (
    AccessMode,
    EventType,
    MemoryCategory,
    MemoryStatus,
    MemoryType,
    RelationType,
    SourceKind,
)
from ..embeddings import EmbeddingError, EmbeddingService, pack_vector
from ..errors import NotFoundError
from .core import MemoryEngine
from .types import EvidenceInput, MemoryCandidate, Principal


class MaintenanceService:
    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        embeddings: EmbeddingService,
        engine: MemoryEngine,
    ) -> None:
        self.database = database
        self.config = config
        self.embeddings = embeddings
        self.engine = engine

    def repository(self, session) -> MemoryRepository:
        return MemoryRepository(session, fts_enabled=self.database.fts_enabled)

    async def embed_memory(self, memory_id: str) -> dict[str, Any]:
        if not self.embeddings.enabled:
            raise EmbeddingError("embedding is disabled")
        async with self.database.session() as session:
            repository = self.repository(session)
            memory = await repository.get_memory(memory_id)
            if memory is None:
                raise NotFoundError("memory not found")
            vector = await self.embeddings.embed_text(f"{memory.title}\n{memory.content}")
            await repository.upsert_embedding(
                memory_id=memory.id,
                provider=self.embeddings.provider_name,
                model=self.embeddings.model_name(),
                dimensions=len(vector),
                vector=pack_vector(vector),
                norm=1.0,
                content_hash=memory.content_hash,
            )
            await repository.add_event(
                event_type=EventType.EMBEDDED,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor="system",
                payload={
                    "provider": self.embeddings.provider_name,
                    "model": self.embeddings.model_name(),
                    "dimensions": len(vector),
                },
            )
            return {
                "memory_id": memory.id,
                "dimensions": len(vector),
                "provider": self.embeddings.provider_name,
            }

    async def reembed_all(self, *, limit: int = 1000) -> dict[str, Any]:
        if not self.embeddings.enabled:
            return {"embedded": 0, "failed": 0, "skipped": True}
        async with self.database.session() as session:
            repository = self.repository(session)
            memories = await repository.all_memories(limit=limit)
        embedded = 0
        failed: list[dict[str, str]] = []
        for memory in memories:
            try:
                await self.embed_memory(memory.id)
                embedded += 1
            except Exception as exc:  # noqa: BLE001 - continue batch
                failed.append({"memory_id": memory.id, "error": str(exc)})
        return {"embedded": embedded, "failed": len(failed), "errors": failed[:10]}

    # -- Consolidation --------------------------------------------------

    async def consolidate(
        self,
        principal: Principal | None = None,
        *,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        actor = principal or Principal(user_id="local", role="admin")
        consolidation = self.config.get("memory.consolidation", {}) or {}
        if not bool(consolidation.get("enabled", True)):
            return {"summaries": 0, "groups": 0, "skipped": True}
        min_group = max(2, int(consolidation.get("min_group_size", 3) or 3))
        max_group = max(min_group, int(consolidation.get("max_group_size", 20) or 20))
        window_days = max(1, int(consolidation.get("window_days", 30) or 30))

        async with self.database.session() as session:
            repository = self.repository(session)
            memories = await repository.episodic_unconsolidated(window_days=window_days)
            if space_id:
                memories = [memory for memory in memories if memory.space_id == space_id]

        groups: dict[tuple[str, str], list[Memory]] = defaultdict(list)
        for memory in memories:
            groups[(memory.space_id, memory.category)].append(memory)

        created = 0
        groups_done = 0
        summary_ids: list[str] = []
        for (group_space_id, category), items in groups.items():
            if len(items) < min_group:
                continue
            items = items[:max_group]
            candidate = self._build_summary_candidate(items, category, group_space_id)
            try:
                result = await self.engine.create_memory(
                    candidate,
                    actor,
                    space_id=group_space_id,
                    mode=AccessMode.MANAGE,
                    deduplicate=False,
                    force=True,
                )
            except Exception as exc:  # noqa: BLE001 - one group must not stop the scan
                logger.warning("Consolidation failed for space {}: {}", group_space_id, exc)
                continue
            summary = result.memory
            summary_ids.append(summary.id)
            created += 1
            groups_done += 1
            async with self.database.session() as session:
                repository = self.repository(session)
                for source in items:
                    fresh = await repository.get_memory(source.id)
                    if fresh is None:
                        continue
                    relation = await repository.add_relation(
                        source_id=summary.id,
                        target_id=fresh.id,
                        relation_type=RelationType.CONSOLIDATED_FROM,
                        strength=0.9,
                        confidence=0.8,
                        attributes={"category": category},
                    )
                    fresh.attributes = {
                        **(fresh.attributes or {}),
                        "consolidated_at": utcnow().isoformat(),
                        "consolidated_into": summary.id,
                    }
                    fresh.version += 1
                    await repository.add_version(
                        fresh, change_type="consolidated", changed_by=actor.actor
                    )
                    await repository.add_event(
                        event_type=EventType.CONSOLIDATED,
                        memory_id=fresh.id,
                        space_id=fresh.space_id,
                        actor=actor.actor,
                        payload={"summary_id": summary.id, "relation_id": relation.id},
                    )
        return {
            "summaries": created,
            "groups": groups_done,
            "summary_ids": summary_ids,
            "eligible": len(memories),
        }

    @staticmethod
    def _build_summary_candidate(
        items: list[Memory],
        category: str,
        space_id: str,
    ) -> MemoryCandidate:
        lines = [memory.content for memory in items]
        joined = "；".join(line.strip() for line in lines if line.strip())
        content = f"归纳记忆（{category}）：{joined}"[:20000]
        importance = min(0.95, sum(memory.importance for memory in items) / len(items) + 0.05)
        confidence = min(0.8, sum(memory.confidence for memory in items) / len(items) * 0.9)
        evidence = [
            EvidenceInput(
                source_kind=SourceKind.MODEL_SUMMARY,
                conversation_id=None,
                message_id=None,
                role="summary",
                quote=memory.content[:2000],
                observed_at=memory.occurred_at,
                attributes={"source_memory_id": memory.id},
            )
            for memory in items[:10]
        ]
        return MemoryCandidate(
            content=content,
            title=f"归纳 · {category}",
            memory_type=MemoryType.SEMANTIC,
            category=MemoryCategory.SUMMARY,
            source_kind=SourceKind.MODEL_SUMMARY,
            importance=importance,
            confidence=confidence,
            tags=["summary", category],
            attributes={
                "space_id": space_id,
                "summary_of": [memory.id for memory in items],
                "extractor": "consolidation",
            },
            evidence=evidence,
            rationale="periodic episodic consolidation",
        )

    # -- Decay ----------------------------------------------------------

    @staticmethod
    def compute_retention(memory: Memory, now, config: ConfigManager) -> float:
        if memory.pinned and bool(config.get("memory.decay.pinned_immune", True)):
            return 1.0
        anchor = (
            naive(memory.last_reinforced_at or memory.last_accessed_at or memory.created_at) or now
        )
        age_days = max(0.0, (now - anchor).total_seconds() / 86400)
        base_half_life = max(1.0, float(config.get("memory.decay.half_life_days", 45) or 45))
        half_life = base_half_life * (
            1.0
            + memory.importance * 2.0
            + min(memory.access_count, 20) * 0.05
            + min(memory.reinforcement_count, 20) * 0.12
        )
        retention = math.pow(2, -age_days / max(half_life, 1e-6))
        return max(0.01, min(1.0, retention))

    async def apply_decay(self, *, limit: int = 2000) -> dict[str, Any]:
        if not bool(self.config.get("memory.decay.enabled", True)):
            return {"updated": 0, "forgotten": 0, "skipped": True}
        threshold = float(self.config.get("memory.decay.forget_threshold", 0.08) or 0.08)
        min_access = int(self.config.get("memory.decay.min_access_count", 1) or 0)
        now = naive(utcnow())
        forgotten = 0
        updated = 0
        async with self.database.session() as session:
            repository = self.repository(session)
            memories = await repository.all_active_for_decay(limit=limit)
            for memory in memories:
                retention = self.compute_retention(memory, now, self.config)
                if abs(memory.retention - retention) >= 0.005:
                    memory.retention = retention
                    updated += 1
                if (
                    retention < threshold
                    and memory.access_count < min_access
                    and memory.status == MemoryStatus.ACTIVE
                    and not memory.pinned
                ):
                    memory.status = MemoryStatus.ARCHIVED
                    forgotten += 1
                    memory.version += 1
                    await repository.add_version(
                        memory, change_type="forgotten", changed_by="system"
                    )
                    await repository.add_event(
                        event_type=EventType.FORGOTTEN_SOFT,
                        memory_id=memory.id,
                        space_id=memory.space_id,
                        actor="system",
                        payload={"retention": retention, "threshold": threshold},
                    )
        return {"updated": updated, "forgotten": forgotten, "scanned": len(memories)}

    # -- Index rebuild --------------------------------------------------

    async def rebuild_indexes(self, *, include_embeddings: bool = False) -> dict[str, Any]:
        async with self.database.session() as session:
            if self.database.fts_enabled:
                indexed = await fts_reindex_all(session)
            else:
                indexed = 0
        result: dict[str, Any] = {"fts_indexed": indexed, "fts_enabled": self.database.fts_enabled}
        if include_embeddings:
            result["embeddings"] = await self.reembed_all()
        return result

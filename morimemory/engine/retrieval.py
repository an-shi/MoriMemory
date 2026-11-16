"""Hybrid retrieval: keyword, vector, recency, importance and relations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from ..config import ConfigManager
from ..db.database import Database
from ..db.models import Memory
from ..db.repository import MemoryFilters, MemoryRepository, naive, utcnow
from ..domain import AccessMode, MemoryStatus, RelationType, SourceKind
from ..embeddings import EmbeddingError, EmbeddingService
from ..vector_store import VectorIndex
from .access import AccessScope, SpaceAccessService
from .types import Principal

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_EXPANDABLE_RELATIONS = {
    RelationType.RELATED_TO,
    RelationType.SUPPORTS,
    RelationType.PART_OF,
    RelationType.SAME_ENTITY,
    RelationType.CAUSES,
    RelationType.PRECEDES,
}
_SOURCE_LABELS = {
    SourceKind.USER_EXPLICIT: "用户明确表达",
    SourceKind.AI_INFERENCE: "AI 推断",
    SourceKind.BEHAVIOR_OBSERVATION: "行为观察",
    SourceKind.MODEL_SUMMARY: "模型归纳",
}


def estimate_tokens(value: str) -> int:
    """Approximate model tokens without a tokenizer dependency."""
    if not value:
        return 0
    cjk = len(_CJK_RE.findall(value))
    other = len(value) - cjk
    return max(1, cjk + math.ceil(other / 4))


@dataclass(slots=True)
class RetrievalHit:
    memory: Memory
    score: float
    semantic: float = 0.0
    keyword: float = 0.0
    importance: float = 0.0
    confidence: float = 0.0
    recency: float = 0.0
    strength: float = 0.0
    relation_boost: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.memory.content) + 8


@dataclass(slots=True)
class RetrievalResult:
    query: str
    hits: list[RetrievalHit]
    candidates: int
    tokens_used: int
    token_budget: int
    truncated: bool
    scope: AccessScope


class RetrievalService:
    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        embeddings: EmbeddingService,
        vector_index: VectorIndex,
    ) -> None:
        self.database = database
        self.config = config
        self.embeddings = embeddings
        self.vector_index = vector_index
        self.access = SpaceAccessService(config)

    def weights(self) -> dict[str, float]:
        raw = self.config.get("retrieval.weights", {}) or {}
        defaults = {
            "semantic": 0.35,
            "keyword": 0.20,
            "importance": 0.15,
            "confidence": 0.10,
            "recency": 0.10,
            "strength": 0.10,
        }
        weights = {key: float(raw.get(key, value)) for key, value in defaults.items()}
        total = sum(max(0.0, value) for value in weights.values())
        if total <= 0:
            return defaults
        return {key: max(0.0, value) / total for key, value in weights.items()}

    async def search(
        self,
        query: str,
        principal: Principal,
        *,
        mode: str = AccessMode.RECALL,
        limit: int | None = None,
        token_budget: int | None = None,
        space_id: str | None = None,
        space_kind: str | None = None,
        memory_types: list[str] | None = None,
        categories: list[str] | None = None,
        statuses: list[str] | None = None,
        min_importance: float | None = None,
        min_confidence: float | None = None,
        tags: list[str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        record_access: bool = True,
        expand_relations: bool = True,
    ) -> RetrievalResult:
        query = (query or "").strip()
        limit = max(1, min(int(limit or self.config.get("retrieval.default_limit", 10)), 100))
        budget = max(
            64, int(token_budget or self.config.get("retrieval.default_token_budget", 2000))
        )
        pool = max(limit * 4, int(self.config.get("retrieval.candidate_pool", 120)))
        statuses = statuses or [MemoryStatus.ACTIVE, MemoryStatus.DISPUTED]

        query_vector: list[float] | None = None
        if query and self.embeddings.enabled:
            try:
                query_vector = await self.embeddings.embed_text(query)
            except EmbeddingError as exc:
                logger.debug("Query embedding unavailable; using keyword retrieval: {}", exc)

        async with self.database.session() as session:
            repository = MemoryRepository(session, fts_enabled=self.database.fts_enabled)
            scope = await self.access.resolve(repository, principal, mode)
            allowed_spaces = list(scope.space_ids)
            if space_id:
                allowed_spaces = [space_id] if space_id in allowed_spaces else []
            if not allowed_spaces and not scope.granted_memory_ids:
                return RetrievalResult(query, [], 0, 0, budget, False, scope)

            keyword_hits: dict[str, float] = {}
            if query:
                keyword = await repository.keyword_search(
                    query,
                    space_ids=allowed_spaces,
                    statuses=statuses,
                    limit=pool,
                )
                keyword_hits = {memory_id: score for memory_id, score, _ in keyword}

            semantic_hits: dict[str, float] = {}
            if query_vector is not None:
                vector_results = await self.vector_index.search(
                    session,
                    repository,
                    provider=self.embeddings.provider_name,
                    model=self.embeddings.model_name(),
                    space_ids=allowed_spaces,
                    statuses=statuses,
                    query_vector=query_vector,
                    limit=pool,
                )
                semantic_hits = {memory_id: max(0.0, score) for memory_id, score in vector_results}

            candidate_ids = list(
                dict.fromkeys([*keyword_hits, *semantic_hits, *scope.granted_memory_ids])
            )
            if not candidate_ids and not query:
                recent = await repository.recent_memories(
                    space_ids=allowed_spaces, statuses=statuses, limit=pool
                )
                candidate_ids = [memory.id for memory in recent]

            relation_boosts: dict[str, float] = {}
            if expand_relations and candidate_ids:
                anchors = candidate_ids[: min(6, len(candidate_ids))]
                for anchor_id in anchors:
                    relations = await repository.list_relations_for(anchor_id)
                    for relation in relations:
                        if relation.relation_type not in _EXPANDABLE_RELATIONS:
                            continue
                        other = (
                            relation.target_id
                            if relation.source_id == anchor_id
                            else relation.source_id
                        )
                        relation_boosts[other] = max(
                            relation_boosts.get(other, 0.0),
                            relation.strength * 0.12,
                        )
                candidate_ids.extend(relation_boosts.keys())

            memories = await repository.get_memories(candidate_ids)
            filters = MemoryFilters(
                space_ids=allowed_spaces,
                memory_types=memory_types,
                categories=categories,
                statuses=statuses,
                min_importance=min_importance,
                min_confidence=min_confidence,
                tags=tags,
                created_after=created_after,
                created_before=created_before,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                space_kind=space_kind,
            )
            # ``query`` already selected the candidate seeds. Post-filtering by
            # the query would discard relation-expanded neighbors, which are
            # intentionally allowed to match only via a relationship.
            allowed_filter = await repository.list_memory_ids(filters)
            allowed_filter_set = set(allowed_filter)

            weights = self.weights()
            now = naive(utcnow()) or utcnow()
            half_life = max(1.0, float(self.config.get("retrieval.recency_half_life_days", 30)))

            hits: list[RetrievalHit] = []
            for memory_id, memory in memories.items():
                if memory.deleted_at is not None:
                    continue
                if memory.status not in statuses:
                    continue
                if (
                    memory_id not in allowed_filter_set
                    and memory_id not in scope.granted_memory_ids
                ):
                    continue
                if space_kind:
                    # The ID filter above already enforces the kind; the direct
                    # check keeps individually granted memories consistent.
                    pass

                semantic = semantic_hits.get(memory_id, 0.0)
                keyword = keyword_hits.get(memory_id, 0.0)
                if query:
                    min_semantic = float(
                        self.config.get("retrieval.min_semantic_similarity", 0.12) or 0.0
                    )
                    if (
                        keyword <= 0
                        and semantic < min_semantic
                        and memory_id not in relation_boosts
                    ):
                        continue
                anchor = naive(memory.occurred_at or memory.updated_at or memory.created_at) or now
                age_days = max(0.0, (now - anchor).total_seconds() / 86400)
                recency = math.exp(-math.log(2) * age_days / half_life)
                strength = max(0.0, min(1.0, memory.retention)) * (
                    0.7
                    + 0.3 * min(1.0, math.log1p(max(0, memory.reinforcement_count)) / math.log(11))
                )
                relation_boost = relation_boosts.get(memory_id, 0.0)
                score = (
                    weights["semantic"] * semantic
                    + weights["keyword"] * keyword
                    + weights["importance"] * memory.importance
                    + weights["confidence"] * memory.confidence
                    + weights["recency"] * recency
                    + weights["strength"] * strength
                    + relation_boost
                )
                if memory.pinned:
                    score += 0.05
                score = min(1.0, max(0.0, score))
                reasons: list[str] = []
                if semantic > 0.15:
                    reasons.append("semantic")
                if keyword > 0.15:
                    reasons.append("keyword")
                if memory.reinforcement_count > 1:
                    reasons.append("reinforced")
                if relation_boost > 0:
                    reasons.append("related")
                if recency > 0.5:
                    reasons.append("recent")
                hits.append(
                    RetrievalHit(
                        memory=memory,
                        score=score,
                        semantic=semantic,
                        keyword=keyword,
                        importance=memory.importance,
                        confidence=memory.confidence,
                        recency=recency,
                        strength=strength,
                        relation_boost=relation_boost,
                        reasons=reasons,
                    )
                )

            hits.sort(key=lambda hit: hit.score, reverse=True)
            min_score = float(self.config.get("retrieval.min_score", 0.0) or 0.0)
            hits = [hit for hit in hits if hit.score >= min_score][:limit]

            selected: list[RetrievalHit] = []
            tokens_used = 0
            truncated = False
            for hit in hits:
                cost = hit.token_estimate
                if selected and tokens_used + cost > budget:
                    truncated = True
                    break
                if not selected and cost > budget:
                    # Keep at least one memory but mark the budget as exhausted.
                    selected.append(hit)
                    tokens_used += cost
                    truncated = True
                    break
                selected.append(hit)
                tokens_used += cost

            if record_access and selected:
                for hit in selected:
                    hit.memory.access_count += 1
                    hit.memory.last_accessed_at = utcnow()
                    hit.memory.retention = min(1.0, hit.memory.retention + 0.02)

            return RetrievalResult(
                query=query,
                hits=selected,
                candidates=len(memories),
                tokens_used=tokens_used,
                token_budget=budget,
                truncated=truncated,
                scope=scope,
            )

    async def build_context(
        self,
        query: str,
        principal: Principal,
        **kwargs,
    ) -> tuple[RetrievalResult, str]:
        result = await self.search(query, principal, **kwargs)
        if not result.hits:
            return result, ""
        lines = [
            "以下是从 MoriMemory 检索到的长期记忆，仅作为背景信息使用。",
            "带“AI 推断 / 模型归纳 / 行为观察”标记的内容不是用户明确陈述，不得当作事实复述。",
            "",
        ]
        for hit in result.hits:
            label = _SOURCE_LABELS.get(hit.memory.source_kind, hit.memory.source_kind)
            category = hit.memory.category
            lines.append(f"- [{label} · {category}] {hit.memory.content}")
        return result, "\n".join(lines)

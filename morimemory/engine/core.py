"""The memory engine: lifecycle operations over the repository."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..config import ConfigManager
from ..db.database import Database
from ..db.models import (
    Memory,
    MemoryEvidence,
    MemoryRelation,
    MemorySpace,
    MemoryVersion,
    utcnow,
)
from ..db.repository import MemoryFilters, MemoryRepository, naive
from ..domain import (
    AccessMode,
    EventType,
    MemorySpaceKind,
    MemoryStatus,
    MemoryType,
    PrincipalRole,
    RelationType,
    SourceKind,
    TaskStatus,
    TaskType,
)
from ..embeddings import EmbeddingError, EmbeddingService, pack_vector
from ..errors import AccessDeniedError, ConflictError, NotFoundError, ValidationError
from ..vector_store import VectorIndex
from .access import AccessScope, SpaceAccessService
from .extraction import ExtractionService, normalize_candidate
from .types import (
    ConversationMessage,
    EntityInput,
    EvidenceInput,
    MemoryCandidate,
    Principal,
)

_NEGATIONS = re.compile(r"(不|没|没有|从未|从不|不是|never|not|no longer|don'?t|doesn'?t)", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9_\u3400-\u4dbf\u4e00-\u9fff]")


def content_hash(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).lower().encode("utf-8")).hexdigest()


def token_set(value: str) -> set[str]:
    return set(_TOKEN_RE.findall((value or "").lower()))


def jaccard(left: str, right: str) -> float:
    a = token_set(left)
    b = token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _negated(value: str) -> bool:
    return bool(_NEGATIONS.search(value or ""))


@dataclass(slots=True)
class CreateMemoryResult:
    memory: Memory
    action: str
    relations: list[MemoryRelation] = field(default_factory=list)
    embedding_pending: bool = False
    duplicate_of: str | None = None
    space: MemorySpace | None = None


@dataclass(slots=True)
class MemoryDetails:
    memory: Memory
    space: MemorySpace | None
    evidence: list[MemoryEvidence]
    versions: list[MemoryVersion]
    relations: list[tuple[MemoryRelation, Memory | None]]
    entities: list[Any]
    versions_total: int


@dataclass(slots=True)
class IngestResult:
    task_id: str | None
    candidates: int
    created: int
    reinforced: int
    memories: list[str]
    spaces: dict[str, str]
    async_mode: bool


class MemoryEngine:
    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        embeddings: EmbeddingService,
        vector_index: VectorIndex,
        extraction: ExtractionService,
    ) -> None:
        self.database = database
        self.config = config
        self.embeddings = embeddings
        self.vector_index = vector_index
        self.extraction = extraction
        self.access = SpaceAccessService(config)

    def repository(self, session) -> MemoryRepository:
        return MemoryRepository(session, fts_enabled=self.database.fts_enabled)

    async def _scope(
        self,
        repository: MemoryRepository,
        principal: Principal,
        mode: str,
    ) -> AccessScope:
        return await self.access.resolve(repository, principal, mode)

    # -- Spaces ---------------------------------------------------------

    async def list_spaces(
        self,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            spaces = await repository.list_spaces(ids=scope.space_ids, kinds=kinds)
            result = []
            for space in spaces:
                count = await repository.count_memories(space.id)
                result.append(
                    {
                        "space": space,
                        "memory_count": count,
                        "writable": scope.can_write_space(space.id),
                    }
                )
            return result

    async def create_space(self, values: dict[str, Any], principal: Principal) -> MemorySpace:
        kind = str(values.get("kind", "")).strip().lower()
        if kind not in {item.value for item in MemorySpaceKind}:
            raise ValidationError(f"invalid space kind: {kind}")
        user_id = str(values.get("user_id") or "").strip() or None
        character_id = str(values.get("character_id") or "").strip() or None
        conversation_id = str(values.get("conversation_id") or "").strip() or None
        if kind == MemorySpaceKind.USER and not user_id:
            user_id = principal.user_id
        if kind == MemorySpaceKind.CHARACTER and not character_id:
            raise ValidationError("character_id is required for a character space")
        if kind == MemorySpaceKind.CONVERSATION and not conversation_id:
            raise ValidationError("conversation_id is required for a conversation space")
        if kind == MemorySpaceKind.USER and user_id != principal.user_id:
            raise AccessDeniedError("cannot create a user space for another user")

        async with self.database.session() as session:
            repository = self.repository(session)
            key = repository.space_key(
                kind,
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
            )
            if await repository.get_space_by_key(key):
                raise ConflictError("a space with the same scope already exists")
            return await repository.create_space(
                {
                    "kind": kind,
                    "key": key,
                    "name": str(values.get("name") or ""),
                    "description": str(values.get("description") or ""),
                    "user_id": user_id,
                    "character_id": character_id,
                    "conversation_id": conversation_id,
                    "status": "active",
                    "settings": values.get("settings") or {},
                }
            )

    async def update_space(
        self,
        space_id: str,
        values: dict[str, Any],
        principal: Principal,
    ) -> MemorySpace:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, AccessMode.MANAGE)
            space = await repository.get_space(space_id)
            if space is None:
                raise NotFoundError("memory space not found")
            if not scope.can_write_space(space.id) and principal.role != PrincipalRole.ADMIN:
                raise AccessDeniedError("no write access to this memory space")
            allowed = {"name", "description", "status", "settings"}
            for key, value in values.items():
                if key in allowed:
                    setattr(space, key, value)
            space.updated_at = utcnow()
            return space

    async def delete_space(self, space_id: str, principal: Principal) -> None:
        async with self.database.session() as session:
            repository = self.repository(session)
            space = await repository.get_space(space_id)
            if space is None:
                raise NotFoundError("memory space not found")
            if principal.role != PrincipalRole.ADMIN or (
                space.kind == MemorySpaceKind.USER and space.user_id != principal.user_id
            ):
                raise AccessDeniedError("only the owning administrator may delete a space")
            await repository.delete_space(space)

    # -- Grants ---------------------------------------------------------

    async def create_grant(self, values: dict[str, Any], principal: Principal) -> dict[str, Any]:
        grantee_kind = str(values.get("grantee_kind", "character"))
        grantee_id = str(values.get("grantee_id", "")).strip()
        permission = str(values.get("permission", "read"))
        space_id = str(values.get("space_id") or "").strip() or None
        memory_id = str(values.get("memory_id") or "").strip() or None
        if not grantee_id:
            raise ValidationError("grantee_id is required")
        if space_id is None and memory_id is None:
            raise ValidationError("space_id or memory_id is required")
        if permission not in {"read", "write", "read_write"}:
            raise ValidationError("invalid permission")

        async with self.database.session() as session:
            repository = self.repository(session)
            if memory_id:
                memory = await repository.get_memory(memory_id)
                if memory is None:
                    raise NotFoundError("memory not found")
                space = await repository.get_space(memory.space_id)
            else:
                memory = None
                space = await repository.get_space(space_id or "")
            if space is None:
                raise NotFoundError("memory space not found")
            await self._assert_space_writable(repository, space, principal, AccessMode.MANAGE)
            grant = await repository.add_grant(
                {
                    "space_id": space.id if memory is None else None,
                    "memory_id": memory.id if memory else None,
                    "grantee_kind": grantee_kind,
                    "grantee_id": grantee_id,
                    "permission": permission,
                    "granted_by": principal.user_id,
                    "expires_at": values.get("expires_at"),
                    "attributes": values.get("attributes") or {},
                }
            )
            await repository.add_event(
                event_type=EventType.SHARED,
                memory_id=memory.id if memory else None,
                space_id=space.id,
                actor=principal.actor,
                payload={
                    "grant_id": grant.id,
                    "grantee_kind": grantee_kind,
                    "grantee_id": grantee_id,
                    "permission": permission,
                },
            )
            return {"grant": grant, "space": space, "memory": memory}

    async def list_grants(
        self,
        principal: Principal,
        *,
        space_id: str | None = None,
        grantee_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, AccessMode.MANAGE)
            grants = await repository.list_grants(
                space_id=space_id,
                grantee_id=grantee_id,
                grantee_kind="character" if grantee_id else None,
            )
            visible = []
            for grant in grants:
                if grant.space_id and grant.space_id in scope.space_ids:
                    visible.append(grant)
                elif grant.memory_id:
                    memory = await repository.get_memory(grant.memory_id)
                    if memory is not None and scope.can_read_memory(memory):
                        visible.append(grant)
            return visible

    async def revoke_grant(self, grant_id: str, principal: Principal) -> None:
        async with self.database.session() as session:
            repository = self.repository(session)
            grant = await repository.get_grant(grant_id)
            if grant is None:
                raise NotFoundError("grant not found")
            if principal.role != PrincipalRole.ADMIN:
                raise AccessDeniedError("only an administrator may revoke a grant")
            await repository.delete_grant(grant)
            await repository.add_event(
                event_type=EventType.UNSHARED,
                memory_id=grant.memory_id,
                space_id=grant.space_id,
                actor=principal.actor,
                payload={"grant_id": grant.id, "grantee_id": grant.grantee_id},
            )

    # -- Memory creation ------------------------------------------------

    async def create_memory(
        self,
        candidate: MemoryCandidate | dict[str, Any],
        principal: Principal,
        *,
        space_id: str | None = None,
        mode: str = AccessMode.MANAGE,
        deduplicate: bool = True,
        force: bool = False,
    ) -> CreateMemoryResult:
        if isinstance(candidate, dict):
            candidate = MemoryCandidate.model_validate(candidate)
        candidate = normalize_candidate(candidate, self.config.get("memory", {}) or {})
        embedding: list[float] | None = None
        if self.embeddings.enabled:
            try:
                embedding = await self.embeddings.embed_text(
                    f"{candidate.title}\n{candidate.content}"
                )
            except EmbeddingError as exc:
                logger.warning("Memory will be stored without an embedding: {}", exc)

        async with self.database.session() as session:
            repository = self.repository(session)
            space = await self._resolve_space(
                repository, principal, candidate, space_id=space_id, mode=mode
            )
            if not force and deduplicate:
                existing = await self._find_duplicate(repository, space, candidate, embedding)
                if existing is not None:
                    relations = await self._reinforce(
                        repository,
                        existing,
                        candidate,
                        principal,
                        relation_seed=embedding,
                    )
                    return CreateMemoryResult(
                        memory=existing,
                        action="reinforced",
                        relations=relations,
                        duplicate_of=existing.id,
                        space=space,
                    )

            similar: list[tuple[Memory, float]] = []
            contradictions: list[Memory] = []
            if not force:
                similar, contradictions = await self._find_related_candidates(
                    repository, space, candidate, embedding
                )

            now = utcnow()
            values = {
                "id": None,
                "space_id": space.id,
                "title": candidate.title,
                "content": candidate.content,
                "memory_type": candidate.memory_type,
                "category": candidate.category,
                "source_kind": candidate.source_kind,
                "status": MemoryStatus.ACTIVE,
                "importance": candidate.importance if candidate.importance is not None else 0.5,
                "confidence": candidate.confidence if candidate.confidence is not None else 0.6,
                "retention": 1.0,
                "access_count": 0,
                "reinforcement_count": 1,
                "contradiction_count": 0,
                "pinned": bool(candidate.attributes.get("pinned", False)),
                "tags": candidate.tags,
                "attributes": candidate.attributes,
                "language": "zh"
                if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", candidate.content)
                else "en",
                "content_hash": content_hash(candidate.content),
                "occurred_at": naive(candidate.occurred_at),
                "last_accessed_at": None,
                "last_reinforced_at": now,
                "expires_at": naive(candidate.attributes.get("expires_at")),
                "version": 1,
            }
            from ..db.models import new_id

            values["id"] = new_id()
            memory = await repository.create_memory(values)
            await self._attach_evidence(repository, memory, candidate, principal)
            await repository.add_version(memory, change_type="create", changed_by=principal.actor)
            await self._link_entities(repository, memory, candidate.entities)
            await repository.add_event(
                event_type=EventType.CREATED,
                memory_id=memory.id,
                space_id=space.id,
                actor=principal.actor,
                payload={
                    "source_kind": candidate.source_kind,
                    "category": candidate.category,
                    "memory_type": candidate.memory_type,
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                },
            )

            relations: list[MemoryRelation] = []
            if embedding is not None:
                await repository.upsert_embedding(
                    memory_id=memory.id,
                    provider=self.embeddings.provider_name,
                    model=self.embeddings.model_name(),
                    dimensions=len(embedding),
                    vector=pack_vector(embedding),
                    norm=1.0,
                    content_hash=memory.content_hash,
                )
            elif self.embeddings.enabled:
                await repository.enqueue_task(
                    TaskType.EMBED_MEMORY,
                    {"memory_id": memory.id},
                    dedupe_key=f"embed:{memory.id}",
                    max_attempts=int(self.config.get("tasks.max_attempts", 3) or 3),
                )

            for other, score in similar:
                relation = await repository.add_relation(
                    source_id=memory.id,
                    target_id=other.id,
                    relation_type=RelationType.RELATED_TO,
                    strength=min(0.95, score),
                    confidence=min(memory.confidence, other.confidence),
                    attributes={"detector": "vector_or_lexical", "similarity": score},
                )
                relations.append(relation)
                await repository.add_event(
                    event_type=EventType.RELATION_CREATED,
                    memory_id=memory.id,
                    space_id=space.id,
                    actor=principal.actor,
                    payload={
                        "relation_id": relation.id,
                        "target": other.id,
                        "type": relation.relation_type,
                    },
                )

            if candidate.contradicts_memory_id:
                target = await repository.get_memory(candidate.contradicts_memory_id)
                if target is not None and target.space_id == space.id:
                    contradictions.append(target)

            for target in {item.id: item for item in contradictions}.values():
                relation = await repository.add_relation(
                    source_id=memory.id,
                    target_id=target.id,
                    relation_type=RelationType.CONTRADICTS,
                    strength=0.75,
                    confidence=0.7,
                    attributes={"reason": "slot_or_negation_mismatch"},
                )
                relations.append(relation)
                target.contradiction_count += 1
                if target.status == MemoryStatus.ACTIVE:
                    target.status = MemoryStatus.DISPUTED
                target.confidence = max(0.05, target.confidence - 0.1)
                target.version += 1
                await repository.add_version(
                    target, change_type="contradicted", changed_by=principal.actor
                )
                await repository.add_event(
                    event_type=EventType.CONTRADICTED,
                    memory_id=target.id,
                    space_id=target.space_id,
                    actor=principal.actor,
                    payload={"by": memory.id, "relation_id": relation.id},
                )

            return CreateMemoryResult(
                memory=memory,
                action="created",
                relations=relations,
                embedding_pending=embedding is None and self.embeddings.enabled,
                space=space,
            )

    async def _resolve_space(
        self,
        repository: MemoryRepository,
        principal: Principal,
        candidate: MemoryCandidate,
        *,
        space_id: str | None,
        mode: str,
    ) -> MemorySpace:
        if space_id:
            space = await repository.get_space(space_id)
            if space is None:
                raise NotFoundError("memory space not found")
            await self._assert_space_writable(repository, space, principal, mode)
            return space

        scope = candidate.scope
        if scope == "auto":
            if candidate.memory_type in {MemoryType.EPISODIC, MemoryType.SHORT_TERM}:
                scope = "conversation" if principal.conversation_id else "user"
            else:
                scope = "user"

        if scope == "user":
            space = await repository.ensure_space(MemorySpaceKind.USER, user_id=principal.user_id)
        elif scope == "character":
            if not principal.character_id:
                raise ValidationError("character_id is required for character-scoped memory")
            space = await repository.ensure_space(
                MemorySpaceKind.CHARACTER, character_id=principal.character_id
            )
        elif scope == "conversation":
            if not principal.conversation_id:
                if principal.character_id:
                    space = await repository.ensure_space(
                        MemorySpaceKind.CHARACTER, character_id=principal.character_id
                    )
                else:
                    raise ValidationError(
                        "conversation_id is required for conversation-scoped memory"
                    )
            else:
                space = await repository.ensure_space(
                    MemorySpaceKind.CONVERSATION,
                    conversation_id=principal.conversation_id,
                    user_id=principal.user_id,
                    character_id=principal.character_id,
                )
        else:
            raise ValidationError(f"unknown memory scope: {scope}")

        await self._assert_space_writable(repository, space, principal, mode)
        return space

    async def _assert_space_writable(
        self,
        repository: MemoryRepository,
        space: MemorySpace,
        principal: Principal,
        mode: str,
    ) -> None:
        is_admin_manage = principal.role == PrincipalRole.ADMIN and mode == AccessMode.MANAGE
        if space.kind == MemorySpaceKind.USER:
            if space.user_id != principal.user_id:
                raise AccessDeniedError("no write access to this user space")
            return
        if space.kind == MemorySpaceKind.CHARACTER:
            if is_admin_manage and bool(
                self.config.get("security.allow_character_management", True)
            ):
                return
            if not principal.character_id or space.character_id != principal.character_id:
                raise AccessDeniedError("no write access to this character space")
            return
        if space.kind == MemorySpaceKind.CONVERSATION:
            if is_admin_manage:
                return
            if principal.conversation_id and space.conversation_id == principal.conversation_id:
                return
            if space.user_id == principal.user_id:
                return
            raise AccessDeniedError("no write access to this conversation space")
        raise AccessDeniedError("unsupported memory space")

    async def _find_duplicate(
        self,
        repository: MemoryRepository,
        space: MemorySpace,
        candidate: MemoryCandidate,
        embedding: list[float] | None,
    ) -> Memory | None:
        exact = await repository.find_by_hash(space.id, content_hash(candidate.content))
        if exact is not None:
            return exact

        duplicate_threshold = float(
            self.config.get("memory.dedup.duplicate_threshold", 0.92) or 0.92
        )
        if embedding is not None:
            matches = await self.vector_index.search(
                repository.session,
                repository,
                provider=self.embeddings.provider_name,
                model=self.embeddings.model_name(),
                space_ids=[space.id],
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.DISPUTED],
                query_vector=embedding,
                limit=5,
            )
            for memory_id, score in matches:
                if score >= duplicate_threshold:
                    memory = await repository.get_memory(memory_id)
                    if memory is not None:
                        return memory
            return None

        # No embedding backend: exact hash plus high lexical overlap.
        keyword = await repository.keyword_search(
            candidate.content,
            space_ids=[space.id],
            statuses=[MemoryStatus.ACTIVE, MemoryStatus.DISPUTED],
            limit=3,
        )
        for memory_id, _score, _rank in keyword:
            memory = await repository.get_memory(memory_id)
            if memory is not None and jaccard(memory.content, candidate.content) >= 0.95:
                return memory
        return None

    async def _find_related_candidates(
        self,
        repository: MemoryRepository,
        space: MemorySpace,
        candidate: MemoryCandidate,
        embedding: list[float] | None,
    ) -> tuple[list[tuple[Memory, float]], list[Memory]]:
        similar_threshold = float(self.config.get("memory.dedup.similar_threshold", 0.82) or 0.82)
        contradiction_threshold = float(
            self.config.get("memory.dedup.contradiction_similarity", 0.62) or 0.62
        )
        scored: list[tuple[Memory, float]] = []

        if embedding is not None:
            matches = await self.vector_index.search(
                repository.session,
                repository,
                provider=self.embeddings.provider_name,
                model=self.embeddings.model_name(),
                space_ids=[space.id],
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.DISPUTED],
                query_vector=embedding,
                limit=12,
            )
            memories = await repository.get_memories([memory_id for memory_id, _ in matches])
            scored = [
                (memories[memory_id], score)
                for memory_id, score in matches
                if memory_id in memories
            ]
        else:
            keyword = await repository.keyword_search(
                candidate.content,
                space_ids=[space.id],
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.DISPUTED],
                limit=12,
            )
            memories = await repository.get_memories([memory_id for memory_id, _, _ in keyword])
            scored = [
                (memories[memory_id], jaccard(memories[memory_id].content, candidate.content))
                for memory_id, _score, _rank in keyword
                if memory_id in memories
            ]

        similar: list[tuple[Memory, float]] = []
        contradictions: list[Memory] = []
        for memory, score in scored:
            if self._is_contradiction(memory, candidate, score, contradiction_threshold):
                contradictions.append(memory)
            elif score >= similar_threshold:
                similar.append((memory, score))
        return similar[:5], contradictions[:3]

    @staticmethod
    def _is_contradiction(
        memory: Memory,
        candidate: MemoryCandidate,
        score: float,
        threshold: float,
    ) -> bool:
        if candidate.contradicts_memory_id == memory.id:
            return True
        new_slots = {
            key: value for key, value in (candidate.attributes.get("slots") or {}).items() if value
        }
        old_slots = {
            key: value for key, value in (memory.attributes.get("slots") or {}).items() if value
        }
        for key in set(new_slots) & set(old_slots):
            if str(new_slots[key]).strip().lower() != str(old_slots[key]).strip().lower():
                return True
        if score >= threshold and jaccard(memory.content, candidate.content) >= 0.55:
            if _negated(memory.content) != _negated(candidate.content):
                return True
        return False

    async def _reinforce(
        self,
        repository: MemoryRepository,
        memory: Memory,
        candidate: MemoryCandidate,
        principal: Principal,
        *,
        relation_seed: list[float] | None = None,
    ) -> list[MemoryRelation]:
        await self._attach_evidence(repository, memory, candidate, principal)
        memory.reinforcement_count += 1
        memory.retention = 1.0
        memory.last_reinforced_at = utcnow()
        memory.importance = min(1.0, max(memory.importance, candidate.importance or 0.0) + 0.02)
        cap = 0.98 if memory.source_kind == SourceKind.USER_EXPLICIT else 0.85
        memory.confidence = min(cap, memory.confidence + (1.0 - memory.confidence) * 0.08)
        memory.version += 1
        await repository.add_version(memory, change_type="reinforce", changed_by=principal.actor)
        await repository.add_event(
            event_type=EventType.REINFORCED,
            memory_id=memory.id,
            space_id=memory.space_id,
            actor=principal.actor,
            payload={
                "reinforcement_count": memory.reinforcement_count,
                "evidence": [evidence.quote for evidence in candidate.evidence][:3],
            },
        )
        return []

    async def _attach_evidence(
        self,
        repository: MemoryRepository,
        memory: Memory,
        candidate: MemoryCandidate,
        principal: Principal,
    ) -> None:
        evidence_items = candidate.evidence or [
            EvidenceInput(
                source_kind=candidate.source_kind,
                conversation_id=principal.conversation_id,
                role="user" if candidate.source_kind == SourceKind.USER_EXPLICIT else "system",
                quote=candidate.content[:2000],
                observed_at=candidate.occurred_at,
                attributes={"synthetic": True, "origin": "manual_or_summary"},
            )
        ]
        for evidence in evidence_items:
            await repository.add_evidence(
                {
                    "memory_id": memory.id,
                    "source_kind": evidence.source_kind or candidate.source_kind,
                    "conversation_id": evidence.conversation_id or principal.conversation_id,
                    "message_id": evidence.message_id,
                    "role": evidence.role,
                    "quote": evidence.quote or candidate.content[:2000],
                    "observed_at": naive(evidence.observed_at or candidate.occurred_at),
                    "attributes": {
                        **(evidence.attributes or {}),
                        "extractor": candidate.attributes.get("extractor", "manual"),
                    },
                }
            )

    async def _link_entities(
        self,
        repository: MemoryRepository,
        memory: Memory,
        entities: list[EntityInput],
    ) -> None:
        for entity in entities:
            record = await repository.upsert_entity(
                name=entity.name,
                entity_type=entity.entity_type,
                space_id=memory.space_id,
            )
            await repository.link_entity(memory.id, record.id, role=entity.role)

    # -- Conversation ingestion ----------------------------------------

    async def ingest_conversation(
        self,
        messages: list[ConversationMessage],
        principal: Principal,
        *,
        conversation_id: str | None = None,
        character_id: str | None = None,
        async_mode: bool = True,
    ) -> IngestResult:
        effective = principal.model_copy(
            update={
                "conversation_id": conversation_id or principal.conversation_id,
                "character_id": character_id or principal.character_id,
            }
        )
        async with self.database.session() as session:
            repository = self.repository(session)
            user_space = await repository.ensure_space(
                MemorySpaceKind.USER, user_id=effective.user_id
            )
            spaces = {"user": user_space.id}
            if effective.character_id:
                character_space = await repository.ensure_space(
                    MemorySpaceKind.CHARACTER, character_id=effective.character_id
                )
                spaces["character"] = character_space.id
            if effective.conversation_id:
                conversation_space = await repository.ensure_space(
                    MemorySpaceKind.CONVERSATION,
                    conversation_id=effective.conversation_id,
                    user_id=effective.user_id,
                    character_id=effective.character_id,
                )
                spaces["conversation"] = conversation_space.id

            if async_mode:
                last_id = next((message.id for message in reversed(messages) if message.id), None)
                dedupe_key = (
                    f"ingest:{effective.conversation_id or 'none'}:{last_id}" if last_id else None
                )
                task = await repository.enqueue_task(
                    TaskType.EXTRACT_CONVERSATION,
                    {
                        "messages": [message.model_dump(mode="json") for message in messages],
                        "principal": effective.model_dump(),
                    },
                    priority=10,
                    dedupe_key=dedupe_key,
                    max_attempts=int(self.config.get("tasks.max_attempts", 3) or 3),
                )
                return IngestResult(
                    task_id=task.id if task else None,
                    candidates=0,
                    created=0,
                    reinforced=0,
                    memories=[],
                    spaces=spaces,
                    async_mode=True,
                )

        candidates = await self.extraction.extract(messages, effective)
        created = 0
        reinforced = 0
        memory_ids: list[str] = []
        for candidate in candidates:
            result = await self.create_memory(
                candidate,
                effective,
                mode=AccessMode.RECALL,
                deduplicate=True,
            )
            memory_ids.append(result.memory.id)
            if result.action == "reinforced":
                reinforced += 1
            else:
                created += 1
        return IngestResult(
            task_id=None,
            candidates=len(candidates),
            created=created,
            reinforced=reinforced,
            memories=memory_ids,
            spaces=spaces,
            async_mode=False,
        )

    async def process_ingest_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = [
            ConversationMessage.model_validate(item) for item in payload.get("messages", [])
        ]
        principal = Principal.model_validate(payload.get("principal", {}))
        result = await self.ingest_conversation(messages, principal, async_mode=False)
        return {
            "candidates": result.candidates,
            "created": result.created,
            "reinforced": result.reinforced,
            "memories": result.memories,
            "spaces": result.spaces,
        }

    # -- Reads ----------------------------------------------------------

    async def get_memory_details(
        self,
        memory_id: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> MemoryDetails:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            space = await repository.get_space(memory.space_id)
            evidence = await repository.list_evidence(memory.id)
            versions = await repository.list_versions(memory.id)
            relations = await repository.list_relations_for(memory.id)
            related_ids = {
                relation.target_id if relation.source_id == memory.id else relation.source_id
                for relation in relations
            }
            related = await repository.get_memories(list(related_ids))
            relation_pairs = []
            for relation in relations:
                other_id = (
                    relation.target_id if relation.source_id == memory.id else relation.source_id
                )
                other = related.get(other_id)
                if other is not None and scope.can_read_memory(other):
                    relation_pairs.append((relation, other))
            entities = await repository.entities_for_memory(memory.id)
            return MemoryDetails(
                memory=memory,
                space=space,
                evidence=evidence,
                versions=versions,
                relations=relation_pairs,
                entities=entities,
                versions_total=len(versions),
            )

    async def list_memories(
        self,
        filters: MemoryFilters,
        principal: Principal,
        *,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        mode: str = AccessMode.MANAGE,
    ) -> tuple[list[Memory], int, AccessScope, dict[str, MemorySpace]]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            if filters.space_id:
                filters.space_ids = (
                    [filters.space_id] if filters.space_id in scope.space_ids else []
                )
            elif filters.space_ids is None:
                filters.space_ids = scope.space_ids
            else:
                filters.space_ids = [item for item in filters.space_ids if item in scope.space_ids]
            memories, total = await repository.list_memories(
                filters,
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            spaces = {
                space.id: space
                for space in await repository.list_spaces(ids=list({m.space_id for m in memories}))
            }
            return memories, total, scope, spaces

    async def list_memory_ids(
        self,
        filters: MemoryFilters,
        principal: Principal,
        *,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        mode: str = AccessMode.MANAGE,
    ) -> tuple[list[str], AccessScope]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            if filters.space_id:
                filters.space_ids = (
                    [filters.space_id] if filters.space_id in scope.space_ids else []
                )
            elif filters.space_ids is None:
                filters.space_ids = scope.space_ids
            else:
                filters.space_ids = [item for item in filters.space_ids if item in scope.space_ids]
            return (
                await repository.list_memory_ids(filters, sort_by=sort_by, sort_order=sort_order),
                scope,
            )

    # -- Mutations ------------------------------------------------------

    async def update_memory(
        self,
        memory_id: str,
        values: dict[str, Any],
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> Memory:
        new_embedding: list[float] | None = None
        content_value = values.get("content")
        if content_value and self.embeddings.enabled:
            try:
                title_value = values.get("title", "")
                new_embedding = await self.embeddings.embed_text(f"{title_value}\n{content_value}")
            except EmbeddingError as exc:
                logger.warning("Updated memory could not be embedded inline: {}", exc)

        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(memory.space_id):
                raise AccessDeniedError("no write access to this memory")

            allowed = {
                "title",
                "content",
                "memory_type",
                "category",
                "source_kind",
                "importance",
                "confidence",
                "retention",
                "pinned",
                "tags",
                "attributes",
                "occurred_at",
                "expires_at",
                "status",
            }
            old_status = memory.status
            changed: dict[str, Any] = {}
            content_changed = False
            for key, value in values.items():
                if key not in allowed:
                    continue
                if key == "status" and value not in {
                    MemoryStatus.ACTIVE,
                    MemoryStatus.ARCHIVED,
                    MemoryStatus.SUPERSEDED,
                    MemoryStatus.DISPUTED,
                }:
                    raise ValidationError("invalid memory status")
                if key == "attributes" and isinstance(value, dict):
                    merged = dict(memory.attributes or {})
                    merged.update(value)
                    value = merged
                if key == "occurred_at":
                    value = naive(value)
                if key == "expires_at":
                    value = naive(value)
                if key == "tags":
                    value = list(
                        dict.fromkeys(
                            str(tag).strip().lower()[:64] for tag in value or [] if str(tag).strip()
                        )
                    )
                if getattr(memory, key) != value:
                    changed[key] = value
                    if key in {"title", "content", "tags"}:
                        content_changed = True
                    setattr(memory, key, value)
            if not changed:
                return memory

            if "content" in changed:
                memory.content_hash = content_hash(memory.content)
            memory.version += 1
            memory.updated_at = utcnow()
            await repository.update_memory(memory, changed)
            await repository.add_version(memory, change_type="update", changed_by=principal.actor)

            event_type = EventType.UPDATED
            if "status" in changed:
                if memory.status == MemoryStatus.ARCHIVED:
                    event_type = EventType.ARCHIVED
                elif old_status == MemoryStatus.ARCHIVED and memory.status == MemoryStatus.ACTIVE:
                    event_type = EventType.RESTORED
            await repository.add_event(
                event_type=event_type,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor=principal.actor,
                payload={"fields": sorted(changed.keys())},
            )

            if content_changed:
                if new_embedding is not None:
                    await repository.upsert_embedding(
                        memory_id=memory.id,
                        provider=self.embeddings.provider_name,
                        model=self.embeddings.model_name(),
                        dimensions=len(new_embedding),
                        vector=pack_vector(new_embedding),
                        norm=1.0,
                        content_hash=memory.content_hash,
                    )
                    await repository.add_event(
                        event_type=EventType.EMBEDDED,
                        memory_id=memory.id,
                        space_id=memory.space_id,
                        actor="system",
                        payload={"provider": self.embeddings.provider_name},
                    )
                else:
                    await repository.delete_embeddings(memory.id)
                    if self.embeddings.enabled:
                        await repository.enqueue_task(
                            TaskType.EMBED_MEMORY,
                            {"memory_id": memory.id},
                            dedupe_key=f"embed:{memory.id}",
                            max_attempts=int(self.config.get("tasks.max_attempts", 3) or 3),
                        )
            return memory

    async def archive_memory(
        self,
        memory_id: str,
        principal: Principal,
        *,
        reason: str = "",
        mode: str = AccessMode.MANAGE,
    ) -> Memory:
        return await self.update_memory(
            memory_id,
            {"status": MemoryStatus.ARCHIVED},
            principal,
            mode=mode,
        )

    async def restore_memory(
        self,
        memory_id: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> Memory:
        async with self.database.session() as session:
            repository = self.repository(session)
            memory = await repository.get_memory(memory_id)
            if memory is None:
                raise NotFoundError("memory not found")
            memory.retention = 1.0
            memory.last_reinforced_at = utcnow()
            memory.status = MemoryStatus.ACTIVE
            memory.version += 1
            await repository.add_version(memory, change_type="restore", changed_by=principal.actor)
            await repository.add_event(
                event_type=EventType.RESTORED,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor=principal.actor,
            )
            return memory

    async def delete_memory(
        self,
        memory_id: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> None:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(memory.space_id):
                raise AccessDeniedError("no write access to this memory")
            deleted = await repository.delete_memory(memory)
            await repository.add_event(
                event_type=EventType.DELETED,
                memory_id=None,
                space_id=deleted["space_id"],
                actor=principal.actor,
                payload={
                    "memory_id": deleted["memory_id"],
                    "snapshot": deleted["snapshot"],
                    "hard": True,
                },
            )

    async def bulk_action(
        self,
        memory_ids: list[str],
        action: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> dict[str, Any]:
        if action not in {"archive", "delete", "restore", "pin", "unpin"}:
            raise ValidationError(f"unsupported bulk action: {action}")
        processed: list[str] = []
        failed: list[dict[str, str]] = []
        for memory_id in memory_ids:
            try:
                if action == "archive":
                    await self.archive_memory(memory_id, principal, mode=mode)
                elif action == "restore":
                    await self.restore_memory(memory_id, principal, mode=mode)
                elif action == "delete":
                    await self.delete_memory(memory_id, principal, mode=mode)
                elif action in {"pin", "unpin"}:
                    await self.update_memory(
                        memory_id,
                        {"pinned": action == "pin"},
                        principal,
                        mode=mode,
                    )
                processed.append(memory_id)
            except Exception as exc:  # noqa: BLE001 - report per-item failures
                failed.append({"memory_id": memory_id, "error": str(exc)})
        return {"processed": processed, "failed": failed}

    async def add_evidence(
        self,
        memory_id: str,
        evidence: EvidenceInput,
        principal: Principal,
        *,
        reinforce: bool = False,
        mode: str = AccessMode.MANAGE,
    ) -> MemoryEvidence:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(memory.space_id):
                raise AccessDeniedError("no write access to this memory")
            record = await repository.add_evidence(
                {
                    "memory_id": memory.id,
                    "source_kind": evidence.source_kind,
                    "conversation_id": evidence.conversation_id or principal.conversation_id,
                    "message_id": evidence.message_id,
                    "role": evidence.role,
                    "quote": evidence.quote,
                    "observed_at": naive(evidence.observed_at),
                    "attributes": evidence.attributes,
                }
            )
            if reinforce:
                memory.reinforcement_count += 1
                memory.retention = 1.0
                memory.last_reinforced_at = utcnow()
                memory.version += 1
                await repository.add_version(
                    memory, change_type="reinforce", changed_by=principal.actor
                )
            await repository.add_event(
                event_type=EventType.EVIDENCE_ADDED,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor=principal.actor,
                payload={"evidence_id": record.id, "reinforce": reinforce},
            )
            return record

    async def reinforce_memory(
        self,
        memory_id: str,
        principal: Principal,
        *,
        evidence: EvidenceInput | None = None,
        mode: str = AccessMode.MANAGE,
    ) -> Memory:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(memory.space_id):
                raise AccessDeniedError("no write access to this memory")
            if evidence is not None:
                await repository.add_evidence(
                    {
                        "memory_id": memory.id,
                        "source_kind": evidence.source_kind,
                        "conversation_id": evidence.conversation_id or principal.conversation_id,
                        "message_id": evidence.message_id,
                        "role": evidence.role,
                        "quote": evidence.quote,
                        "observed_at": naive(evidence.observed_at),
                        "attributes": evidence.attributes,
                    }
                )
            memory.reinforcement_count += 1
            memory.retention = 1.0
            memory.last_reinforced_at = utcnow()
            memory.confidence = min(0.98, memory.confidence + (1.0 - memory.confidence) * 0.06)
            memory.version += 1
            await repository.add_version(
                memory, change_type="reinforce", changed_by=principal.actor
            )
            await repository.add_event(
                event_type=EventType.REINFORCED,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor=principal.actor,
                payload={"manual": True, "evidence": bool(evidence)},
            )
            return memory

    async def add_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: str,
        strength: float,
        confidence: float,
        attributes: dict[str, Any],
        principal: Principal,
        mode: str = AccessMode.MANAGE,
    ) -> MemoryRelation:
        if relation_type not in {item.value for item in RelationType}:
            raise ValidationError(f"invalid relation type: {relation_type}")
        if source_id == target_id:
            raise ValidationError("a memory cannot relate to itself")
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            source = await repository.get_memory(source_id)
            target = await repository.get_memory(target_id)
            if source is None or target is None:
                raise NotFoundError("memory not found")
            if not scope.can_read_memory(source) or not scope.can_read_memory(target):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(source.space_id):
                raise AccessDeniedError("no write access to the source memory")
            relation = await repository.add_relation(
                source_id=source_id,
                target_id=target_id,
                relation_type=relation_type,
                strength=strength,
                confidence=confidence,
                attributes=attributes,
            )
            await repository.add_event(
                event_type=EventType.RELATION_CREATED,
                memory_id=source_id,
                space_id=source.space_id,
                actor=principal.actor,
                payload={"relation_id": relation.id, "target": target_id, "type": relation_type},
            )
            return relation

    async def delete_relation(
        self,
        relation_id: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> None:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            relation = await repository.get_relation(relation_id)
            if relation is None:
                raise NotFoundError("relation not found")
            source = await repository.get_memory(relation.source_id)
            if source is None or not scope.can_read_memory(source):
                raise NotFoundError("relation not found")
            if not scope.can_write_space(source.space_id):
                raise AccessDeniedError("no write access to this relation")
            await repository.delete_relation(relation)
            await repository.add_event(
                event_type=EventType.RELATION_DELETED,
                memory_id=source.id,
                space_id=source.space_id,
                actor=principal.actor,
                payload={"relation_id": relation_id},
            )

    async def merge_memories(
        self,
        source_id: str,
        target_id: str,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> dict[str, Any]:
        if source_id == target_id:
            raise ValidationError("cannot merge a memory into itself")
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            source = await repository.get_memory(source_id)
            target = await repository.get_memory(target_id)
            if source is None or target is None:
                raise NotFoundError("memory not found")
            if not scope.can_read_memory(source) or not scope.can_read_memory(target):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(source.space_id) or not scope.can_write_space(
                target.space_id
            ):
                raise AccessDeniedError("no write access to both memories")
            if source.space_id != target.space_id:
                raise ValidationError("only memories in the same space can be merged")

            await repository.add_evidence(
                {
                    "memory_id": target.id,
                    "source_kind": source.source_kind,
                    "conversation_id": None,
                    "message_id": None,
                    "role": "merged",
                    "quote": source.content,
                    "observed_at": naive(source.occurred_at),
                    "attributes": {"merged_from": source.id},
                }
            )
            await repository.move_evidence(source.id, target.id)
            await repository.rewire_relations(source.id, target.id)
            target.importance = max(source.importance, target.importance)
            target.confidence = max(source.confidence, target.confidence)
            target.reinforcement_count += source.reinforcement_count
            target.access_count += source.access_count
            target.tags = list(dict.fromkeys([*(target.tags or []), *(source.tags or [])]))
            target_attributes = dict(target.attributes or {})
            target_attributes["merged_from"] = list(
                dict.fromkeys([*(target_attributes.get("merged_from") or []), source.id])
            )
            target.attributes = target_attributes
            target.version += 1
            await repository.update_memory(target, {"tags": target.tags})
            await repository.add_version(target, change_type="merge", changed_by=principal.actor)

            source.status = MemoryStatus.SUPERSEDED
            source.attributes = {**(source.attributes or {}), "merged_into": target.id}
            source.version += 1
            await repository.add_version(source, change_type="merged", changed_by=principal.actor)
            relation = await repository.add_relation(
                source_id=source.id,
                target_id=target.id,
                relation_type=RelationType.MERGED_INTO,
                strength=1.0,
                confidence=0.95,
            )
            await repository.add_event(
                event_type=EventType.MERGED,
                memory_id=target.id,
                space_id=target.space_id,
                actor=principal.actor,
                payload={"source": source.id, "target": target.id, "relation_id": relation.id},
            )
            return {"source": source, "target": target, "relation": relation}

    async def rollback_version(
        self,
        memory_id: str,
        version: int,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> Memory:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            memory = await repository.get_memory(memory_id)
            if memory is None or not scope.can_read_memory(memory):
                raise NotFoundError("memory not found")
            if not scope.can_write_space(memory.space_id):
                raise AccessDeniedError("no write access to this memory")
            snapshot = await repository.get_version(memory_id, version)
            if snapshot is None:
                raise NotFoundError("memory version not found")
            data = snapshot.snapshot
            restore_fields = {
                "title",
                "content",
                "memory_type",
                "category",
                "source_kind",
                "importance",
                "confidence",
                "retention",
                "pinned",
                "tags",
                "attributes",
                "language",
                "stable",
            }
            for key in restore_fields:
                if key in data:
                    setattr(memory, key, data[key])
            memory.content_hash = content_hash(memory.content)
            memory.status = MemoryStatus.ACTIVE
            memory.version += 1
            await repository.reindex_memory(memory)
            await repository.delete_embeddings(memory.id)
            if self.embeddings.enabled:
                await repository.enqueue_task(
                    TaskType.EMBED_MEMORY,
                    {"memory_id": memory.id},
                    dedupe_key=f"embed:{memory.id}",
                    max_attempts=int(self.config.get("tasks.max_attempts", 3) or 3),
                )
            await repository.add_version(memory, change_type="rollback", changed_by=principal.actor)
            await repository.add_event(
                event_type=EventType.ROLLED_BACK,
                memory_id=memory.id,
                space_id=memory.space_id,
                actor=principal.actor,
                payload={"restored_version": version},
            )
            return memory

    # -- Statistics -----------------------------------------------------

    async def stats(
        self,
        principal: Principal,
        *,
        mode: str = AccessMode.MANAGE,
    ) -> dict[str, Any]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, mode)
            counts = await repository.stats_counts(scope.space_ids)
            _recent_tasks, total_tasks = await repository.list_tasks(limit=1)
            pending_tasks = 0
            running_tasks = 0
            for status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                subset, subset_total = await repository.list_tasks(statuses=[status], limit=1)
                if status == TaskStatus.PENDING:
                    pending_tasks = subset_total
                else:
                    running_tasks = subset_total
            recent_events = await repository.list_events(limit=8)
            spaces = await repository.list_spaces(ids=scope.space_ids)
            return {
                **counts,
                "spaces_detail": spaces,
                "tasks": {
                    "total": total_tasks,
                    "pending": pending_tasks,
                    "running": running_tasks,
                },
                "recent_events": recent_events,
                "storage": {
                    "database_bytes": (
                        await asyncio.to_thread(os.path.getsize, self.database.path)
                        if self.database.path.exists()
                        else 0
                    ),
                    "fts_enabled": self.database.fts_enabled,
                    "vector_backend": self.config.get("vector.backend", "sqlite"),
                    "schema_version": await self.database.schema_version(),
                },
                "embedding": {
                    "enabled": self.embeddings.enabled,
                    "provider": self.embeddings.provider_name,
                    "model": self.embeddings.model_name(),
                },
                "access": {
                    "mode": scope.mode,
                    "space_count": len(scope.space_ids),
                },
            }

    async def recent_events(
        self,
        principal: Principal,
        *,
        limit: int = 50,
        offset: int = 0,
        memory_id: str | None = None,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            repository = self.repository(session)
            scope = await self._scope(repository, principal, AccessMode.MANAGE)
            events = await repository.list_events(
                memory_id=memory_id,
                event_types=event_types,
                limit=limit,
                offset=offset,
            )
            visible = [
                event
                for event in events
                if event.space_id is None or event.space_id in scope.space_ids
            ]
            return [
                {"event": event, "space": await repository.get_space(event.space_id)}
                if event.space_id
                else {"event": event, "space": None}
                for event in visible
            ]

"""JSON serialization helpers shared by API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..db.models import (
    AsyncTask,
    Entity,
    Memory,
    MemoryEvent,
    MemoryEvidence,
    MemoryGrant,
    MemoryRelation,
    MemorySpace,
    MemoryVersion,
)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def space_to_dict(space: MemorySpace | None, *, memory_count: int | None = None) -> dict | None:
    if space is None:
        return None
    data = {
        "id": space.id,
        "kind": space.kind,
        "key": space.key,
        "name": space.name,
        "description": space.description,
        "user_id": space.user_id,
        "character_id": space.character_id,
        "conversation_id": space.conversation_id,
        "status": space.status,
        "settings": space.settings or {},
        "created_at": iso(space.created_at),
        "updated_at": iso(space.updated_at),
    }
    if memory_count is not None:
        data["memory_count"] = memory_count
    return data


def memory_to_dict(
    memory: Memory,
    *,
    space: MemorySpace | None = None,
    evidence_count: int | None = None,
    relation_count: int | None = None,
    scores: dict[str, Any] | None = None,
) -> dict:
    data = {
        "id": memory.id,
        "space_id": memory.space_id,
        "space": space_to_dict(space),
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
        "attributes": memory.attributes or {},
        "language": memory.language,
        "occurred_at": iso(memory.occurred_at),
        "last_accessed_at": iso(memory.last_accessed_at),
        "last_reinforced_at": iso(memory.last_reinforced_at),
        "expires_at": iso(memory.expires_at),
        "version": memory.version,
        "created_at": iso(memory.created_at),
        "updated_at": iso(memory.updated_at),
    }
    if evidence_count is not None:
        data["evidence_count"] = evidence_count
    if relation_count is not None:
        data["relation_count"] = relation_count
    if scores is not None:
        data["scores"] = scores
    return data


def evidence_to_dict(evidence: MemoryEvidence) -> dict:
    return {
        "id": evidence.id,
        "memory_id": evidence.memory_id,
        "source_kind": evidence.source_kind,
        "conversation_id": evidence.conversation_id,
        "message_id": evidence.message_id,
        "role": evidence.role,
        "quote": evidence.quote,
        "observed_at": iso(evidence.observed_at),
        "attributes": evidence.attributes or {},
        "created_at": iso(evidence.created_at),
    }


def version_to_dict(version: MemoryVersion, *, include_snapshot: bool = True) -> dict:
    data = {
        "id": version.id,
        "memory_id": version.memory_id,
        "version": version.version,
        "change_type": version.change_type,
        "changed_by": version.changed_by,
        "created_at": iso(version.created_at),
    }
    if include_snapshot:
        data["snapshot"] = version.snapshot
    return data


def relation_to_dict(relation: MemoryRelation, other: Memory | None = None) -> dict:
    return {
        "id": relation.id,
        "source_id": relation.source_id,
        "target_id": relation.target_id,
        "relation_type": relation.relation_type,
        "strength": relation.strength,
        "confidence": relation.confidence,
        "directed": relation.directed,
        "attributes": relation.attributes or {},
        "created_at": iso(relation.created_at),
        "updated_at": iso(relation.updated_at),
        "other": memory_to_dict(other) if other is not None else None,
    }


def event_to_dict(event: MemoryEvent) -> dict:
    return {
        "id": event.id,
        "memory_id": event.memory_id,
        "space_id": event.space_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "payload": event.payload or {},
        "created_at": iso(event.created_at),
    }


def entity_to_dict(entity: Entity) -> dict:
    return {
        "id": entity.id,
        "space_id": entity.space_id,
        "name": entity.name,
        "canonical_name": entity.canonical_name,
        "entity_type": entity.entity_type,
        "aliases": list(entity.aliases or []),
        "description": entity.description,
        "attributes": entity.attributes or {},
        "created_at": iso(entity.created_at),
        "updated_at": iso(entity.updated_at),
    }


def grant_to_dict(grant: MemoryGrant) -> dict:
    return {
        "id": grant.id,
        "space_id": grant.space_id,
        "memory_id": grant.memory_id,
        "grantee_kind": grant.grantee_kind,
        "grantee_id": grant.grantee_id,
        "permission": grant.permission,
        "granted_by": grant.granted_by,
        "expires_at": iso(grant.expires_at),
        "attributes": grant.attributes or {},
        "created_at": iso(grant.created_at),
    }


def task_to_dict(task: AsyncTask) -> dict:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "payload": task.payload or {},
        "result": task.result or {},
        "error": task.error,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "dedupe_key": task.dedupe_key,
        "scheduled_at": iso(task.scheduled_at),
        "started_at": iso(task.started_at),
        "finished_at": iso(task.finished_at),
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }


def node_to_dict(memory: Memory, space: MemorySpace | None) -> dict:
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
        "occurred_at": iso(memory.occurred_at),
        "created_at": iso(memory.created_at),
        "updated_at": iso(memory.updated_at),
    }

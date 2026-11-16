"""SQLAlchemy models for the memory engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC timestamp for SQLite storage."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, onupdate=utcnow, nullable=False
    )


class MemorySpace(TimestampMixin, Base):
    """A visibility boundary: user, character or conversation memory."""

    __tablename__ = "memory_spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    character_id: Mapped[str | None] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_memory_spaces_kind_owner", "kind", "user_id", "character_id"),)


class Memory(TimestampMixin, Base):
    """A single memory with independent quality and lifecycle signals."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    space_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="long_term", index=True
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="other", index=True)
    source_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user_explicit", index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)

    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    retention: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reinforcement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    contradiction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), index=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    last_reinforced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_memories_space_status", "space_id", "status"),
        Index("ix_memories_space_type", "space_id", "memory_type"),
        Index("ix_memories_status_retention", "status", "retention"),
        Index("ix_memories_occurred", "occurred_at"),
        Index("ix_memories_updated", "updated_at"),
    )


class MemoryRelation(TimestampMixin, Base):
    """A typed, weighted edge between two memories."""

    __tablename__ = "memory_relations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "source_id", "target_id", "relation_type", name="uix_relation_source_target_type"
        ),
        Index("ix_relations_type", "relation_type"),
    )


class MemoryEvidence(TimestampMixin, Base):
    """Source evidence proving where a memory came from."""

    __tablename__ = "memory_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="user_explicit")
    conversation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class MemoryGrant(TimestampMixin, Base):
    """An explicit share from a user space (or memory) to a character."""

    __tablename__ = "memory_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    space_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memory_spaces.id", ondelete="CASCADE"), index=True
    )
    memory_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    grantee_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="character")
    grantee_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(24), nullable=False, default="read")
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_grants_grantee", "grantee_kind", "grantee_id"),)


class MemoryEvent(Base):
    """Audit trail for lifecycle mutations and access accounting."""

    __tablename__ = "memory_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="SET NULL"), index=True
    )
    space_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memory_spaces.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False, index=True
    )


class Entity(TimestampMixin, Base):
    """A canonical entity referenced by memories."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    space_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memory_spaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="topic", index=True
    )
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "space_id", "canonical_name", "entity_type", name="uix_entity_space_name_type"
        ),
    )


class MemoryEntity(Base):
    """Join table linking memories to entities."""

    __tablename__ = "memory_entities"

    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="mentioned")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False
    )


class MemoryVersion(Base):
    """Immutable snapshot created on every meaningful memory update."""

    __tablename__ = "memory_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, default="update")
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("memory_id", "version", name="uix_version_memory_version"),
        Index("ix_versions_memory_created", "memory_id", "created_at"),
    )


class MemoryEmbedding(Base):
    """A vector for one memory produced by one provider/model pair."""

    __tablename__ = "memory_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    norm: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("memory_id", "provider", "model", name="uix_embedding_memory_model"),
    )


class AsyncTask(Base):
    """Persisted background work (extraction, embeddings, consolidation...)."""

    __tablename__ = "async_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_tasks_status_priority", "status", "priority", "scheduled_at"),
        Index("ix_tasks_type_status", "task_type", "status"),
    )


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utcnow, nullable=False
    )


ALL_MODELS = (
    MemorySpace,
    Memory,
    MemoryRelation,
    MemoryEvidence,
    MemoryGrant,
    MemoryEvent,
    Entity,
    MemoryEntity,
    MemoryVersion,
    MemoryEmbedding,
    AsyncTask,
    SchemaMigration,
)

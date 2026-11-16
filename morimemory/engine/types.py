"""Validated types shared by the engine and HTTP layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EvidenceInput(BaseModel):
    source_kind: str = "user_explicit"
    conversation_id: str | None = None
    message_id: str | None = None
    role: str = ""
    quote: str = ""
    observed_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    entity_type: str = "topic"
    role: str = "mentioned"


class MemoryCandidate(BaseModel):
    """A candidate memory produced by extraction or entered manually."""

    content: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="", max_length=255)
    memory_type: str = "long_term"
    category: str = "other"
    source_kind: str = "user_explicit"
    scope: str = "auto"
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    entities: list[EntityInput] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)
    contradicts_memory_id: str | None = None
    rationale: str = ""


class ConversationMessage(BaseModel):
    id: str | None = None
    role: str = "user"
    content: str = Field(min_length=1, max_length=50000)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Principal(BaseModel):
    """The caller identity forwarded by the MoriBot host."""

    user_id: str = "local"
    character_id: str | None = None
    conversation_id: str | None = None
    role: str = "admin"

    @property
    def actor(self) -> str:
        parts = [f"user:{self.user_id}"]
        if self.character_id:
            parts.append(f"character:{self.character_id}")
        if self.conversation_id:
            parts.append(f"conversation:{self.conversation_id}")
        return "|".join(parts)

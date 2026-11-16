"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..engine.types import ConversationMessage, EntityInput, EvidenceInput, MemoryCandidate


class ApiEnvelope(BaseModel):
    status: str = "ok"
    message: str = ""
    data: Any = None


class MemoryCreateRequest(MemoryCandidate):
    space_id: str | None = None
    deduplicate: bool = True
    force: bool = False


class MemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    memory_type: str | None = None
    category: str | None = None
    source_kind: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    retention: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None
    tags: list[str] | None = None
    attributes: dict[str, Any] | None = None
    occurred_at: datetime | None = None
    expires_at: datetime | None = None
    status: str | None = None


class ReinforceRequest(BaseModel):
    evidence: EvidenceInput | None = None


class SearchRequest(BaseModel):
    query: str = ""
    mode: Literal["recall", "manage"] = "recall"
    limit: int = Field(default=10, ge=1, le=100)
    token_budget: int = Field(default=2000, ge=64, le=200000)
    space_id: str | None = None
    space_kind: str | None = None
    memory_types: list[str] | None = None
    categories: list[str] | None = None
    statuses: list[str] | None = None
    min_importance: float | None = Field(default=None, ge=0, le=1)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    tags: list[str] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    record_access: bool = False
    expand_relations: bool = True


class ContextRequest(SearchRequest):
    limit: int = Field(default=8, ge=1, le=50)
    token_budget: int = Field(default=1600, ge=64, le=200000)
    record_access: bool = True


class IngestRequest(BaseModel):
    conversation_id: str | None = None
    character_id: str | None = None
    character_name: str | None = Field(default=None, max_length=64)
    messages: list[ConversationMessage] = Field(min_length=1, max_length=200)
    async_mode: bool = True


class SpaceCreateRequest(BaseModel):
    kind: str
    name: str = ""
    description: str = ""
    user_id: str | None = None
    character_id: str | None = None
    conversation_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class SpaceUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: Literal["active", "archived"] | None = None
    settings: dict[str, Any] | None = None


class GrantCreateRequest(BaseModel):
    space_id: str | None = None
    memory_id: str | None = None
    grantee_kind: Literal["character", "user"] = "character"
    grantee_id: str
    permission: Literal["read", "write", "read_write"] = "read"
    expires_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class RelationCreateRequest(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = "related_to"
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceCreateRequest(EvidenceInput):
    reinforce: bool = False


class BulkActionRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["archive", "delete", "restore", "pin", "unpin"]


class MergeRequest(BaseModel):
    source_id: str


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)


class ConfigPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding: dict[str, Any] | None = None
    extraction: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    vector: dict[str, Any] | None = None
    tasks: dict[str, Any] | None = None


class TestEmbeddingRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    dimensions: int | None = Field(default=None, ge=1, le=20000)


class ImportRequest(BaseModel):
    format: Literal["json", "csv"] = "json"
    payload: Any
    target_space_id: str | None = None
    deduplicate: bool = True


class MaintenanceRequest(BaseModel):
    async_mode: bool = False
    include_embeddings: bool = False
    space_id: str | None = None
    limit: int = Field(default=1000, ge=1, le=10000)


class ReembedRequest(BaseModel):
    limit: int = Field(default=1000, ge=1, le=10000)


class EntityInputRequest(EntityInput):
    pass

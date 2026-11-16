"""Domain vocabulary for the memory engine.

Memory *spaces* (where a memory lives and who may see it) are deliberately
separate from memory *content types* (how a memory behaves over time).
"""

from __future__ import annotations

from enum import StrEnum


class MemorySpaceKind(StrEnum):
    """Visibility scope of a memory space."""

    USER = "user"
    CHARACTER = "character"
    CONVERSATION = "conversation"


class MemoryType(StrEnum):
    """Functional memory type, modelled after human memory systems."""

    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    ASSOCIATIVE = "associative"


class MemoryCategory(StrEnum):
    """What the memory is about."""

    IDENTITY = "identity"
    FACT = "fact"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    GOAL = "goal"
    PLAN = "plan"
    SKILL = "skill"
    OPINION = "opinion"
    HABIT = "habit"
    CONTEXT = "context"
    SUMMARY = "summary"
    OTHER = "other"


class SourceKind(StrEnum):
    """Provenance class. Inference must never masquerade as user fact."""

    USER_EXPLICIT = "user_explicit"
    AI_INFERENCE = "ai_inference"
    BEHAVIOR_OBSERVATION = "behavior_observation"
    MODEL_SUMMARY = "model_summary"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    DELETED = "deleted"


class RelationType(StrEnum):
    RELATED_TO = "related_to"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    PART_OF = "part_of"
    CAUSES = "causes"
    PRECEDES = "precedes"
    SAME_ENTITY = "same_entity"
    MERGED_INTO = "merged_into"
    CONSOLIDATED_FROM = "consolidated_from"


class EventType(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    REINFORCED = "reinforced"
    MERGED = "merged"
    RELATION_CREATED = "relation_created"
    RELATION_DELETED = "relation_deleted"
    EVIDENCE_ADDED = "evidence_added"
    ARCHIVED = "archived"
    RESTORED = "restored"
    DELETED = "deleted"
    FORGOTTEN_SOFT = "forgotten_soft"
    CONSOLIDATED = "consolidated"
    CONTRADICTED = "contradicted"
    ACCESSED = "accessed"
    IMPORTED = "imported"
    VERSION_CREATED = "version_created"
    ROLLED_BACK = "rolled_back"
    SHARED = "shared"
    UNSHARED = "unshared"
    EMBEDDED = "embedded"
    TASK_CREATED = "task_created"


class TaskType(StrEnum):
    EXTRACT_CONVERSATION = "extract_conversation"
    EMBED_MEMORY = "embed_memory"
    REEMBED_ALL = "reembed_all"
    CONSOLIDATE = "consolidate"
    DECAY_SCAN = "decay_scan"
    REBUILD_INDEX = "rebuild_index"
    BACKUP = "backup"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GrantPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"


class PrincipalRole(StrEnum):
    ADMIN = "admin"
    SERVICE = "service"


class AccessMode(StrEnum):
    """Management browsing versus reply-time recall."""

    MANAGE = "manage"
    RECALL = "recall"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"

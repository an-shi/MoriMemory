"""JSON/CSV memory import and export scoped by backend permissions."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from ..api.serializers import (
    evidence_to_dict,
    memory_to_dict,
    relation_to_dict,
    space_to_dict,
)
from ..db.database import Database
from ..db.repository import MemoryFilters, MemoryRepository
from ..domain import AccessMode, SourceKind
from ..engine.core import MemoryEngine
from ..engine.types import EvidenceInput, MemoryCandidate, Principal
from ..errors import ValidationError

CSV_COLUMNS = [
    "id",
    "space_id",
    "space_kind",
    "title",
    "content",
    "memory_type",
    "category",
    "source_kind",
    "status",
    "importance",
    "confidence",
    "retention",
    "tags",
    "occurred_at",
    "created_at",
    "updated_at",
    "attributes",
]


class TransferService:
    def __init__(self, database: Database, engine: MemoryEngine) -> None:
        self.database = database
        self.engine = engine

    def repository(self, session) -> MemoryRepository:
        return MemoryRepository(session, fts_enabled=self.database.fts_enabled)

    async def export(
        self,
        filters: MemoryFilters,
        principal: Principal,
        *,
        fmt: str = "json",
    ) -> tuple[bytes, str, str]:
        ids, _scope = await self.engine.list_memory_ids(
            filters, principal, sort_by="created_at", sort_order="asc"
        )
        async with self.database.session() as session:
            repository = self.repository(session)
            memories = await repository.get_memories(ids)
            ordered = [memories[memory_id] for memory_id in ids if memory_id in memories]
            spaces = {
                space.id: space
                for space in await repository.list_spaces(
                    ids=list({memory.space_id for memory in ordered})
                )
            }
            relations = await repository.list_relations_among(ids)
            evidence_map: dict[str, list] = {}
            for memory in ordered:
                evidence_map[memory.id] = await repository.list_evidence(memory.id)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if fmt.lower() == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for memory in ordered:
                space = spaces.get(memory.space_id)
                writer.writerow(
                    {
                        "id": memory.id,
                        "space_id": memory.space_id,
                        "space_kind": space.kind if space else "",
                        "title": memory.title,
                        "content": memory.content,
                        "memory_type": memory.memory_type,
                        "category": memory.category,
                        "source_kind": memory.source_kind,
                        "status": memory.status,
                        "importance": memory.importance,
                        "confidence": memory.confidence,
                        "retention": memory.retention,
                        "tags": json.dumps(list(memory.tags or []), ensure_ascii=False),
                        "occurred_at": memory.occurred_at.isoformat() if memory.occurred_at else "",
                        "created_at": memory.created_at.isoformat() if memory.created_at else "",
                        "updated_at": memory.updated_at.isoformat() if memory.updated_at else "",
                        "attributes": json.dumps(memory.attributes or {}, ensure_ascii=False),
                    }
                )
            content = output.getvalue().encode("utf-8-sig")
            return content, "text/csv; charset=utf-8", f"morimemory-{stamp}.csv"

        payload = {
            "format": "morimemory-memories",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "spaces": [space_to_dict(space) for space in spaces.values()],
            "memories": [
                {
                    **memory_to_dict(memory, space=spaces.get(memory.space_id)),
                    "evidence": [
                        evidence_to_dict(item) for item in evidence_map.get(memory.id, [])
                    ],
                }
                for memory in ordered
            ],
            "relations": [relation_to_dict(relation) for relation in relations],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return content, "application/json; charset=utf-8", f"morimemory-{stamp}.json"

    async def import_payload(
        self,
        payload: Any,
        principal: Principal,
        *,
        fmt: str = "json",
        target_space_id: str | None = None,
        deduplicate: bool = True,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        evidence_by_memory: dict[str, list[dict[str, Any]]] = {}
        if fmt.lower() == "csv":
            if not isinstance(payload, str):
                raise ValidationError("CSV import requires a string payload")
            reader = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
            for row in reader:
                items.append(dict(row))
        else:
            document = payload
            if isinstance(document, str):
                try:
                    document = json.loads(document)
                except ValueError as exc:
                    raise ValidationError("invalid JSON payload") from exc
            if isinstance(document, list):
                items = [item for item in document if isinstance(item, dict)]
            elif isinstance(document, dict) and isinstance(document.get("memories"), list):
                items = [item for item in document["memories"] if isinstance(item, dict)]
                for item in items:
                    evidence = item.get("evidence")
                    if isinstance(evidence, list) and item.get("id"):
                        evidence_by_memory[str(item["id"])] = evidence
            else:
                raise ValidationError("JSON payload must be an array or an export document")

        created = 0
        reinforced = 0
        errors: list[dict[str, str]] = []
        memory_ids: list[str] = []
        accessible_spaces = {
            entry["space"].id
            for entry in await self.engine.list_spaces(principal, mode=AccessMode.MANAGE)
        }
        for index, item in enumerate(items):
            try:
                candidate = self._candidate_from_item(
                    item,
                    evidence_by_memory.get(str(item.get("id", "")), []),
                )
                source_space = str(item.get("space_id") or "").strip()
                space_id = target_space_id or (
                    source_space if source_space in accessible_spaces else None
                )
                result = await self.engine.create_memory(
                    candidate,
                    principal,
                    space_id=space_id,
                    mode=AccessMode.MANAGE,
                    deduplicate=deduplicate,
                )
                memory_ids.append(result.memory.id)
                if result.action == "reinforced":
                    reinforced += 1
                else:
                    created += 1
            except Exception as exc:  # noqa: BLE001 - per-row import errors
                errors.append({"row": str(index + 1), "error": str(exc)})
        return {
            "created": created,
            "reinforced": reinforced,
            "failed": len(errors),
            "errors": errors[:50],
            "memory_ids": memory_ids,
        }

    def _candidate_from_item(
        self,
        item: dict[str, Any],
        evidence_items: list[dict[str, Any]],
    ) -> MemoryCandidate:
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValidationError("memory content is required")
        tags = item.get("tags")
        if isinstance(tags, str):
            try:
                parsed = json.loads(tags)
                tags = (
                    parsed if isinstance(parsed, list) else [tag for tag in tags.split(",") if tag]
                )
            except ValueError:
                tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        elif not isinstance(tags, list):
            tags = []
        attributes = item.get("attributes")
        if isinstance(attributes, str):
            try:
                attributes = json.loads(attributes)
            except ValueError:
                attributes = {}
        if not isinstance(attributes, dict):
            attributes = {}
        attributes = {**attributes, "imported": True}

        evidence: list[EvidenceInput] = []
        for raw in evidence_items:
            if not isinstance(raw, dict):
                continue
            evidence.append(
                EvidenceInput(
                    source_kind=str(raw.get("source_kind") or SourceKind.AI_INFERENCE),
                    conversation_id=raw.get("conversation_id"),
                    message_id=raw.get("message_id"),
                    role=str(raw.get("role") or "import"),
                    quote=str(raw.get("quote") or content)[:2000],
                    observed_at=raw.get("observed_at"),
                    attributes={**(raw.get("attributes") or {}), "imported": True},
                )
            )
        source_kind = str(item.get("source_kind") or SourceKind.AI_INFERENCE)
        if source_kind not in {value.value for value in SourceKind}:
            source_kind = SourceKind.AI_INFERENCE
        confidence = item.get("confidence")
        try:
            confidence_value = float(confidence) if confidence not in (None, "") else 0.5
        except (TypeError, ValueError):
            confidence_value = 0.5
        importance = item.get("importance")
        try:
            importance_value = float(importance) if importance not in (None, "") else None
        except (TypeError, ValueError):
            importance_value = None
        occurred_at = None
        raw_occurred = item.get("occurred_at")
        if isinstance(raw_occurred, str) and raw_occurred.strip():
            try:
                occurred_at = datetime.fromisoformat(raw_occurred.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = None
        return MemoryCandidate(
            content=content,
            title=str(item.get("title") or "")[:255],
            memory_type=str(item.get("memory_type") or "long_term"),
            category=str(item.get("category") or "other"),
            source_kind=source_kind,
            importance=importance_value,
            confidence=confidence_value,
            tags=[str(tag) for tag in tags],
            attributes=attributes,
            occurred_at=occurred_at,
            evidence=evidence,
            rationale="import",
        )

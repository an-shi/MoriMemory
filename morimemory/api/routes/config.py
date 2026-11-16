"""Runtime configuration and embedding connectivity testing."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from ...config import ConfigManager
from ...domain import (
    AccessMode,
    GrantPermission,
    MemoryCategory,
    MemorySpaceKind,
    MemoryStatus,
    MemoryType,
    RelationType,
    SourceKind,
)
from ...embeddings import (
    EmbeddingError,
    EmbeddingService,
    HashingEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from ...engine.types import Principal
from ...security import SecretBox
from ..deps import get_config, get_embeddings, require_admin, require_principal
from ..responses import ok
from ..schemas import ConfigPatchRequest, TestEmbeddingRequest

router = APIRouter(tags=["config"])

_EMBEDDING_KEYS = {
    "enabled",
    "provider",
    "model",
    "base_url",
    "dimensions",
    "batch_size",
    "timeout_seconds",
    "normalize",
}
_EXTRACTION_KEYS = {
    "mode",
    "max_candidates",
    "min_characters",
    "include_assistant",
    "auto_accept_confidence",
}
_LLM_KEYS = {"base_url", "model", "timeout_seconds"}
_VECTOR_KEYS = {"backend"}
_TASK_KEYS = {
    "workers",
    "poll_interval_seconds",
    "max_attempts",
    "retention_days",
    "run_maintenance_on_startup",
    "maintenance_interval_hours",
}


def _clean_mapping(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value[key] for key in allowed if key in value}


@router.get("/config")
async def get_runtime_config(
    _principal: Principal = Depends(require_principal),
    config: ConfigManager = Depends(get_config),
) -> dict:
    return ok(config.public_dict())


@router.patch("/config")
async def update_runtime_config(
    payload: ConfigPatchRequest,
    _principal: Principal = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> dict:
    secret_box = SecretBox(config.secret)
    patch: dict[str, Any] = {}
    if payload.embedding is not None:
        embedding = _clean_mapping(payload.embedding, _EMBEDDING_KEYS)
        if "api_key" in payload.embedding:
            api_key = str(payload.embedding.get("api_key") or "").strip()
            embedding["api_key_encrypted"] = secret_box.encrypt(api_key) if api_key else ""
        patch["embedding"] = embedding
    if payload.extraction is not None:
        extraction = _clean_mapping(payload.extraction, _EXTRACTION_KEYS)
        llm = payload.extraction.get("llm")
        if isinstance(llm, dict):
            llm_patch = _clean_mapping(llm, _LLM_KEYS)
            if "api_key" in llm:
                api_key = str(llm.get("api_key") or "").strip()
                llm_patch["api_key_encrypted"] = secret_box.encrypt(api_key) if api_key else ""
            extraction["llm"] = llm_patch
        patch["extraction"] = extraction
    if payload.retrieval is not None:
        patch["retrieval"] = payload.retrieval
    if payload.memory is not None:
        patch["memory"] = payload.memory
    if payload.vector is not None:
        patch["vector"] = _clean_mapping(payload.vector, _VECTOR_KEYS)
    if payload.tasks is not None:
        patch["tasks"] = _clean_mapping(payload.tasks, _TASK_KEYS)
    config.update(patch)
    config.save()
    if payload.embedding is not None:
        await embeddings.close()
    return ok(config.public_dict(), message="configuration saved")


@router.post("/config/embedding/test")
async def test_embedding(
    payload: TestEmbeddingRequest | None = None,
    _principal: Principal = Depends(require_admin),
    config: ConfigManager = Depends(get_config),
) -> dict:
    secret_box = SecretBox(config.secret)
    overrides = payload or TestEmbeddingRequest()
    provider_name = (overrides.provider or config.get("embedding.provider", "hashing")).lower()
    model = overrides.model or config.get("embedding.model", "local-hash-512")
    dimensions = int(overrides.dimensions or config.get("embedding.dimensions", 512) or 512)
    base_url = overrides.base_url or config.get("embedding.base_url", "")
    api_key = overrides.api_key
    if api_key is None:
        api_key = secret_box.decrypt(str(config.get("embedding.api_key_encrypted", "")))
    if provider_name in {"hashing", "local", "offline"}:
        provider = HashingEmbeddingProvider(model=model, dimensions=dimensions)
    else:
        provider = OpenAICompatibleEmbeddingProvider(
            base_url=str(base_url),
            api_key=str(api_key),
            model=str(model),
            dimensions=dimensions,
            timeout_seconds=float(config.get("embedding.timeout_seconds", 30) or 30),
            normalize=bool(config.get("embedding.normalize", True)),
        )
    started = time.perf_counter()
    try:
        vectors = await provider.embed(["MoriMemory embedding connectivity test"])
    except EmbeddingError as exc:
        from ...errors import ValidationError

        raise ValidationError(str(exc)) from exc
    finally:
        await provider.close()
    return ok(
        {
            "ok": True,
            "provider": provider_name,
            "model": model,
            "dimensions": len(vectors[0]) if vectors else 0,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    )


@router.get("/meta")
async def metadata(
    _principal: Principal = Depends(require_principal),
) -> dict:
    return ok(
        {
            "memory_spaces": [item.value for item in MemorySpaceKind],
            "memory_types": [item.value for item in MemoryType],
            "categories": [item.value for item in MemoryCategory],
            "source_kinds": [item.value for item in SourceKind],
            "statuses": [item.value for item in MemoryStatus],
            "relation_types": [item.value for item in RelationType],
            "permissions": [item.value for item in GrantPermission],
            "access_modes": [item.value for item in AccessMode],
        }
    )

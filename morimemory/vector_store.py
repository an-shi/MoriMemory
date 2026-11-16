"""Pluggable vector index implementations.

The built-in backend stores packed float32 vectors in SQLite and performs an
exact cosine scan. That is appropriate for a local assistant's memory volume
and keeps deployment at zero. Integrators can register another backend under
``vector.backend`` without changing the retrieval pipeline.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.repository import MemoryRepository
from .embeddings import unpack_vector


class VectorIndex(ABC):
    """Interface implemented by every vector backend."""

    name: str = "base"

    @abstractmethod
    async def search(
        self,
        session: AsyncSession,
        repository: MemoryRepository,
        *,
        provider: str,
        model: str,
        space_ids: list[str],
        statuses: list[str] | None,
        query_vector: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        """Return ``(memory_id, cosine_similarity)`` best-first."""


class SQLiteVectorIndex(VectorIndex):
    """Exact cosine similarity over ``memory_embeddings``."""

    name = "sqlite"

    async def search(
        self,
        session: AsyncSession,
        repository: MemoryRepository,
        *,
        provider: str,
        model: str,
        space_ids: list[str],
        statuses: list[str] | None,
        query_vector: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        candidates = await repository.vector_candidates(
            provider=provider,
            model=model,
            space_ids=space_ids,
            statuses=statuses,
        )
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm <= 1e-12:
            return []
        query_unit = [value / query_norm for value in query_vector]
        scored: list[tuple[str, float]] = []
        for memory_id, raw, norm in candidates:
            vector = unpack_vector(raw)
            if len(vector) != len(query_unit):
                continue
            denominator = max(norm, 1e-12)
            score = sum(a * b for a, b in zip(query_unit, vector, strict=False)) / denominator
            scored.append((memory_id, max(-1.0, min(1.0, score))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


class NullVectorIndex(VectorIndex):
    name = "none"

    async def search(
        self,
        session: AsyncSession,
        repository: MemoryRepository,
        *,
        provider: str,
        model: str,
        space_ids: list[str],
        statuses: list[str] | None,
        query_vector: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        return []


_BUILTIN_BACKENDS: dict[str, type[VectorIndex]] = {
    "sqlite": SQLiteVectorIndex,
    "none": NullVectorIndex,
}

_CUSTOM_BACKENDS: dict[str, type[VectorIndex]] = {}


def register_vector_backend(name: str, backend: type[VectorIndex]) -> None:
    """Register a custom backend before the application starts."""
    _CUSTOM_BACKENDS[name] = backend


def create_vector_index(name: str = "sqlite") -> VectorIndex:
    key = (name or "sqlite").strip().lower()
    backend = _CUSTOM_BACKENDS.get(key) or _BUILTIN_BACKENDS.get(key)
    if backend is None:
        raise ValueError(f"unknown vector backend: {name}")
    return backend()


def vector_to_public_metadata(values: dict[str, Any]) -> dict[str, Any]:
    """Small helper for health endpoints."""
    return {"backend": values.get("vector", {}).get("backend", "sqlite")}

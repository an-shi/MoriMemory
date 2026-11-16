"""Pluggable embedding providers.

The default ``hashing`` provider is deterministic, offline and dependency-free;
it is intended for tests, first-run UX and air-gapped installations. Real
semantic quality comes from any OpenAI-compatible embeddings endpoint, which
covers OpenAI, Azure-compatible gateways, Ollama, LM Studio, vLLM, DeepSeek,
SiliconFlow and similar services.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from array import array
from typing import Protocol

import httpx
from loguru import logger

from .config import ConfigManager
from .security import SecretBox

_HASH_DIMENSIONS = 512


class EmbeddingError(RuntimeError):
    pass


def normalize_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        return [0.0 for _ in values]
    return [value / norm for value in values]


def pack_vector(values: list[float]) -> bytes:
    return array("f", values).tobytes()


def unpack_vector(raw: bytes) -> list[float]:
    values = array("f")
    values.frombytes(raw)
    return list(values)


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def close(self) -> None: ...


class HashingEmbeddingProvider:
    """Hashed bag-of-tokens embedding with L2 normalisation."""

    name = "hashing"
    model: str
    dimensions: int

    def __init__(self, model: str = "local-hash-512", dimensions: int = _HASH_DIMENSIONS) -> None:
        self.model = model
        self.dimensions = max(64, int(dimensions or _HASH_DIMENSIONS))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from .db.fts import index_tokens

        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = index_tokens(text)
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dimensions
                sign = 1.0 if (value >> 63) & 1 else -1.0
                # Bigrams and words carry more signal than single CJK characters.
                weight = 1.0 if len(token) <= 1 else 1.6
                vector[index] += sign * weight
            vectors.append(normalize_vector(vector))
        return vectors

    async def close(self) -> None:
        return None


class OpenAICompatibleEmbeddingProvider:
    """OpenAI ``/embeddings`` protocol."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
        normalize: bool = True,
    ) -> None:
        self.name = "openai_compatible"
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.normalize = normalize
        self._client: httpx.AsyncClient | None = None

    def _endpoint(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.timeout_seconds),
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.base_url:
            raise EmbeddingError("embedding.base_url is empty")
        if not self.model:
            raise EmbeddingError("embedding.model is empty")
        payload: dict = {"model": self.model, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        try:
            response = await self._client_or_create().post(self._endpoint(), json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise EmbeddingError(
                f"embedding endpoint returned {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise EmbeddingError("embedding endpoint returned an unexpected payload")
        vectors: list[list[float]] = []
        for item in sorted(items, key=lambda entry: entry.get("index", 0)):
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("embedding endpoint returned an empty vector")
            values = [float(value) for value in vector]
            vectors.append(normalize_vector(values) if self.normalize else values)
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class EmbeddingService:
    """Resolve the configured provider and expose a small async API."""

    def __init__(self, config: ConfigManager, secret_box: SecretBox) -> None:
        self.config = config
        self.secret_box = secret_box
        self._provider: EmbeddingProvider | None = None
        self._signature: tuple | None = None

    def _signature_value(self) -> tuple:
        return (
            bool(self.config.get("embedding.enabled", True)),
            str(self.config.get("embedding.provider", "hashing")),
            str(self.config.get("embedding.model", "local-hash-512")),
            str(self.config.get("embedding.base_url", "")),
            str(self.config.get("embedding.api_key_encrypted", "")),
            int(self.config.get("embedding.dimensions", _HASH_DIMENSIONS) or _HASH_DIMENSIONS),
            float(self.config.get("embedding.timeout_seconds", 30.0) or 30.0),
            bool(self.config.get("embedding.normalize", True)),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("embedding.enabled", True))

    @property
    def provider_name(self) -> str:
        return str(self.config.get("embedding.provider", "hashing"))

    @property
    def model(self) -> str:
        return str(self.config.get("embedding.model", "local-hash-512"))

    def api_key(self) -> str:
        env_key = os.environ.get("MORIMEMORY_EMBEDDING_API_KEY", "").strip()
        if env_key:
            return env_key
        encrypted = str(self.config.get("embedding.api_key_encrypted", ""))
        return self.secret_box.decrypt(encrypted)

    def base_url(self) -> str:
        return os.environ.get("MORIMEMORY_EMBEDDING_BASE_URL", "").strip() or str(
            self.config.get("embedding.base_url", "")
        )

    def model_name(self) -> str:
        return os.environ.get("MORIMEMORY_EMBEDDING_MODEL", "").strip() or self.model

    def _build_provider(self) -> EmbeddingProvider:
        provider = self.provider_name.lower()
        model = self.model_name()
        dimensions = int(
            self.config.get("embedding.dimensions", _HASH_DIMENSIONS) or _HASH_DIMENSIONS
        )
        if provider in {"hashing", "local", "offline"}:
            return HashingEmbeddingProvider(model=model, dimensions=dimensions)
        if provider in {
            "openai",
            "openai_compatible",
            "ollama",
            "custom",
            "deepseek",
            "siliconflow",
            "lmstudio",
            "vllm",
        }:
            return OpenAICompatibleEmbeddingProvider(
                base_url=self.base_url(),
                api_key=self.api_key(),
                model=model,
                dimensions=dimensions,
                timeout_seconds=float(self.config.get("embedding.timeout_seconds", 30.0) or 30.0),
                normalize=bool(self.config.get("embedding.normalize", True)),
            )
        raise EmbeddingError(f"unsupported embedding provider: {provider}")

    def provider(self) -> EmbeddingProvider:
        signature = self._signature_value()
        if self._provider is None or self._signature != signature:
            if self._provider is not None:
                # The old client is closed lazily by the event loop; replacing it
                # is safe because requests hold their own references.
                logger.info("Embedding provider configuration changed; rebuilding client.")
            self._provider = self._build_provider()
            self._signature = signature
        return self._provider

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled:
            raise EmbeddingError("embedding is disabled")
        if not texts:
            return []
        provider = self.provider()
        batch_size = max(1, int(self.config.get("embedding.batch_size", 32) or 32))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await provider.embed(batch))
        return vectors

    async def embed_text(self, text: str) -> list[float]:
        vectors = await self.embed_texts([text])
        if not vectors:
            raise EmbeddingError("no embedding returned")
        return vectors[0]

    async def test_connection(self) -> dict:
        started = time.perf_counter()
        vector = await self.embed_text("MoriMemory embedding connectivity test")
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "provider": self.provider_name,
            "model": self.model_name(),
            "dimensions": len(vector),
            "elapsed_ms": elapsed,
        }

    def dimensions(self) -> int:
        return int(self.config.get("embedding.dimensions", _HASH_DIMENSIONS) or _HASH_DIMENSIONS)

    async def close(self) -> None:
        if self._provider is not None:
            await self._provider.close()
            self._provider = None

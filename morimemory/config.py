"""Runtime configuration with defensive defaults and atomic persistence."""

from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

CONFIG_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "server": {
        "host": "127.0.0.1",
        "port": 6190,
        "cors_origins": [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        "trusted_hosts": [],
        "docs_enabled": True,
    },
    "security": {
        # Generated on first start, never shipped as a fixed default.
        "token": "",
        "secret": "",
        "default_user_id": "local",
        "allow_character_management": True,
        "enforce_origin": True,
    },
    "storage": {
        "database": "morimemory.db",
    },
    "embedding": {
        "enabled": True,
        "provider": "hashing",
        "model": "local-hash-512",
        "base_url": "",
        "api_key_encrypted": "",
        "dimensions": 512,
        "batch_size": 32,
        "timeout_seconds": 30.0,
        "normalize": True,
    },
    "extraction": {
        "mode": "heuristic",
        "llm": {
            "base_url": "",
            "api_key_encrypted": "",
            "model": "",
            "timeout_seconds": 60.0,
        },
        "max_candidates": 24,
        "min_characters": 4,
        "include_assistant": False,
        "auto_accept_confidence": 0.45,
    },
    "retrieval": {
        "weights": {
            "semantic": 0.35,
            "keyword": 0.20,
            "importance": 0.15,
            "confidence": 0.10,
            "recency": 0.10,
            "strength": 0.10,
        },
        "default_limit": 10,
        "max_limit": 50,
        "default_token_budget": 2000,
        "candidate_pool": 120,
        "relation_expansion": 1,
        "recency_half_life_days": 30,
        "min_semantic_similarity": 0.12,
        "min_score": 0.0,
    },
    "memory": {
        "dedup": {
            "duplicate_threshold": 0.92,
            "similar_threshold": 0.82,
            "contradiction_similarity": 0.62,
        },
        "decay": {
            "enabled": True,
            "half_life_days": 45,
            "forget_threshold": 0.08,
            "min_access_count": 1,
            "pinned_immune": True,
        },
        "consolidation": {
            "enabled": True,
            "min_group_size": 3,
            "max_group_size": 20,
            "window_days": 30,
        },
        "default_importance": 0.5,
        "default_confidence": 0.6,
    },
    "vector": {
        "backend": "sqlite",
    },
    "tasks": {
        "workers": 2,
        "poll_interval_seconds": 1.0,
        "max_attempts": 3,
        "retention_days": 30,
        "run_maintenance_on_startup": True,
        "maintenance_interval_hours": 24,
    },
    "backup": {
        "directory": "backups",
        "max_files": 10,
    },
    "logging": {
        "level": "INFO",
    },
}

# Values exposed to embedded clients. Secrets are never part of this list.
_PUBLIC_EMBEDDING_KEYS = {
    "enabled",
    "provider",
    "model",
    "base_url",
    "dimensions",
    "batch_size",
    "timeout_seconds",
    "normalize",
}


def utc_timestamp() -> int:
    return int(time.time())


class ConfigManager:
    """Load, merge and persist MoriMemory configuration.

    The configuration intentionally stays dictionary-based so new engine
    options can be introduced without a migration. Missing keys are always
    backfilled from :data:`DEFAULT_CONFIG`.
    """

    def __init__(self, config_path: str | Path, defaults: dict[str, Any] | None = None) -> None:
        self.config_path = Path(config_path)
        self.defaults = copy.deepcopy(defaults or DEFAULT_CONFIG)
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._loaded = False

    def load(self) -> dict[str, Any]:
        """Read the config file and repair missing defaults/secrets."""
        with self._lock:
            changed = False
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            if not self.config_path.exists():
                self._data = copy.deepcopy(self.defaults)
                changed = True
            else:
                try:
                    raw = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
                    if not isinstance(raw, dict):
                        raise ValueError("configuration root must be a JSON object")
                    self._data = raw
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    backup = self.config_path.with_name(
                        f"{self.config_path.stem}.broken-{utc_timestamp()}.json"
                    )
                    logger.error(
                        "Failed to read config {}: {}. Recreating defaults (backup: {}).",
                        self.config_path,
                        exc,
                        backup,
                    )
                    try:
                        self.config_path.replace(backup)
                    except OSError:
                        logger.exception("Could not back up the broken config file.")
                    self._data = copy.deepcopy(self.defaults)
                    changed = True

                changed = self._merge_missing(self.defaults, self._data) or changed

            if not self._data.get("config_version"):
                self._data["config_version"] = CONFIG_VERSION
                changed = True

            security = self._data.get("security")
            if not isinstance(security, dict):
                security = {}
                self._data["security"] = security
                changed = True
            if not security.get("token"):
                security["token"] = secrets.token_urlsafe(48)
                changed = True
            if not security.get("secret"):
                security["secret"] = secrets.token_urlsafe(48)
                changed = True

            if changed:
                self.save()

            self._loaded = True
            return self._data

    @staticmethod
    def _merge_missing(defaults: dict[str, Any], target: dict[str, Any]) -> bool:
        """Recursively add default keys without overwriting user values."""
        changed = False
        for key, default_value in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(default_value)
                changed = True
                continue
            value = target[key]
            if isinstance(default_value, dict) and isinstance(value, dict):
                changed = ConfigManager._merge_missing(default_value, value) or changed
        return changed

    def save(self) -> None:
        """Atomically persist the current configuration."""
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.config_path.with_name(f".{self.config_path.name}.tmp")
            payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass

    def reload(self) -> dict[str, Any]:
        self._loaded = False
        return self.load()

    def get(self, key: str, default: Any = None) -> Any:
        current: Any = self._data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            parts = key.split(".")
            current = self._data
            for part in parts[:-1]:
                child = current.get(part)
                if not isinstance(child, dict):
                    child = {}
                    current[part] = child
                current = child
            current[parts[-1]] = value

    def update(self, patch: dict[str, Any]) -> None:
        """Deep-merge a partial configuration document."""

        def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = copy.deepcopy(value)

        with self._lock:
            merge(self._data, patch)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def public_dict(self) -> dict[str, Any]:
        """Return a client-safe configuration document."""
        with self._lock:
            embedding = self._data.get("embedding", {})
            extraction = self._data.get("extraction", {})
            public = {
                "config_version": self._data.get("config_version", CONFIG_VERSION),
                "server": {
                    "host": self._data.get("server", {}).get("host", "127.0.0.1"),
                    "port": self._data.get("server", {}).get("port", 6190),
                },
                "security": {
                    "default_user_id": self.get("security.default_user_id", "local"),
                    "allow_character_management": bool(
                        self.get("security.allow_character_management", True)
                    ),
                },
                "embedding": {
                    key: embedding.get(key) for key in _PUBLIC_EMBEDDING_KEYS if key in embedding
                },
                "extraction": {
                    "mode": extraction.get("mode", "heuristic"),
                    "max_candidates": extraction.get("max_candidates", 24),
                    "include_assistant": extraction.get("include_assistant", False),
                    "llm": {
                        "base_url": extraction.get("llm", {}).get("base_url", ""),
                        "model": extraction.get("llm", {}).get("model", ""),
                    },
                },
                "retrieval": copy.deepcopy(self._data.get("retrieval", {})),
                "memory": copy.deepcopy(self._data.get("memory", {})),
                "vector": copy.deepcopy(self._data.get("vector", {})),
                "tasks": copy.deepcopy(self._data.get("tasks", {})),
                "backup": copy.deepcopy(self._data.get("backup", {})),
            }
            public["embedding"]["api_key_set"] = bool(embedding.get("api_key_encrypted"))
            public["extraction"]["llm"]["api_key_set"] = bool(
                extraction.get("llm", {}).get("api_key_encrypted")
            )
            return public

    @property
    def token(self) -> str:
        env = os.environ.get("MORIMEMORY_TOKEN", "").strip()
        if env:
            return env
        token = str(self.get("security.token", "")).strip()
        if not token:
            raise RuntimeError("security.token is not configured")
        return token

    @property
    def secret(self) -> str:
        secret = str(self.get("security.secret", "")).strip()
        if not secret:
            raise RuntimeError("security.secret is not configured")
        return secret

    @property
    def host(self) -> str:
        return os.environ.get("MORIMEMORY_HOST", "").strip() or str(
            self.get("server.host", "127.0.0.1")
        )

    @property
    def port(self) -> int:
        raw = os.environ.get("MORIMEMORY_PORT", "").strip()
        try:
            return int(raw or self.get("server.port", 6190))
        except (TypeError, ValueError):
            return 6190

    @property
    def log_level(self) -> str:
        value = (
            os.environ.get("MORIMEMORY_LOG_LEVEL", "").strip()
            or str(self.get("logging.level", "INFO"))
        ).upper()
        if value not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return value

    @property
    def cors_origins(self) -> list[str]:
        origins = self.get("server.cors_origins", [])
        if not isinstance(origins, list):
            return []
        return [str(origin) for origin in origins if str(origin).strip()]

    @property
    def database_file(self) -> str:
        return str(self.get("storage.database", "morimemory.db"))

    @property
    def default_user_id(self) -> str:
        return str(self.get("security.default_user_id", "local")).strip() or "local"

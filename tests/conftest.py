from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["MORIMEMORY_TESTING"] = "1"

from morimemory.app import create_app  # noqa: E402
from morimemory.config import ConfigManager  # noqa: E402
from morimemory.paths import AppPaths  # noqa: E402

TOKEN = "test-service-token"


@pytest.fixture()
def app_context(tmp_path: Path):
    paths = AppPaths.resolve(tmp_path / "data", project_root=Path.cwd())
    paths.ensure()
    config = ConfigManager(paths.config_file)
    config.load()
    config.set("security.token", TOKEN)
    config.set("security.enforce_origin", True)
    config.set("tasks.workers", 0)
    config.set("tasks.run_maintenance_on_startup", False)
    config.set("embedding.provider", "hashing")
    config.set("embedding.model", "local-hash-512")
    config.set("embedding.dimensions", 512)
    config.set("server.cors_origins", ["http://localhost:5173"])
    config.save()
    app = create_app(paths=paths, config=config, start_background=False)
    return app, config, paths


@pytest.fixture()
def client(app_context):
    app, _config, _paths = app_context
    with TestClient(app) as test_client:
        yield test_client


def auth_headers(
    *,
    user_id: str = "u1",
    character_id: str | None = "char1",
    conversation_id: str | None = "conv1",
    role: str = "admin",
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Mori-User-Id": user_id,
        "X-Mori-Role": role,
    }
    if character_id:
        headers["X-Mori-Character-Id"] = character_id
    if conversation_id:
        headers["X-Mori-Conversation-Id"] = conversation_id
    return headers


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    return auth_headers(role="admin")


@pytest.fixture()
def service_headers() -> dict[str, str]:
    return auth_headers(role="service")

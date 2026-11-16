from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from morimemory.app import create_app

from .conftest import TOKEN, auth_headers


def test_async_ingest_is_processed_by_worker(tmp_path: Path):
    from morimemory.config import ConfigManager
    from morimemory.paths import AppPaths

    paths = AppPaths.resolve(tmp_path / "async-data", project_root=Path.cwd())
    paths.ensure()
    config = ConfigManager(paths.config_file)
    config.load()
    config.set("security.token", TOKEN)
    config.set("tasks.workers", 1)
    config.set("tasks.run_maintenance_on_startup", False)
    config.set("tasks.poll_interval_seconds", 0.1)
    config.set("server.cors_origins", ["http://localhost:5173"])
    config.save()
    app = create_app(paths=paths, config=config, start_background=True)
    headers = auth_headers(role="service")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/conversations/ingest",
            headers=headers,
            json={
                "messages": [
                    {"id": "async-1", "role": "user", "content": "我计划下个月去成都旅行。"}
                ],
                "async_mode": True,
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["data"]["task_id"]
        assert task_id

        deadline = time.time() + 8
        completed = False
        while time.time() < deadline:
            tasks = client.get(
                "/api/v1/tasks?status=completed", headers=auth_headers(role="admin")
            ).json()["data"]["items"]
            if any(task["id"] == task_id for task in tasks):
                completed = True
                break
            time.sleep(0.1)
        assert completed, "worker did not complete the ingest task"

        listing = client.get("/api/v1/memories", headers=auth_headers(role="admin")).json()["data"]
        contents = [item["content"] for item in listing["items"]]
        assert any("成都" in content for content in contents)

from __future__ import annotations

import os
from pathlib import Path

import pytest

from integrations.moribot import MemoryServiceManager, MemoryServiceSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manager_token_is_stable_and_private(tmp_path: Path):
    settings = MemoryServiceSettings(data_dir=tmp_path / "memory", port=1)
    first = MemoryServiceManager(settings)
    token = first.token
    assert len(token) >= 40
    assert (tmp_path / "memory" / "service-token").is_file()

    second = MemoryServiceManager(MemoryServiceSettings(data_dir=tmp_path / "memory", port=1))
    assert second.token == token
    assert first._pid_alive(os.getpid()) is True
    assert first._pid_alive(999_999_999) is False


@pytest.mark.asyncio
async def test_manager_reports_unavailable_service(tmp_path: Path):
    manager = MemoryServiceManager(
        MemoryServiceSettings(
            home=PROJECT_ROOT,
            data_dir=tmp_path / "memory",
            port=1,
            auto_start=False,
            startup_timeout_seconds=1,
        )
    )
    status = await manager.status()
    assert status.running is False
    assert status.database_ok is False
    assert status.base_url == "http://127.0.0.1:1"
    assert await manager.ensure_started() is False
    assert "auto_start" in status.error or status.error == ""

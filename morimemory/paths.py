"""Filesystem layout for the standalone memory service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved locations for configuration, storage, logs and backups."""

    project_root: Path
    data_dir: Path
    config_file: Path
    log_dir: Path
    database_file: Path
    backup_dir: Path

    @classmethod
    def resolve(
        cls,
        data_dir: str | Path | None = None,
        *,
        project_root: Path | None = None,
    ) -> AppPaths:
        root = (project_root or Path(__file__).resolve().parents[1]).resolve()

        if data_dir is not None:
            resolved_data = Path(data_dir).expanduser()
        elif env_dir := os.environ.get("MORIMEMORY_DATA_DIR", "").strip():
            resolved_data = Path(env_dir).expanduser()
        else:
            resolved_data = root / "data"

        if not resolved_data.is_absolute():
            resolved_data = (Path.cwd() / resolved_data).resolve()
        else:
            resolved_data = resolved_data.resolve()

        return cls(
            project_root=root,
            data_dir=resolved_data,
            config_file=resolved_data / "config.json",
            log_dir=resolved_data / "logs",
            database_file=resolved_data / "morimemory.db",
            backup_dir=resolved_data / "backups",
        )

    def ensure(self) -> None:
        for directory in (self.data_dir, self.log_dir, self.backup_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def database_path(self, configured: str = "morimemory.db") -> Path:
        path = Path(configured).expanduser()
        if path.is_absolute():
            return path
        return (self.data_dir / path).resolve()

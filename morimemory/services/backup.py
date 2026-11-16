"""Self-describing ZIP backups with integrity checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from .. import __version__
from ..config import ConfigManager
from ..db.database import Database
from ..errors import ConflictError, ValidationError
from ..paths import AppPaths

BACKUP_FORMAT = "morimemory-backup"
BACKUP_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _integrity_ok(path: Path) -> bool:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(result and result[0] == "ok")
    finally:
        connection.close()


class BackupService:
    def __init__(
        self,
        database: Database,
        config: ConfigManager,
        paths: AppPaths,
    ) -> None:
        self.database = database
        self.config = config
        self.paths = paths

    def backup_dir(self) -> Path:
        configured = str(self.config.get("backup.directory", "backups") or "backups")
        path = Path(configured)
        directory = path if path.is_absolute() else self.paths.data_dir / path
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def create(self) -> Path:
        await self.database.checkpoint()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"morimemory-backup-{stamp}.zip"
        target = self.backup_dir() / filename
        temp_db = self.backup_dir() / f".snapshot-{stamp}.db"
        await asyncio.to_thread(_snapshot_database, self.database.path, temp_db)

        files: list[dict[str, Any]] = [
            {
                "path": "morimemory.db",
                "sha256": _sha256(temp_db),
                "size": temp_db.stat().st_size,
            }
        ]
        config_payload: bytes | None = None
        if self.paths.config_file.is_file():
            config_payload = self.paths.config_file.read_bytes()
            files.append(
                {
                    "path": "config.json",
                    "sha256": hashlib.sha256(config_payload).hexdigest(),
                    "size": len(config_payload),
                }
            )

        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "schema_version": await self.database.schema_version(),
            "application_version": __version__,
            "files": files,
        }
        try:
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
                archive.write(temp_db, "morimemory.db")
                if config_payload is not None:
                    archive.writestr("config.json", config_payload)
        finally:
            temp_db.unlink(missing_ok=True)
        self._prune()
        logger.info("Created MoriMemory backup {}.", target.name)
        return target

    def _prune(self) -> None:
        max_files = max(1, int(self.config.get("backup.max_files", 10) or 10))
        backups = sorted(
            self.backup_dir().glob("morimemory-backup-*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in backups[max_files:]:
            try:
                old.unlink()
            except OSError:
                logger.warning("Could not prune old backup {}.", old)

    def list(self) -> list[dict[str, Any]]:
        backups = sorted(
            self.backup_dir().glob("morimemory-backup-*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
            }
            for path in backups
        ]

    def resolve_backup(self, name: str) -> Path:
        if "/" in name or "\\" in name or name in {"..", "."}:
            raise ValidationError("invalid backup name")
        path = (self.backup_dir() / name).resolve()
        if not path.is_file() or path.parent != self.backup_dir().resolve():
            raise ValidationError("backup not found")
        return path

    async def restore(self, archive_path: Path) -> dict[str, Any]:
        """Restore the SQLite database after validating checksums and integrity.

        The current configuration and service token are preserved so an
        embedded client is not locked out by restoring an older backup.
        """
        temp_db = self.paths.data_dir / f".restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                names = set(archive.namelist())
                if "manifest.json" not in names or "morimemory.db" not in names:
                    raise ValidationError("backup is missing manifest.json or morimemory.db")
                for name in names:
                    if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                        raise ValidationError("backup contains an unsafe path")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if manifest.get("format") != BACKUP_FORMAT:
                    raise ValidationError("unsupported backup format")
                expected = {
                    item["path"]: item["sha256"]
                    for item in manifest.get("files", [])
                    if isinstance(item, dict) and "path" in item
                }
                with archive.open("morimemory.db") as source, temp_db.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if expected.get("morimemory.db") != _sha256(temp_db):
                    raise ValidationError("database checksum mismatch")
            if not await asyncio.to_thread(_integrity_ok, temp_db):
                raise ValidationError("database integrity check failed")

            safety = self.paths.data_dir / (
                f"morimemory.pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
            )
            if self.database.path.exists():
                await asyncio.to_thread(shutil.copy2, self.database.path, safety)
            await self.database.dispose()
            for suffix in ("-wal", "-shm"):
                await asyncio.to_thread(
                    Path(str(self.database.path) + suffix).unlink, missing_ok=True
                )
            await asyncio.to_thread(shutil.copy2, temp_db, self.database.path)
            await self.database.initialize()
            return {
                "restored": True,
                "schema_version": manifest.get("schema_version"),
                "safety_copy": safety.name,
            }
        except zipfile.BadZipFile as exc:
            raise ConflictError("backup archive is not a valid ZIP file") from exc
        finally:
            temp_db.unlink(missing_ok=True)

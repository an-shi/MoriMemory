"""Async SQLite database handle with FTS5 lifecycle support."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .migrations import initialize_schema


class Database:
    """Owns the async engine and session factory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        url_path = self.path.resolve().as_posix()
        self.database_url = f"sqlite+aiosqlite:///{url_path}"
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            future=True,
            connect_args={"timeout": 30},
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self.fts_enabled = False
        self._initialized = False

        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as connection:
            self.fts_enabled = await initialize_schema(connection)
        self._initialized = True
        logger.info(
            "Database ready at {} (fts5={}).",
            self.path,
            "on" if self.fts_enabled else "off",
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session that commits on success and rolls back on error."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def schema_version(self) -> int:
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT MAX(version) FROM schema_migrations"))
            value = result.scalar_one_or_none()
            return int(value or 0)

    async def dispose(self) -> None:
        await self.engine.dispose()
        self._initialized = False

    async def checkpoint(self) -> None:
        """Flush the WAL before taking a file-level snapshot."""
        async with self.engine.connect() as connection:
            await connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))

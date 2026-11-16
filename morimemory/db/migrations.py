"""Schema initialization and forward-only migrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base, SchemaMigration, utcnow

LATEST_SCHEMA_VERSION = 1

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED,
    space_id UNINDEXED,
    title,
    content,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""


def _initialize_schema(connection) -> bool:
    """Create base tables and the FTS index. Runs synchronously."""
    from .fts import fts_available

    Base.metadata.create_all(connection)
    available = fts_available(connection)
    if available:
        connection.exec_driver_sql(FTS_DDL)
    else:
        logger.warning(
            "SQLite FTS5 is unavailable in this Python build; keyword recall will use LIKE."
        )
    return available


Migration = tuple[int, str, Callable[[AsyncConnection], Awaitable[Any]]]


async def _migration_2_placeholder(_connection: AsyncConnection) -> None:
    """Reserved example of a forward migration.

    Real migrations append an entry to ``MIGRATIONS`` and increment
    ``LATEST_SCHEMA_VERSION``. Keeping this function documents the shape
    without pretending a schema change exists.
    """


MIGRATIONS: list[Migration] = [
    # (2, "add example column", _migration_2_placeholder),
]


async def initialize_schema(connection: AsyncConnection) -> bool:
    """Create tables, apply pending migrations and return FTS availability."""
    fts_enabled = bool(await connection.run_sync(_initialize_schema))
    rows = (await connection.execute(text("SELECT version FROM schema_migrations"))).all()
    versions = {int(row[0]) for row in rows}
    if not versions:
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (version, description, applied_at) "
                "VALUES (:version, :description, :applied_at)"
            ),
            {
                "version": LATEST_SCHEMA_VERSION if not MIGRATIONS else 1,
                "description": "initial schema",
                "applied_at": utcnow(),
            },
        )
        versions.add(LATEST_SCHEMA_VERSION if not MIGRATIONS else 1)
        logger.info("Initialized MoriMemory schema v{}.", max(versions))

    for version, description, migrate in MIGRATIONS:
        if version in versions:
            continue
        logger.info("Applying schema migration v{}: {}", version, description)
        await migrate(connection)
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (version, description, applied_at) "
                "VALUES (:version, :description, :applied_at)"
            ),
            {"version": version, "description": description, "applied_at": utcnow()},
        )
        versions.add(version)

    if versions and max(versions) > LATEST_SCHEMA_VERSION:
        logger.warning(
            "Database schema v{} is newer than this build (v{}).",
            max(versions),
            LATEST_SCHEMA_VERSION,
        )
    return fts_enabled


async def record_migration(connection: AsyncConnection, version: int, description: str) -> None:
    """Helper used by tests and future migrations."""
    exists = await connection.get(SchemaMigration, version)
    if exists is None:
        await connection.execute(
            text(
                "INSERT INTO schema_migrations (version, description, applied_at) "
                "VALUES (:version, :description, :applied_at)"
            ),
            {"version": version, "description": description, "applied_at": utcnow()},
        )

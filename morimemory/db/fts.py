"""SQLite FTS5 helpers with CJK-aware tokenization.

SQLite's unicode61 tokenizer does not segment Chinese text. We index both
single CJK characters and bigrams, and translate queries to the same token
space. This keeps keyword retrieval useful for mixed Chinese/English content
without pulling in a native segmentation dependency.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN_RE = re.compile(r"[a-z0-9_]+")
_SAFE_TOKEN_RE = re.compile(r"^[a-z0-9_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").lower()


def index_tokens(value: str) -> list[str]:
    """Tokens written to the FTS index."""
    normalized = normalize_text(value)
    tokens: list[str] = []
    tokens.extend(_LATIN_RE.findall(normalized))
    for run in _CJK_RE.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def query_tokens(value: str) -> list[str]:
    """Tokens used to build a MATCH expression."""
    normalized = normalize_text(value)
    tokens: list[str] = []
    for word in _LATIN_RE.findall(normalized):
        if len(word) >= 2:
            tokens.append(word)
    for run in _CJK_RE.findall(normalized):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    # Stable de-duplication keeps the query deterministic and short.
    return list(dict.fromkeys(tokens))


def build_match_query(value: str, *, match_all: bool = False) -> str:
    """Build a safe FTS5 MATCH expression, or an empty string."""
    parts: list[str] = []
    for token in query_tokens(value):
        if not _SAFE_TOKEN_RE.fullmatch(token):
            continue
        if token.isascii():
            parts.append(f"{token}*")
        else:
            parts.append(f'"{token}"')
    operator = " AND " if match_all else " OR "
    return operator.join(parts)


def serialize_index(
    memory_id: str, space_id: str, title: str, content: str, tags: list[str]
) -> str:
    return " ".join(index_tokens(" ".join([title, content, " ".join(tags)])))


async def fts_upsert(
    session: AsyncSession,
    *,
    memory_id: str,
    space_id: str,
    title: str,
    content: str,
    tags: list[str],
) -> None:
    await fts_delete(session, memory_id)
    await session.execute(
        text(
            "INSERT INTO memory_fts (memory_id, space_id, title, content, tags) "
            "VALUES (:memory_id, :space_id, :title, :content, :tags)"
        ),
        {
            "memory_id": memory_id,
            "space_id": space_id,
            "title": " ".join(index_tokens(title)),
            "content": " ".join(index_tokens(content)),
            "tags": " ".join(index_tokens(" ".join(tags))),
        },
    )


async def fts_delete(session: AsyncSession, memory_id: str) -> None:
    await session.execute(
        text("DELETE FROM memory_fts WHERE memory_id = :memory_id"),
        {"memory_id": memory_id},
    )


async def fts_search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 100,
    match_all: bool = False,
) -> list[tuple[str, float]]:
    """Return ``(memory_id, rank)`` ordered best-first."""
    match = build_match_query(query, match_all=match_all)
    if not match:
        return []
    result = await session.execute(
        text(
            "SELECT memory_id, bm25(memory_fts) AS rank FROM memory_fts "
            "WHERE memory_fts MATCH :match ORDER BY rank LIMIT :limit"
        ),
        {"match": match, "limit": limit},
    )
    return [(str(row[0]), float(row[1])) for row in result.all()]


async def fts_reindex_all(session: AsyncSession) -> int:
    """Rebuild the FTS table from the memories table."""
    await session.execute(text("DELETE FROM memory_fts"))
    result = await session.execute(
        text("SELECT id, space_id, title, content, tags FROM memories WHERE deleted_at IS NULL")
    )
    count = 0
    for row in result.mappings():
        tags = row["tags"] or []
        if isinstance(tags, str):
            try:
                import json

                parsed = json.loads(tags)
                tags = parsed if isinstance(parsed, list) else []
            except ValueError:
                tags = []
        await session.execute(
            text(
                "INSERT INTO memory_fts (memory_id, space_id, title, content, tags) "
                "VALUES (:memory_id, :space_id, :title, :content, :tags)"
            ),
            {
                "memory_id": row["id"],
                "space_id": row["space_id"],
                "title": " ".join(index_tokens(row["title"] or "")),
                "content": " ".join(index_tokens(row["content"] or "")),
                "tags": " ".join(index_tokens(" ".join(str(tag) for tag in tags))),
            },
        )
        count += 1
    return count


async def fts_optimize(session: AsyncSession) -> None:
    await session.execute(text("INSERT INTO memory_fts(memory_fts) VALUES('optimize')"))


def fts_available(session_sync_connection) -> bool:
    """Check whether the linked SQLite build has FTS5."""
    try:
        session_sync_connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS __morimemory_fts_probe USING fts5(x)"
        )
        session_sync_connection.exec_driver_sql("DROP TABLE IF EXISTS __morimemory_fts_probe")
        return True
    except Exception:
        return False

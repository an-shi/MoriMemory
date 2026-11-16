"""Liveness and readiness endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ... import __version__

router = APIRouter(tags=["health"])


async def _health_payload(request: Request) -> dict:
    database_ok = False
    schema_version = 0
    try:
        database = request.app.state.database
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        schema_version = await database.schema_version()
        database_ok = True
    except Exception:
        database_ok = False

    started_at = getattr(request.app.state, "started_at", None)
    uptime = int(time.time() - started_at) if started_at else 0
    embeddings = request.app.state.embeddings
    config = request.app.state.config
    return {
        "status": "ok" if database_ok else "degraded",
        "service": "morimemory",
        "version": __version__,
        "uptime_seconds": max(0, uptime),
        "database": {
            "ok": database_ok,
            "schema_version": schema_version,
            "fts5": bool(request.app.state.database.fts_enabled),
        },
        "embedding": {
            "enabled": embeddings.enabled,
            "provider": embeddings.provider_name,
            "model": embeddings.model_name(),
        },
        "vector": {"backend": config.get("vector.backend", "sqlite")},
        "auth_required": True,
    }


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    payload = await _health_payload(request)
    status = 200 if payload["database"]["ok"] else 503
    return JSONResponse(payload, status_code=status)


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok", "service": "morimemory"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    payload = await _health_payload(request)
    return JSONResponse(
        {
            "status": "ready" if payload["database"]["ok"] else "not_ready",
            **payload,
        },
        status_code=200 if payload["database"]["ok"] else 503,
    )

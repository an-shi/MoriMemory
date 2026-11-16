"""Authenticated MoriBot-to-MoriMemory proxy router (drop-in reference)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from .service_manager import MemoryServiceManager

MEMORY_PROXY_PREFIX = "/memory"
_FORWARD_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS", "HEAD"]
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


def create_memory_proxy_router(
    *,
    manager: MemoryServiceManager,
    get_current_user: Callable[..., Any],
    prefix: str = MEMORY_PROXY_PREFIX,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["memory"])

    @router.api_route("/{path:path}", methods=_FORWARD_METHODS)
    async def proxy(
        request: Request,
        path: str,
        user: Any = Depends(get_current_user),
    ) -> Response:
        body = await request.body()
        headers = {
            "X-Mori-User-Id": getattr(user, "username", None) or str(getattr(user, "id", "local")),
            "X-Mori-Role": "admin",
        }
        for name in (
            "content-type",
            "accept",
            "accept-language",
            "x-mori-character-id",
            "x-mori-conversation-id",
        ):
            value = request.headers.get(name)
            if value:
                headers[name] = value
        try:
            upstream = await manager.request(
                request.method,
                path,
                params=list(request.query_params.multi_items()),
                headers=headers,
                content=body,
            )
        except (httpx.HTTPError, OSError) as exc:
            return Response(
                content=json.dumps(
                    {
                        "status": "error",
                        "message": f"MoriMemory service is unavailable: {exc}",
                        "data": {"code": "memory_unavailable"},
                    },
                    ensure_ascii=False,
                ),
                status_code=503,
                media_type="application/json",
            )

        response_headers = {
            key: value for key, value in upstream.headers.items() if key.lower() not in _HOP_BY_HOP
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

    return router

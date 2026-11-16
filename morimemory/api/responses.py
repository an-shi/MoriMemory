"""Unified response envelope helpers."""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "") -> dict[str, Any]:
    return {"status": "ok", "message": message, "data": data}


def error(message: str, data: Any = None, code: str = "error") -> dict[str, Any]:
    return {"status": "error", "message": message, "data": data, "code": code}

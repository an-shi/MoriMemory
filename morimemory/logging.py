"""Loguru configuration for the memory service."""

from __future__ import annotations

import sys

from loguru import logger

from .paths import AppPaths

_configured_path: str | None = None


def setup_logging(paths: AppPaths, level: str = "INFO") -> None:
    """Attach a rolling file sink and a compact console sink once per process."""
    global _configured_path
    target = str(paths.log_dir.resolve())
    if _configured_path == target:
        return

    paths.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        paths.log_dir / "morimemory.log",
        level=level,
        rotation="10 MB",
        retention=14,
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} - {message}",
    )
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    _configured_path = target

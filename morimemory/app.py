"""FastAPI application factory for the MoriMemory service."""

from __future__ import annotations

import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .api.responses import error
from .api.routes import API_V1_PREFIX, api_router
from .config import ConfigManager
from .db.database import Database
from .domain import TaskType
from .embeddings import EmbeddingService
from .engine.core import MemoryEngine
from .engine.extraction import ExtractionService
from .engine.graph import GraphService
from .engine.maintenance import MaintenanceService
from .engine.retrieval import RetrievalService
from .engine.tasks import MaintenanceScheduler, TaskRunner
from .engine.types import Principal
from .errors import EngineError
from .logging import setup_logging
from .paths import AppPaths
from .security import SecretBox
from .services.backup import BackupService
from .services.import_export import TransferService
from .vector_store import create_vector_index


def create_app(
    *,
    paths: AppPaths | None = None,
    config: ConfigManager | None = None,
    start_background: bool = True,
) -> FastAPI:
    """Build the ASGI application.

    Args:
        paths: Optional pre-resolved runtime paths (used by the CLI/tests).
        config: Optional loaded configuration.
        start_background: Start task workers and the maintenance scheduler.
            Tests and one-shot tooling disable this.
    """
    load_dotenv()
    resolved_paths = paths or AppPaths.resolve()
    resolved_paths.ensure()
    resolved_config = config or ConfigManager(resolved_paths.config_file)
    if config is None:
        resolved_config.load()
    if not os.environ.get("MORIMEMORY_TESTING"):
        setup_logging(resolved_paths, resolved_config.log_level)

    database_path = resolved_paths.database_path(resolved_config.database_file)
    database = Database(database_path)
    secret_box = SecretBox(resolved_config.secret)
    embeddings = EmbeddingService(resolved_config, secret_box)
    vector_index = create_vector_index(str(resolved_config.get("vector.backend", "sqlite")))
    extraction = ExtractionService(resolved_config, secret_box)
    engine = MemoryEngine(database, resolved_config, embeddings, vector_index, extraction)
    retrieval = RetrievalService(database, resolved_config, embeddings, vector_index)
    graph = GraphService(database, resolved_config)
    maintenance = MaintenanceService(database, resolved_config, embeddings, engine)
    transfer = TransferService(database, engine)
    backup = BackupService(database, resolved_config, resolved_paths)

    task_runner = TaskRunner(database, resolved_config)

    async def handle_extract(payload: dict[str, Any]) -> dict[str, Any]:
        return await engine.process_ingest_payload(payload)

    async def handle_embed(payload: dict[str, Any]) -> dict[str, Any]:
        return await maintenance.embed_memory(str(payload.get("memory_id", "")))

    async def handle_reembed(payload: dict[str, Any]) -> dict[str, Any]:
        return await maintenance.reembed_all(limit=int(payload.get("limit", 1000) or 1000))

    async def handle_consolidate(payload: dict[str, Any]) -> dict[str, Any]:
        raw_principal = payload.get("principal")
        principal = (
            Principal.model_validate(raw_principal)
            if isinstance(raw_principal, dict)
            else Principal(user_id=resolved_config.default_user_id, role="admin")
        )
        return await maintenance.consolidate(principal)

    async def handle_decay(_payload: dict[str, Any]) -> dict[str, Any]:
        return await maintenance.apply_decay()

    async def handle_rebuild(payload: dict[str, Any]) -> dict[str, Any]:
        return await maintenance.rebuild_indexes(
            include_embeddings=bool(payload.get("include_embeddings", False))
        )

    async def handle_backup(_payload: dict[str, Any]) -> dict[str, Any]:
        path = await backup.create()
        return {"name": path.name, "size": path.stat().st_size}

    task_runner.register(TaskType.EXTRACT_CONVERSATION, handle_extract)
    task_runner.register(TaskType.EMBED_MEMORY, handle_embed)
    task_runner.register(TaskType.REEMBED_ALL, handle_reembed)
    task_runner.register(TaskType.CONSOLIDATE, handle_consolidate)
    task_runner.register(TaskType.DECAY_SCAN, handle_decay)
    task_runner.register(TaskType.REBUILD_INDEX, handle_rebuild)
    task_runner.register(TaskType.BACKUP, handle_backup)
    scheduler = MaintenanceScheduler(database, resolved_config, task_runner)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await database.initialize()
        application.state.started_at = time.time()
        if start_background:
            await task_runner.start()
            if int(resolved_config.get("tasks.workers", 2) or 0) > 0:
                await scheduler.start()
        logger.info(
            "MoriMemory v{} ready at {}{} (db={})",
            __version__,
            API_V1_PREFIX,
            "",
            database_path,
        )
        try:
            yield
        finally:
            await scheduler.stop()
            await task_runner.stop()
            await embeddings.close()
            await database.dispose()
            logger.info("MoriMemory stopped.")

    docs_enabled = bool(resolved_config.get("server.docs_enabled", True))
    app = FastAPI(
        title="MoriMemory API",
        description="Local-first memory engine for MoriBot",
        version=__version__,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url="/api/redoc" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    app.state.paths = resolved_paths
    app.state.config = resolved_config
    app.state.database = database
    app.state.secret_box = secret_box
    app.state.embeddings = embeddings
    app.state.vector_index = vector_index
    app.state.extraction = extraction
    app.state.engine = engine
    app.state.retrieval = retrieval
    app.state.graph = graph
    app.state.maintenance = maintenance
    app.state.transfer = transfer
    app.state.backup = backup
    app.state.task_runner = task_runner
    app.state.scheduler = scheduler
    app.state.started_at = None

    if resolved_config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_config.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition", "X-Request-ID"],
        )
    trusted_hosts = resolved_config.get("server.trusted_hosts", [])
    if isinstance(trusted_hosts, list) and trusted_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[str(host) for host in trusted_hosts if str(host).strip()],
        )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or secrets.token_hex(12)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.bind(request_id=request_id).exception(
                "Unhandled error while processing {} {}", request.method, request.url.path
            )
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        principal = getattr(request.state, "principal", None)
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith(
            "/api/v1/health"
        ):
            logger.bind(source="http").info(
                "{} {} -> {} ({} ms) actor={}",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                principal.actor if principal is not None else "anonymous",
            )
        return response

    @app.exception_handler(EngineError)
    async def engine_error_handler(_request: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse(
            error(exc.message, {"code": exc.code}, code=exc.code),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = []
        for item in exc.errors():
            location = [
                str(part) for part in item.get("loc", ()) if part not in ("body", "query", "path")
            ]
            details.append(
                {
                    "field": ".".join(location),
                    "message": str(item.get("msg", "invalid value")),
                }
            )
        return JSONResponse(
            error("invalid request", {"errors": details}, code="validation_error"),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            error(detail, code=f"http_{exc.status_code}"),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.opt(exception=exc).error("Unhandled application error")
        return JSONResponse(
            error("internal server error", code="internal_error"),
            status_code=500,
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": "morimemory",
            "version": __version__,
            "api": API_V1_PREFIX,
            "docs": "/api/docs" if docs_enabled else None,
            "health": f"{API_V1_PREFIX}/health",
        }

    app.include_router(api_router)
    return app


def default_app() -> FastAPI:
    """Factory used by ``uvicorn morimemory.app:default_app --factory``."""
    return create_app()

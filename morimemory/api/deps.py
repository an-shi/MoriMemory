"""FastAPI dependencies and request authentication."""

from __future__ import annotations

from typing import cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import ConfigManager
from ..db.database import Database
from ..embeddings import EmbeddingService
from ..engine.core import MemoryEngine
from ..engine.graph import GraphService
from ..engine.maintenance import MaintenanceService
from ..engine.retrieval import RetrievalService
from ..engine.tasks import TaskRunner
from ..engine.types import Principal
from ..errors import AccessDeniedError, AuthenticationError
from ..security import secure_equals
from ..services.backup import BackupService
from ..services.import_export import TransferService

bearer_scheme = HTTPBearer(auto_error=False)

VALID_ROLES = {"admin", "service"}


def get_config(request: Request) -> ConfigManager:
    return cast(ConfigManager, request.app.state.config)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_embeddings(request: Request) -> EmbeddingService:
    return cast(EmbeddingService, request.app.state.embeddings)


def get_engine(request: Request) -> MemoryEngine:
    return cast(MemoryEngine, request.app.state.engine)


def get_retrieval(request: Request) -> RetrievalService:
    return cast(RetrievalService, request.app.state.retrieval)


def get_graph(request: Request) -> GraphService:
    return cast(GraphService, request.app.state.graph)


def get_maintenance(request: Request) -> MaintenanceService:
    return cast(MaintenanceService, request.app.state.maintenance)


def get_transfer(request: Request) -> TransferService:
    return cast(TransferService, request.app.state.transfer)


def get_backup(request: Request) -> BackupService:
    return cast(BackupService, request.app.state.backup)


def get_task_runner(request: Request) -> TaskRunner:
    return cast(TaskRunner, request.app.state.task_runner)


async def require_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    config: ConfigManager = Depends(get_config),
) -> Principal:
    """Authenticate the service token and derive the caller principal.

    MoriBot is the only intended caller. It sets the user, character and
    conversation headers after authenticating the actual end user, so the
    token never travels through a browser when embedded.
    """
    token = credentials.credentials if credentials is not None else ""
    if not token:
        token = request.headers.get("x-mori-memory-token", "").strip()
    if not secure_equals(token, config.token):
        raise AuthenticationError("invalid or missing service token")

    if bool(config.get("security.enforce_origin", True)):
        origin = request.headers.get("origin", "").strip()
        if origin and origin not in config.cors_origins:
            raise AccessDeniedError("origin is not allowed")

    role = request.headers.get("x-mori-role", "admin").strip().lower()
    if role not in VALID_ROLES:
        role = "service"
    principal = Principal(
        user_id=request.headers.get("x-mori-user-id", "").strip() or config.default_user_id,
        character_id=request.headers.get("x-mori-character-id", "").strip() or None,
        conversation_id=request.headers.get("x-mori-conversation-id", "").strip() or None,
        role=role,
    )
    request.state.principal = principal
    return principal


async def require_admin(
    principal: Principal = Depends(require_principal),
) -> Principal:
    if principal.role != "admin":
        raise AccessDeniedError("administrator role required")
    return principal

"""Domain errors surfaced as structured API responses."""

from __future__ import annotations


class EngineError(Exception):
    status_code = 400
    code = "engine_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(EngineError):
    status_code = 404
    code = "not_found"


class AuthenticationError(EngineError):
    status_code = 401
    code = "unauthorized"


class AccessDeniedError(EngineError):
    status_code = 403
    code = "access_denied"


class ValidationError(EngineError):
    status_code = 422
    code = "validation_error"


class ConflictError(EngineError):
    status_code = 409
    code = "conflict"

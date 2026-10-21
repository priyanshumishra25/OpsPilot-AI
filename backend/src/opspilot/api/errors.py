from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from opspilot.audit import current_trace_id
from opspilot.errors import (
    AuthenticationError,
    ConflictError,
    NotConfiguredError,
    NotFoundError,
    OpsPilotError,
    PermissionDeniedError,
    RateLimitedError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from opspilot.logging import get_logger

log = get_logger(__name__)

STATUS = {
    AuthenticationError: 401,
    PermissionDeniedError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    ValidationFailedError: 422,
    RateLimitedError: 429,
    NotConfiguredError: 503,
    UpstreamUnavailableError: 503,
}


def error_body(code: str, message: str, details: dict | None = None) -> dict:  # type: ignore[type-arg]
    return {"error": {"code": code, "message": message, "details": details or {}, "trace_id": current_trace_id()}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OpsPilotError)
    async def _domain(_: Request, exc: OpsPilotError) -> JSONResponse:
        status = next((s for t, s in STATUS.items() if isinstance(exc, t)), 500)
        headers = {}
        if isinstance(exc, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 60))
        if status >= 500:
            log.error("request_failed", code=exc.code, error=exc.message)
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(error_body("validation_failed", "Invalid request", {"errors": errors}), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=exc.__class__.__name__)
        return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)

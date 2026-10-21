"""Shared MCP server plumbing: authenticated server factory, caller context, and the tool wrapper that adds
timeouts, structured errors and audit logging to every tool."""

from __future__ import annotations

import asyncio
import functools
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from opspilot.errors import (
    ConflictError,
    NotFoundError,
    OpsPilotError,
    PermissionDeniedError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from opspilot.logging import get_logger, redact
from opspilot.mcpservers.tokens import SERVER_AUDIENCES, CallerContext, ToolTokenVerifier

log = get_logger("opspilot.mcp")
P = ParamSpec("P")
R = TypeVar("R")


def caller() -> CallerContext:
    """Identity of the user this tool call acts for. Derived only from the verified bearer token."""
    token = get_access_token()
    if token is None or token.claims is None:
        raise ToolError(_err("unauthenticated", "No authenticated caller"))
    c = token.claims
    return CallerContext(
        user_id=uuid.UUID(c["sub"]),
        workspace_id=uuid.UUID(c["ws"]),
        role=str(c.get("role", "")),
        doc_scopes=frozenset(c.get("doc_scopes", [])),
        tool_scopes=frozenset(token.scopes),
        session_id=c.get("sid"),
        token_id=str(c["jti"]),
    )


def require_scope(ctx: CallerContext, scope: str) -> None:
    if scope not in ctx.tool_scopes:
        raise ToolError(_err("permission_denied", f"Caller lacks tool scope '{scope}'"))


def _err(code: str, message: str, **details: Any) -> str:
    return json.dumps({"error": {"code": code, "message": message, "details": details}})


_CODES: list[tuple[type[Exception], str]] = [
    (PermissionDeniedError, "permission_denied"),
    (NotFoundError, "not_found"),
    (ValidationFailedError, "invalid_argument"),
    (ConflictError, "conflict"),
    (UpstreamUnavailableError, "upstream_unavailable"),
]


def tool_guard(
    server: str, scope: str, *, timeout: float = 20.0
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Enforce the tool scope, bound execution time, map domain errors to structured tool errors, and audit."""

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = caller()
            start = time.perf_counter()
            outcome, code = "success", None
            try:
                require_scope(ctx, scope)
                async with asyncio.timeout(timeout):
                    return await fn(*args, **kwargs)
            except ToolError:
                outcome, code = "error", "tool_error"
                raise
            except TimeoutError as exc:
                outcome, code = "error", "timeout"
                raise ToolError(_err("timeout", f"Tool exceeded {timeout:.0f}s")) from exc
            except OpsPilotError as exc:
                code = next((c for t, c in _CODES if isinstance(exc, t)), "internal_error")
                outcome = "denied" if code == "permission_denied" else "error"
                raise ToolError(_err(code, exc.message, **exc.details)) from exc
            finally:
                log.info(
                    "mcp_tool_call",
                    server=server,
                    tool=fn.__name__,
                    user_id=str(ctx.user_id),
                    workspace_id=str(ctx.workspace_id),
                    session_id=ctx.session_id,
                    token_id=ctx.token_id,
                    arguments=redact(dict(kwargs)),
                    outcome=outcome,
                    error_code=code,
                    duration_ms=round((time.perf_counter() - start) * 1000, 1),
                )

        return wrapper

    return decorate


def create_server(
    server: str, *, public_key_b64: str, base_url: str, instructions: str, required_scope: str
) -> MCPServer:
    mcp = MCPServer(
        name=SERVER_AUDIENCES[server],
        instructions=instructions,
        version="0.1.0",
        token_verifier=ToolTokenVerifier(public_key_b64, server),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://opspilot-api.internal"),
            resource_server_url=AnyHttpUrl(base_url),
            validate_token_resource=False,  # the verifier checks the audience itself
            required_scopes=[required_scope],
        ),
    )

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": SERVER_AUDIENCES[server]})

    # The SDK's route decorator is untyped; register explicitly instead of decorating.
    route: Any = mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    route(healthz)
    return mcp


def serve(mcp: MCPServer, port: int) -> None:
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")  # noqa: S104
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", timeout_graceful_shutdown=10)  # noqa: S104

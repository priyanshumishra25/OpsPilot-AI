"""API-side MCP client. Every connection carries a freshly minted, short-lived tool token for the *authenticated*
user, scoped (audience) to the target server. Tool arguments never carry identity."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from opentelemetry import trace

from opspilot.auth.principal import Principal
from opspilot.logging import get_logger
from opspilot.mcpservers.errors import parse_tool_error
from opspilot.mcpservers.tokens import ToolTokenIssuer

ServerName = Literal["knowledge", "github", "observability", "operations"]
SERVERS: tuple[ServerName, ...] = ("knowledge", "github", "observability", "operations")
log = get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass
class ToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None


@dataclass
class ToolCallOutcome:
    server: str
    tool: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0


class McpGateway:
    def __init__(self, urls: dict[ServerName, str], issuer: ToolTokenIssuer, *, timeout: float = 20.0) -> None:
        self._urls = urls
        self._issuer = issuer
        self._timeout = timeout

    def _client(self, principal: Principal, server: ServerName, session_id: str | None) -> Client:
        token = self._issuer.issue(principal, server, session_id=session_id)
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout)
        return Client(streamable_http_client(self._urls[server], http_client=http), read_timeout_seconds=self._timeout)

    async def list_tools(self, principal: Principal, server: ServerName) -> list[ToolInfo]:
        async with self._client(principal, server, None) as c:
            tools = (await c.list_tools()).tools
        return [
            ToolInfo(
                server=server,
                name=t.name,
                description=t.description or "",
                input_schema=dict(t.input_schema),
                output_schema=dict(t.output_schema) if t.output_schema else None,
            )
            for t in tools
        ]

    async def call_tool(
        self,
        principal: Principal,
        server: ServerName,
        tool: str,
        arguments: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> ToolCallOutcome:
        start = time.perf_counter()
        with tracer.start_as_current_span("mcp.call_tool") as span:
            span.set_attribute("mcp.server", server)
            span.set_attribute("mcp.tool", tool)
            try:
                async with self._client(principal, server, session_id) as c:
                    result = await c.call_tool(tool, arguments, read_timeout_seconds=self._timeout)
            except Exception as exc:  # noqa: BLE001 - transport failures are reported as structured tool errors
                log.warning("mcp_transport_error", server=server, tool=tool, error=exc.__class__.__name__)
                return ToolCallOutcome(
                    server,
                    tool,
                    False,
                    error={"code": "unavailable", "message": f"{server} MCP server unreachable or refused the call"},
                    duration_ms=_ms(start),
                )
            span.set_attribute("mcp.is_error", bool(result.is_error))
        if result.is_error:
            text = result.content[0].text if result.content and hasattr(result.content[0], "text") else ""
            return ToolCallOutcome(server, tool, False, error=parse_tool_error(text), duration_ms=_ms(start))
        return ToolCallOutcome(server, tool, True, data=dict(result.structured_content or {}), duration_ms=_ms(start))


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)

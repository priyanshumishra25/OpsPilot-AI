"""Run an MCP server over real Streamable HTTP on a free port, and connect authenticated clients to it."""

from __future__ import annotations

import asyncio
import base64
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer

# Test-only key pair, generated per test session.
_KEY = Ed25519PrivateKey.generate()
PRIVATE_B64 = base64.b64encode(
    _KEY.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
).decode()
PUBLIC_B64 = base64.b64encode(
    _KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
).decode()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@asynccontextmanager
async def running(mcp: MCPServer, port: int) -> AsyncIterator[str]:
    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2)
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes no started event
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


@asynccontextmanager
async def client(url: str, token: str | None) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers, timeout=30))) as c:
        yield c


def payload(result: Any) -> dict[str, Any]:
    """Structured content of a successful call, or the parsed error body of a failed one."""
    from opspilot.mcpservers.errors import parse_tool_error

    if result.is_error:
        return {"is_error": True, "error": parse_tool_error(result.content[0].text)}
    return dict(result.structured_content or {})

"""TEST DOUBLE of the Anthropic Messages API for agent tests.

It replaces only the model: the real Claude Agent SDK and its bundled CLI talk to it over HTTP, and the real MCP
servers execute the tool calls it scripts. It verifies orchestration (tool wiring, permissions, budgets, approvals,
persistence, streaming), not model quality.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tests.mcp_harness import free_port

Block = dict[str, Any]
_ids = itertools.count(1)
Step = list[Block] | Callable[[list[dict[str, Any]]], list[Block]]


def tool(name: str, **input: Any) -> Block:
    return {"type": "tool_use", "name": name, "input": input}


def text(t: str) -> Block:
    return {"type": "text", "text": t}


def final(answer: str, report: dict[str, Any] | None = None) -> Block:
    return {"type": "tool_use", "name": "StructuredOutput", "input": {"answer": answer, "report": report}}


def tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All tool_result blocks the model has been sent so far (parsed JSON where possible)."""
    out = []
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    content = b.get("content")
                    raw = (
                        content
                        if isinstance(content, str)
                        else "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
                    )
                    try:
                        parsed: Any = json.loads(raw)
                    except ValueError:
                        parsed = raw
                    out.append(
                        {
                            "tool_use_id": b.get("tool_use_id"),
                            "is_error": b.get("is_error", False),
                            "content": parsed,
                            "raw": raw,
                        }
                    )
    return out


@dataclass
class ScriptedModel:
    steps: list[Step]
    delay_seconds: float = 0.0
    requests: list[dict[str, Any]] = field(default_factory=list)
    usage: tuple[int, int] = (1000, 200)

    _prompt: str | None = None
    _served: int = 0

    def _step_index(self, messages: list[dict[str, Any]]) -> int:
        # The CLI merges tool rounds and inserts system messages, so count our own responses since the latest
        # user prompt instead of inspecting message structure. A new prompt (e.g. a resumed turn) resets the count.
        prompt = next(
            (
                m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
                for m in reversed(messages)
                if m["role"] == "user" and _is_prompt(m)
            ),
            "",
        )
        if prompt != self._prompt:
            self._prompt, self._served = prompt, 0
        self._served += 1
        return self._served - 1

    def respond(self, body: dict[str, Any]) -> list[Block]:
        # The CLI may make auxiliary requests (e.g. titles/summaries). Only agent-loop requests, which always offer
        # the StructuredOutput tool, advance the script.
        if not any(t.get("name") == "StructuredOutput" for t in body.get("tools", [])):
            return [text("ok")]
        self.requests.append(body)
        idx = self._step_index(body["messages"])
        if idx >= len(self.steps):
            return [text("Done.")]
        step = self.steps[idx]
        return step(body["messages"]) if callable(step) else step


def _is_prompt(m: dict[str, Any]) -> bool:
    c = m.get("content")
    if isinstance(c, str):
        return True
    return (
        isinstance(c, list)
        and any(b.get("type") == "text" for b in c)
        and not any(b.get("type") == "tool_result" for b in c)
    )


def _sse(blocks: list[Block], model: str, usage: tuple[int, int]) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        def ev(e: dict[str, Any]) -> str:
            return f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"

        yield ev(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": usage[0], "output_tokens": 1},
                },
            }
        )
        stop = "end_turn"
        for i, b in enumerate(blocks):
            if b["type"] == "text":
                yield ev({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
                yield ev(
                    {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}
                )
            else:
                stop = "tool_use"
                tid = b.get("id") or f"toolu_{next(_ids):012d}"
                yield ev(
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {"type": "tool_use", "id": tid, "name": b["name"], "input": {}},
                    }
                )
                yield ev(
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])},
                    }
                )
            yield ev({"type": "content_block_stop", "index": i})
        yield ev(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": usage[1]},
            }
        )
        yield ev({"type": "message_stop"})

    return gen()


@asynccontextmanager
async def fake_anthropic(model: ScriptedModel) -> AsyncIterator[str]:
    async def messages(req: Request) -> Any:
        body = await req.json()
        if model.delay_seconds:
            await asyncio.sleep(model.delay_seconds)
        blocks = model.respond(body)
        if body.get("stream"):
            return StreamingResponse(_sse(blocks, body.get("model", "x"), model.usage), media_type="text/event-stream")
        return JSONResponse(
            {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [text("ok")],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def other(req: Request) -> JSONResponse:
        return JSONResponse({}, status_code=404)

    app = Starlette(
        routes=[Route("/v1/messages", messages, methods=["POST"]), Route("/{p:path}", other, methods=["GET", "POST"])]
    )
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2)
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task

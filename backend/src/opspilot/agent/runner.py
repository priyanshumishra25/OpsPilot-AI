"""Runs one agent turn with the Claude Agent SDK.

The agent gets no built-in tools (no shell, no filesystem), loads no local settings, and can only reach the MCP
servers its user's role allows, through tool tokens minted for this user and session. Budgets: SDK max_turns and
max_budget_usd, a wall-clock limit, and a tool-call cap enforced by a PreToolUse hook. Only observable events are
recorded: assistant text, tool calls and results, approvals, usage. Thinking content is never exposed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    SessionStore,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select, update

from opspilot.agent.events import EventSink, cancel_channel
from opspilot.agent.policy import split_tool_name, tool_access
from opspilot.agent.prompts import SYSTEM_PROMPT
from opspilot.agent.schema import TurnOutput, inline_schema
from opspilot.agent.session_store import PostgresSessionStore
from opspilot.auth.service import load_principal
from opspilot.config import Settings
from opspilot.db.base import Database
from opspilot.db.models import (
    AgentEvent,
    AgentSession,
    AgentSessionStatus,
    Approval,
    ApprovalStatus,
    Incident,
    IncidentSeverity,
)
from opspilot.logging import get_logger
from opspilot.mcpservers.errors import parse_tool_error
from opspilot.mcpservers.tokens import ToolTokenIssuer
from opspilot.rag.cost import ModelPricing

log = get_logger(__name__)


@dataclass
class _ToolCall:
    server: str
    tool: str
    input: dict[str, Any]
    started: float
    ok: bool | None = None
    output: Any = None


@dataclass
class TurnState:
    calls: dict[str, _ToolCall] = field(default_factory=dict)
    tool_calls: int = 0
    approvals: list[str] = field(default_factory=list)


class AgentRunner:
    def __init__(
        self, settings: Settings, db: Database, redis: Redis, issuer: ToolTokenIssuer, pricing: ModelPricing
    ) -> None:
        self._settings = settings
        self._db = db
        self._redis = redis
        self._issuer = issuer
        self._pricing = pricing
        self._store = PostgresSessionStore(db)
        self._urls = {
            "knowledge": settings.mcp_knowledge_url,
            "github": settings.mcp_github_url,
            "observability": settings.mcp_observability_url,
            "operations": settings.mcp_operations_url,
        }

    async def run_turn(self, session_id: uuid.UUID, message_seq: int) -> AgentSessionStatus | None:
        async with self._db.session() as s:
            session: AgentSession | None = await s.scalar(
                update(AgentSession)
                .where(AgentSession.id == session_id, AgentSession.status == AgentSessionStatus.QUEUED)
                .values(status=AgentSessionStatus.RUNNING, error=None)
                .returning(AgentSession)
            )
            await s.commit()
            if session is None:
                return None  # duplicate delivery or not queued
            message = await s.scalar(
                select(AgentEvent).where(AgentEvent.session_id == session_id, AgentEvent.seq == message_seq)
            )
            principal = await load_principal(s, session.user_id, session.workspace_id)
        turn = session.turns + 1
        sink = EventSink(self._db, self._redis, session_id, turn)
        if message is None or message.type != "user_message":
            return await self._finish(session, sink, AgentSessionStatus.FAILED, error="Turn message not found")
        if session.cancel_requested:
            return await self._finish(session, sink, AgentSessionStatus.CANCELLED, error="Cancelled before start")

        state = TurnState()
        servers, allowed, disallowed = tool_access(principal)
        ttl = self._settings.agent_time_budget_seconds + 60  # tool tokens live only as long as this turn

        max_calls = self._settings.agent_max_tool_calls

        async def budget_hook(input_data: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput:
            if split_tool_name(str(input_data.get("tool_name", ""))) is None:
                return {}
            if state.tool_calls >= self._settings.agent_max_tool_calls:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Tool-call budget of {max_calls} reached; "
                        "finish with the evidence you have.",
                    }
                }
            state.tool_calls += 1
            return {}

        # Deterministic per-session directory: the SDK derives the session-store project key from the cwd, so every
        # worker resuming this session must use the same path. It holds no project files.
        workdir = os.path.join(tempfile.gettempdir(), "opspilot-agent", str(session_id))
        os.makedirs(workdir, exist_ok=True)
        env = {
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ENABLE_TOOL_SEARCH": "false",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CONFIG_DIR": f"{workdir}/.claude",
        }
        if self._settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self._settings.anthropic_api_key.get_secret_value()
        if self._settings.anthropic_base_url:
            env["ANTHROPIC_BASE_URL"] = self._settings.anthropic_base_url
        options = ClaudeAgentOptions(
            model=self._settings.agent_model,
            effort=self._settings.agent_effort,
            system_prompt=SYSTEM_PROMPT,
            tools=[],
            setting_sources=[],
            mcp_servers={
                srv: {
                    "type": "http",
                    "url": self._urls[srv],
                    "headers": {
                        "Authorization": "Bearer "
                        + self._issuer.issue(principal, srv, session_id=str(session_id), ttl_seconds=ttl)
                    },
                }
                for srv in servers
            },
            strict_mcp_config=True,
            allowed_tools=allowed,
            disallowed_tools=disallowed,
            permission_mode="dontAsk",
            max_turns=self._settings.agent_max_turns,
            max_budget_usd=self._settings.agent_max_budget_usd,
            output_format={"type": "json_schema", "schema": inline_schema(TurnOutput)},
            hooks={"PreToolUse": [HookMatcher(matcher="mcp__.*", hooks=[budget_hook])]},
            # The SDK probes optional SessionStore methods at runtime; append/load/list/delete/list_subkeys exist.
            session_store=cast(SessionStore, self._store),
            resume=session.sdk_session_id,
            cwd=workdir,
            env=env,
            stderr=lambda line: log.debug("agent_cli_stderr", line=line[:500]),
        )
        await sink.emit(
            "turn_started",
            {"message_seq": message_seq, "model": self._settings.agent_model, "servers": servers, "tools": allowed},
        )
        result: ResultMessage | None = None
        cancelled = False
        started = time.perf_counter()
        try:
            async with ClaudeSDKClient(options=options) as client:
                watcher = asyncio.create_task(self._watch_cancel(session_id, client))
                try:
                    async with asyncio.timeout(self._settings.agent_time_budget_seconds):
                        await client.query(str(message.payload.get("text", "")))
                        async for msg in client.receive_response():
                            if isinstance(msg, ResultMessage):
                                result = msg
                            else:
                                await self._record(msg, sink, state)
                finally:
                    cancelled = watcher.done() and not watcher.cancelled() and watcher.result()
                    watcher.cancel()
        except TimeoutError:
            return await self._finish(
                session,
                sink,
                AgentSessionStatus.FAILED,
                state=state,
                error=f"Time budget of {self._settings.agent_time_budget_seconds}s exceeded",
            )
        except ClaudeSDKError as exc:
            log.warning("agent_sdk_error", session_id=str(session_id), error=str(exc)[:500])
            return await self._finish(
                session,
                sink,
                AgentSessionStatus.FAILED,
                state=state,
                error=f"Agent runtime error: {exc.__class__.__name__}",
            )
        if cancelled:
            return await self._finish(
                session, sink, AgentSessionStatus.CANCELLED, state=state, result=result, error="Cancelled by user"
            )
        if result is None:
            return await self._finish(
                session, sink, AgentSessionStatus.FAILED, state=state, error="No result from agent"
            )
        usage = self._usage(result, time.perf_counter() - started)
        if result.is_error or result.subtype != "success":
            return await self._finish(
                session,
                sink,
                AgentSessionStatus.FAILED,
                state=state,
                result=result,
                usage=usage,
                error=_result_error(result),
            )
        try:
            output = TurnOutput.model_validate(result.structured_output)
        except ValidationError as exc:
            return await self._finish(
                session,
                sink,
                AgentSessionStatus.FAILED,
                state=state,
                result=result,
                usage=usage,
                error=f"Invalid structured output: {exc.error_count()} errors",
            )
        return await self._complete(session, sink, state, result, usage, output)

    async def _watch_cancel(self, session_id: uuid.UUID, client: ClaudeSDKClient) -> bool:
        pubsub: Any = self._redis.pubsub()  # redis-py's PubSub is untyped
        await pubsub.subscribe(cancel_channel(session_id))
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is not None:
                    await client.interrupt()
                    return True
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def _record(self, msg: Any, sink: EventSink, state: TurnState) -> None:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    await sink.emit("assistant_text", {"text": block.text})
                elif isinstance(block, ToolUseBlock):
                    parsed = split_tool_name(block.name)
                    if parsed is None:
                        continue  # StructuredOutput: captured from the result message
                    server, tool = parsed
                    state.calls[block.id] = _ToolCall(server, tool, dict(block.input), time.perf_counter())
                    await sink.emit(
                        "tool_call", {"tool_use_id": block.id, "server": server, "tool": tool, "input": block.input}
                    )
                # ThinkingBlock and other internal blocks are intentionally not recorded or exposed.
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in state.calls:
                    await self._record_result(block, sink, state)

    async def _record_result(self, block: ToolResultBlock, sink: EventSink, state: TurnState) -> None:
        call = state.calls[block.tool_use_id]
        text = _result_text(block.content)
        if block.is_error:
            output: Any = {"error": parse_tool_error(text)}
        else:
            try:
                output = json.loads(text)
            except ValueError:
                output = {"text": text}
        call.ok, call.output = not block.is_error, output
        await sink.emit(
            "tool_result",
            {
                "tool_use_id": block.tool_use_id,
                "server": call.server,
                "tool": call.tool,
                "is_error": bool(block.is_error),
                "duration_ms": round((time.perf_counter() - call.started) * 1000, 1),
                "output": output,
            },
        )
        if (
            call.ok
            and call.tool == "request_deployment_rollback"
            and isinstance(output, dict)
            and output.get("approval_id")
        ):
            state.approvals.append(str(output["approval_id"]))
            await sink.emit(
                "approval_requested",
                {
                    k: output.get(k)
                    for k in ("approval_id", "status", "target_resource", "arguments", "expected_impact", "expires_at")
                },
            )
        if call.ok and call.tool == "execute_approved_rollback":
            await sink.emit("action_executed", output if isinstance(output, dict) else {"output": output})

    def _usage(self, result: ResultMessage, wall_seconds: float) -> dict[str, Any]:
        u = result.usage or {}
        inp, out = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
        cr, cw = int(u.get("cache_read_input_tokens", 0) or 0), int(u.get("cache_creation_input_tokens", 0) or 0)
        p = self._pricing
        cost = (
            inp * p.input_per_mtok + out * p.output_per_mtok + cr * p.cache_read_per_mtok + cw * p.cache_write_per_mtok
        ) / 1e6
        return {
            "model": self._settings.agent_model,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw,
            "estimated_cost_usd": round(cost, 6),
            "sdk_reported_cost_usd": result.total_cost_usd,
            "num_turns": result.num_turns,
            "duration_ms": result.duration_ms,
            "duration_api_ms": result.duration_api_ms,
            "wall_ms": round(wall_seconds * 1000),
        }

    async def _complete(
        self,
        session: AgentSession,
        sink: EventSink,
        state: TurnState,
        result: ResultMessage,
        usage: dict[str, Any],
        output: TurnOutput,
    ) -> AgentSessionStatus:
        checks = verify_evidence(output, state)
        report = output.report.model_dump(mode="json") if output.report else None
        incident_id = session.incident_id
        async with self._db.session() as s:
            if report is not None:
                severity = IncidentSeverity.SEV2
                if incident_id is None:
                    inc = Incident(
                        workspace_id=session.workspace_id,
                        title=session.title[:300],
                        severity=severity,
                        service=(report["relevant_services"] or [None])[0],
                        summary=report["incident_summary"],
                        report=report,
                        created_by=session.user_id,
                    )
                    s.add(inc)
                    await s.flush()
                    incident_id = inc.id
                else:
                    existing = await s.get(Incident, incident_id)
                    if existing is not None:
                        existing.report, existing.summary = report, report["incident_summary"]
            approval_ids = sorted({*session.approval_ids, *state.approvals})
            if incident_id is not None and approval_ids:
                await s.execute(
                    update(Approval)
                    .where(Approval.id.in_([uuid.UUID(a) for a in approval_ids]), Approval.incident_id.is_(None))
                    .values(incident_id=incident_id)
                )
            pending = await s.scalar(
                select(Approval.id)
                .where(
                    Approval.id.in_([uuid.UUID(a) for a in approval_ids] or [uuid.uuid4()]),
                    Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
                )
                .limit(1)
            )
            await s.commit()
        status = AgentSessionStatus.AWAITING_APPROVAL if pending else AgentSessionStatus.COMPLETED
        await sink.emit(
            "answer", {"answer": output.answer, "has_report": report is not None, "evidence_checks": checks}
        )
        if report is not None:
            await sink.emit("report", {"incident_id": str(incident_id), "report": report, "evidence_checks": checks})
        return await self._finish(
            session,
            sink,
            status,
            state=state,
            result=result,
            usage=usage,
            answer=output.answer,
            report=report,
            incident_id=incident_id,
            approvals=approval_ids,
        )

    async def _finish(
        self,
        session: AgentSession,
        sink: EventSink,
        status: AgentSessionStatus,
        *,
        state: TurnState | None = None,
        result: ResultMessage | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        answer: str | None = None,
        report: dict[str, Any] | None = None,
        incident_id: uuid.UUID | None = None,
        approvals: list[str] | None = None,
    ) -> AgentSessionStatus:
        total_usage = _add_usage(session.usage or {}, usage or {})
        values: dict[str, Any] = {
            "status": status,
            "turns": session.turns + 1,
            "error": error,
            "usage": total_usage,
            "tool_calls": session.tool_calls + (state.tool_calls if state else 0),
            "updated_at": datetime.now(UTC),
            "cancel_requested": False,
        }
        if result is not None and result.session_id:
            values["sdk_session_id"] = result.session_id
        if answer is not None:
            values["answer"] = answer
        if report is not None:
            values["report"] = report
        if incident_id is not None:
            values["incident_id"] = incident_id
        if approvals is not None:
            values["approval_ids"] = approvals
        async with self._db.session() as s:
            await s.execute(update(AgentSession).where(AgentSession.id == session.id).values(**values))
            await s.commit()
        await sink.emit("turn_finished", {"status": status.value, "error": error, "usage": usage or {}})
        log.info(
            "agent_turn_finished",
            session_id=str(session.id),
            status=status.value,
            error=error,
            tool_calls=state.tool_calls if state else 0,
        )
        return status


def verify_evidence(output: TurnOutput, state: TurnState) -> dict[str, Any]:
    """Deterministic check that each evidence item refers to a tool that actually succeeded in this turn, and that
    cited chunk ids / commit SHAs appear in those tools' results."""
    if output.report is None:
        return {"checked": 0, "unverified": []}
    called = {c.tool: c for c in state.calls.values() if c.ok}
    blob = json.dumps([c.output for c in state.calls.values() if c.ok], default=str)
    unverified = []
    for ev in output.report.evidence:
        tool_ok = any(t in ev.source for t in called)
        ids = [tok.strip(".,;:()[]") for tok in ev.source.replace("=", " ").split()]
        ids = [
            i
            for i in ids
            if (len(i) == 36 and i.count("-") == 4) or (len(i) >= 7 and all(c in "0123456789abcdef" for c in i))
        ]
        ids_ok = all(i in blob for i in ids)
        if not (tool_ok and ids_ok):
            unverified.append(
                {
                    "id": ev.id,
                    "source": ev.source,
                    "reason": "tool not called" if not tool_ok else "identifier not found in tool results",
                }
            )
    return {"checked": len(output.report.evidence), "unverified": unverified}


def _result_error(result: ResultMessage) -> str:
    parts = [f"Agent stopped ({result.subtype}"]
    if result.api_error_status:
        parts.append(f", model API HTTP {result.api_error_status}")
    parts.append(")")
    detail = "; ".join(result.errors or []) or (result.result or "")
    return "".join(parts) + (f": {detail[:500]}" if detail else "")


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def _add_usage(total: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    out = dict(total)
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "estimated_cost_usd",
    ):
        if k in turn:
            out[k] = (
                round(float(out.get(k, 0)) + float(turn[k]), 6)
                if k == "estimated_cost_usd"
                else int(out.get(k, 0)) + int(turn[k])
            )
    if "model" in turn:
        out["model"] = turn["model"]
    return out

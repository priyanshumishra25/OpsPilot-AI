from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sse_starlette import EventSourceResponse

from opspilot.agent.events import channel
from opspilot.agent.service import AgentService
from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep, enforce_rate_limit
from opspilot.auth.principal import Permission
from opspilot.container import Container
from opspilot.db.models import AgentEvent, AgentSession, AgentSessionStatus
from opspilot.errors import NotConfiguredError

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
TERMINAL = {s.value for s in AgentSessionStatus} - {"queued", "running"}


class MessageIn(BaseModel):
    message: str = Field(min_length=2, max_length=4000)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    status: str
    model: str
    turns: int
    tool_calls: int
    usage: dict[str, Any]
    answer: str | None
    report: dict[str, Any] | None
    incident_id: uuid.UUID | None
    approval_ids: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    turn: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class SessionDetailOut(SessionOut):
    events: list[EventOut]


def _service(container: Container, *, starting: bool = False) -> AgentService:
    if container.agent is None:
        raise NotConfiguredError("The agent is not configured (requires OPSPILOT_MCP_SIGNING_PRIVATE_KEY)")
    if starting and container.settings.anthropic_api_key is None:
        raise NotConfiguredError("The agent needs a model API key (ANTHROPIC_API_KEY); none is configured")
    return container.agent


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_202_ACCEPTED)
async def create_session(body: MessageIn, principal: PrincipalDep, container: ContainerDep) -> AgentSession:
    await enforce_rate_limit(container, "agent", str(principal.user_id), container.settings.rate_limit_agent_per_minute)
    return await _service(container, starting=True).create(principal, body.message)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[AgentSession]:
    principal.require(Permission.QUERY)
    rows = await session.scalars(
        select(AgentSession)
        .where(AgentSession.workspace_id == principal.workspace_id, AgentSession.user_id == principal.user_id)
        .order_by(AgentSession.created_at.desc())
        .limit(limit)
    )
    return list(rows)


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: uuid.UUID, principal: PrincipalDep, container: ContainerDep, session: SessionDep
) -> dict[str, Any]:
    s = await _service(container).get_owned(session, principal, session_id)
    events = await session.scalars(select(AgentEvent).where(AgentEvent.session_id == s.id).order_by(AgentEvent.seq))
    return {**SessionOut.model_validate(s).model_dump(), "events": [EventOut.model_validate(e) for e in events]}


@router.post("/sessions/{session_id}/messages", response_model=SessionOut, status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    session_id: uuid.UUID, body: MessageIn, principal: PrincipalDep, container: ContainerDep
) -> AgentSession:
    await enforce_rate_limit(container, "agent", str(principal.user_id), container.settings.rate_limit_agent_per_minute)
    return await _service(container, starting=True).post_message(principal, session_id, body.message)


@router.post("/sessions/{session_id}/cancel", response_model=SessionOut)
async def cancel(session_id: uuid.UUID, principal: PrincipalDep, container: ContainerDep) -> AgentSession:
    return await _service(container).cancel(principal, session_id)


@router.get("/sessions/{session_id}/stream")
async def stream(
    session_id: uuid.UUID,
    request: Request,
    principal: PrincipalDep,
    container: ContainerDep,
    session: SessionDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> EventSourceResponse:
    """Replays events after `after` (or Last-Event-ID) from the database, then follows live ones. Ends when the
    session is idle and all events were sent."""
    owned = await _service(container).get_owned(session, principal, session_id)
    last = int(request.headers.get("last-event-id") or after)
    return EventSourceResponse(_follow(container, owned.id, last), ping=15)


async def _follow(container: Container, session_id: uuid.UUID, last: int) -> AsyncIterator[dict[str, str]]:
    pubsub: Any = container.redis.pubsub()
    await pubsub.subscribe(channel(session_id))
    try:
        while True:
            async with container.db.session() as s:
                events = list(
                    await s.scalars(
                        select(AgentEvent)
                        .where(AgentEvent.session_id == session_id, AgentEvent.seq > last)
                        .order_by(AgentEvent.seq)
                        .limit(200)
                    )
                )
                state = await s.scalar(select(AgentSession.status).where(AgentSession.id == session_id))
            for e in events:
                last = e.seq
                yield {
                    "id": str(e.seq),
                    "event": e.type,
                    "data": json.dumps(
                        {"seq": e.seq, "turn": e.turn, "payload": e.payload, "created_at": e.created_at.isoformat()},
                        default=str,
                    ),
                }
            if not events and state is not None and state.value in TERMINAL:
                yield {"event": "idle", "data": json.dumps({"status": state.value})}
                return
            await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            await asyncio.sleep(0)
    finally:
        await pubsub.aclose()

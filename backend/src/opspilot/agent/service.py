"""Agent session lifecycle used by the API: create, follow up, cancel. Turns are executed by the agent worker."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.agent.events import EventSink, cancel_channel
from opspilot.audit import record_audit
from opspilot.auth.principal import Permission, Principal
from opspilot.db.base import Database
from opspilot.db.models import AgentSession, AgentSessionStatus
from opspilot.errors import ConflictError, NotFoundError
from opspilot.queue.base import JobQueue

AGENT_STREAM = "opspilot:agent-runs"
ACTIVE = (AgentSessionStatus.QUEUED, AgentSessionStatus.RUNNING)


class AgentService:
    def __init__(self, db: Database, redis: Redis, queue: JobQueue, *, model: str) -> None:
        self._db, self._redis, self._queue, self._model = db, redis, queue, model

    async def get_owned(self, s: AsyncSession, principal: Principal, session_id: uuid.UUID) -> AgentSession:
        session = await s.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == principal.workspace_id,
                AgentSession.user_id == principal.user_id,
            )
        )
        if session is None:
            raise NotFoundError("Session not found")
        return session

    async def create(self, principal: Principal, message: str) -> AgentSession:
        principal.require(Permission.QUERY)
        async with self._db.session() as s:
            session = AgentSession(
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                title=message.strip()[:300],
                status=AgentSessionStatus.QUEUED,
                model=self._model,
                usage={},
                approval_ids=[],
            )
            s.add(session)
            record_audit(
                s,
                action="agent.session.created",
                resource_type="agent_session",
                resource_id=str(session.id),
                workspace_id=principal.workspace_id,
                actor_user_id=principal.user_id,
            )
            await s.commit()
        await self._enqueue_message(session, message, turn=1)
        return session

    async def post_message(self, principal: Principal, session_id: uuid.UUID, message: str) -> AgentSession:
        async with self._db.session() as s:
            session = await self.get_owned(s, principal, session_id)
            row: AgentSession | None = await s.scalar(
                update(AgentSession)
                .where(AgentSession.id == session.id, AgentSession.status.not_in(ACTIVE))
                .values(status=AgentSessionStatus.QUEUED, cancel_requested=False)
                .returning(AgentSession)
            )
            if row is None:
                raise ConflictError("A turn is already running for this session")
            await s.commit()
        await self._enqueue_message(row, message, turn=row.turns + 1)
        return row

    async def cancel(self, principal: Principal, session_id: uuid.UUID) -> AgentSession:
        async with self._db.session() as s:
            session = await self.get_owned(s, principal, session_id)
            if session.status not in ACTIVE:
                raise ConflictError(f"Session is {session.status.value}; nothing to cancel")
            await s.execute(update(AgentSession).where(AgentSession.id == session.id).values(cancel_requested=True))
            # A queued turn that has not started is cancelled immediately; a running one is interrupted by the worker.
            await s.execute(
                update(AgentSession)
                .where(AgentSession.id == session.id, AgentSession.status == AgentSessionStatus.QUEUED)
                .values(status=AgentSessionStatus.CANCELLED, error="Cancelled by user")
            )
            record_audit(
                s,
                action="agent.session.cancelled",
                resource_type="agent_session",
                resource_id=str(session.id),
                workspace_id=principal.workspace_id,
                actor_user_id=principal.user_id,
            )
            await s.commit()
            await s.refresh(session)
        await self._redis.publish(cancel_channel(session.id), "cancel")
        return session

    async def _enqueue_message(self, session: AgentSession, message: str, *, turn: int) -> None:
        seq = await EventSink(self._db, self._redis, session.id, turn).emit("user_message", {"text": message})
        await self._queue.publish(f"{session.id}:{seq}")

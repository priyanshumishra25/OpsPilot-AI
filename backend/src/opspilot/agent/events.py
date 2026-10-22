from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select

from opspilot.db.base import Database
from opspilot.db.models import AgentEvent
from opspilot.logging import get_logger, redact

log = get_logger(__name__)

MAX_PAYLOAD_CHARS = 20_000


def channel(session_id: uuid.UUID | str) -> str:
    return f"opspilot:agent-events:{session_id}"


def cancel_channel(session_id: uuid.UUID | str) -> str:
    return f"opspilot:agent-cancel:{session_id}"


class EventSink:
    """Persists observable agent events and notifies live viewers. The database is the source of truth; Redis
    pub/sub only wakes up SSE streams."""

    def __init__(self, db: Database, redis: Redis, session_id: uuid.UUID, turn: int) -> None:
        self._db, self._redis, self._sid, self.turn = db, redis, session_id, turn
        self._seq: int | None = None

    async def emit(self, type_: str, payload: dict[str, Any]) -> int:
        payload = _bounded(redact(payload))
        async with self._db.session() as s:
            if self._seq is None:
                self._seq = int(
                    await s.scalar(
                        select(func.coalesce(func.max(AgentEvent.seq), 0)).where(AgentEvent.session_id == self._sid)
                    )
                    or 0
                )
            self._seq += 1
            s.add(AgentEvent(session_id=self._sid, seq=self._seq, turn=self.turn, type=type_, payload=payload))
            await s.commit()
        try:
            await self._redis.publish(channel(self._sid), str(self._seq))
        except Exception:  # noqa: BLE001 - live viewers fall back to polling the database
            log.warning("agent_event_publish_failed", session_id=str(self._sid), seq=self._seq)
        return self._seq


def _bounded(payload: Any) -> Any:
    text = json.dumps(payload, default=str)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return json.loads(text)
    return {"truncated": True, "preview": text[:MAX_PAYLOAD_CHARS]}

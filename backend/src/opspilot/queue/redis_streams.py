from __future__ import annotations

import socket
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from opspilot.queue.base import QueueMessage

STREAM = "opspilot:ingestion"
DLQ_STREAM = "opspilot:ingestion:dlq"
GROUP = "ingestion-workers"


class RedisStreamQueue:
    def __init__(self, redis: Redis, *, consumer: str | None = None, claim_idle_ms: int = 300_000) -> None:
        self._redis = redis
        self._consumer = consumer or f"{socket.gethostname()}"
        self._claim_idle_ms = claim_idle_ms
        self._group_ready = False

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def publish(self, job_id: str) -> None:
        await self._redis.xadd(STREAM, {"job_id": job_id}, maxlen=100_000, approximate=True)

    async def receive(self, *, max_messages: int, wait_seconds: float) -> list[QueueMessage]:
        await self._ensure_group()
        # Reclaim messages delivered to crashed consumers first.
        # redis-py types stream replies loosely; shapes are validated while parsing below.
        autoclaim: Any = await self._redis.xautoclaim(
            STREAM, GROUP, self._consumer, min_idle_time=self._claim_idle_ms, count=max_messages
        )
        entries: list[Any] = list(autoclaim[1])
        if not entries:
            resp: Any = await self._redis.xreadgroup(
                GROUP, self._consumer, {STREAM: ">"}, count=max_messages, block=int(wait_seconds * 1000)
            )
            for _, stream_entries in resp or []:
                entries.extend(stream_entries)
        out: list[QueueMessage] = []
        for msg_id, fields in entries:
            job_id = fields.get(b"job_id") or fields.get("job_id")
            if job_id is None:
                await self._redis.xack(STREAM, GROUP, msg_id)
                continue
            out.append(QueueMessage(job_id=_s(job_id), receipt=_s(msg_id)))
        return out

    async def ack(self, message: QueueMessage) -> None:
        await self._redis.xack(STREAM, GROUP, message.receipt)

    async def publish_dead_letter(self, job_id: str, reason: str) -> None:
        await self._redis.xadd(DLQ_STREAM, {"job_id": job_id, "reason": reason[:2000]}, maxlen=10_000)

    async def ping(self) -> None:
        await self._redis.ping()

    async def close(self) -> None:
        await self._redis.aclose()


def _s(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else v

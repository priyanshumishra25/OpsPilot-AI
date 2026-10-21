"""Job queue abstraction.

Delivery is at-least-once. The `ingestion_jobs` table is the source of truth for job state; a worker
claims a job with an atomic conditional UPDATE, so a duplicated or stale message is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QueueMessage:
    job_id: str
    receipt: str


class JobQueue(Protocol):
    async def publish(self, job_id: str) -> None: ...
    async def receive(self, *, max_messages: int, wait_seconds: float) -> list[QueueMessage]: ...
    async def ack(self, message: QueueMessage) -> None: ...
    async def publish_dead_letter(self, job_id: str, reason: str) -> None: ...
    async def ping(self) -> None: ...
    async def close(self) -> None: ...

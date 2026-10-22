"""Agent worker process: executes agent turns from the `opspilot:agent-runs` stream."""

from __future__ import annotations

import asyncio
import signal
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from opspilot.agent.runner import AgentRunner
from opspilot.db.base import Database
from opspilot.db.models import AgentSession, AgentSessionStatus
from opspilot.logging import get_logger
from opspilot.queue.base import JobQueue, QueueMessage

log = get_logger(__name__)


class AgentWorker:
    def __init__(
        self, db: Database, queue: JobQueue, runner: AgentRunner, *, concurrency: int, stale_after_seconds: int
    ) -> None:
        self._db, self._queue, self._runner = db, queue, runner
        self._sem = asyncio.Semaphore(concurrency)
        self._stale = stale_after_seconds
        self._stopping = asyncio.Event()

    async def recover_stale(self) -> int:
        """Sessions left RUNNING by a crashed worker are failed (the turn's side effects are approval-gated)."""
        async with self._db.session() as s:
            rows = await s.execute(
                update(AgentSession)
                .where(
                    AgentSession.status == AgentSessionStatus.RUNNING,
                    AgentSession.updated_at < datetime.now(UTC) - timedelta(seconds=self._stale),
                )
                .values(
                    status=AgentSessionStatus.FAILED,
                    error="Agent worker stopped during this turn; send the message again",
                )
                .returning(AgentSession.id)
            )
            n = len(list(rows))
            await s.commit()
        return n

    async def handle(self, msg: QueueMessage) -> None:
        async with self._sem:
            try:
                session_id, seq = msg.job_id.split(":")
                await self._runner.run_turn(uuid.UUID(session_id), int(seq))
            except ValueError:
                log.warning("invalid_agent_message", job_id=msg.job_id)
            except Exception:
                log.exception("agent_turn_crashed", job_id=msg.job_id)
            finally:
                await self._queue.ack(msg)

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stopping.set)
        recovered = await self.recover_stale()
        log.info("agent_worker_started", recovered_stale_sessions=recovered)
        tasks: set[asyncio.Task[None]] = set()
        while not self._stopping.is_set():
            try:
                messages = await self._queue.receive(max_messages=2, wait_seconds=2)
            except Exception:
                log.exception("agent_queue_receive_failed")
                await asyncio.sleep(2)
                continue
            for m in messages:
                t = asyncio.create_task(self.handle(m))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("agent_worker_stopped")

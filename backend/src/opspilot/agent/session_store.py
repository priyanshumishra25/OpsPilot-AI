"""Postgres-backed Claude Agent SDK `SessionStore`.

The SDK subprocess writes transcripts to local disk; this mirrors them into Postgres so a session can be resumed
by any agent worker (containers are ephemeral). Validated with the SDK's own conformance suite.
"""

from __future__ import annotations

import time
from typing import Any

from claude_agent_sdk import SessionKey, SessionStoreListEntry
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from opspilot.db.base import Database
from opspilot.db.models import AgentTranscriptEntry as Row


class PostgresSessionStore:
    """Implements append/load (required) plus list_sessions, delete and list_subkeys."""

    def __init__(self, db: Database, *, namespace: str = "") -> None:
        self._db = db
        self._ns = namespace  # prefixes project keys; used to isolate tests

    def _pk(self, project_key: str) -> str:
        return f"{self._ns}{project_key}"[:256]

    async def append(self, key: SessionKey, entries: list[Any]) -> None:
        if not entries:
            return
        values = [
            {
                "project_key": self._pk(key["project_key"]),
                "session_id": key["session_id"],
                "subpath": key.get("subpath") or "",
                "entry_uuid": e.get("uuid") if isinstance(e, dict) else None,
                "entry": e,
            }
            for e in entries
        ]
        stmt = insert(Row).values(values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["project_key", "session_id", "subpath", "entry_uuid"],
            index_where=Row.entry_uuid.is_not(None),
        )
        async with self._db.session() as s:
            await s.execute(stmt)
            await s.commit()

    async def load(self, key: SessionKey) -> list[Any] | None:
        async with self._db.session() as s:
            rows = list(
                await s.scalars(
                    select(Row.entry)
                    .where(
                        Row.project_key == self._pk(key["project_key"]),
                        Row.session_id == key["session_id"],
                        Row.subpath == (key.get("subpath") or ""),
                    )
                    .order_by(Row.id)
                )
            )
        return rows or None

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        async with self._db.session() as s:
            rows = await s.execute(
                select(Row.session_id, func.max(Row.created_at))
                .where(Row.project_key == self._pk(project_key), Row.subpath == "")
                .group_by(Row.session_id)
            )
            return [
                {"session_id": sid, "mtime": int(ts.timestamp() * 1000) if ts else int(time.time() * 1000)}
                for sid, ts in rows
            ]

    async def delete(self, key: SessionKey) -> None:
        stmt = delete(Row).where(Row.project_key == self._pk(key["project_key"]), Row.session_id == key["session_id"])
        if key.get("subpath"):
            stmt = stmt.where(Row.subpath == key["subpath"])
        async with self._db.session() as s:
            await s.execute(stmt)
            await s.commit()

    async def list_subkeys(self, key: Any) -> list[str]:
        async with self._db.session() as s:
            rows = await s.scalars(
                select(Row.subpath)
                .where(
                    Row.project_key == self._pk(key["project_key"]),
                    Row.session_id == key["session_id"],
                    Row.subpath != "",
                )
                .distinct()
            )
            return list(rows)

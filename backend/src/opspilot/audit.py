from __future__ import annotations

import uuid
from typing import Any

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.db.models import AuditLog
from opspilot.logging import redact


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    workspace_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction so it commits atomically with the action."""
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=redact(details or {}),
            trace_id=current_trace_id(),
        )
    )

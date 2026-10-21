"""Approval state machine for privileged actions.

Every transition is a single conditional UPDATE (`... WHERE status = <expected> ... RETURNING`), so concurrent
or replayed requests cannot double-approve or double-execute. An approval binds: requester, workspace, action,
the one tool allowed to consume it, a hash of the exact validated arguments, the target resource, the target's
state snapshot, an expiry, and (once execution starts) a one-time execution id.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.audit import record_audit
from opspilot.auth.principal import Permission, Principal
from opspilot.db.models import Approval, ApprovalStatus
from opspilot.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError


def arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def _commit_denial(
    s: AsyncSession, principal: Principal, approval_id: uuid.UUID, action: str, details: dict[str, Any]
) -> None:
    """Refused operations still fail, but the refusal itself (and any expiry it triggered) is committed so the
    audit trail records it."""
    record_audit(
        s,
        action=action,
        resource_type="approval",
        resource_id=str(approval_id),
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        outcome="denied",
        details=details,
    )
    await s.commit()


class ApprovalService:
    def __init__(self, *, ttl_seconds: int = 1800, require_distinct_approver: bool = True) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._distinct = require_distinct_approver

    async def create(
        self,
        s: AsyncSession,
        principal: Principal,
        *,
        action: str,
        tool_name: str,
        arguments: dict[str, Any],
        target_resource: str,
        target_state: dict[str, Any],
        expected_impact: str,
        reason: str,
        incident_id: uuid.UUID | None = None,
    ) -> Approval:
        approval = Approval(
            workspace_id=principal.workspace_id,
            requested_by=principal.user_id,
            action=action,
            tool_name=tool_name,
            arguments=arguments,
            arguments_hash=arguments_hash(arguments),
            target_resource=target_resource,
            target_state=target_state,
            expected_impact=expected_impact,
            reason=reason,
            incident_id=incident_id,
            status=ApprovalStatus.PENDING,
            expires_at=_now() + self._ttl,
        )
        s.add(approval)
        await s.flush()
        record_audit(
            s,
            action="approval.requested",
            resource_type="approval",
            resource_id=str(approval.id),
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            details={"action": action, "target": target_resource, "arguments": arguments},
        )
        return approval

    async def expire_stale(self, s: AsyncSession, workspace_id: uuid.UUID) -> int:
        rows = await s.execute(
            update(Approval)
            .where(
                Approval.workspace_id == workspace_id,
                Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
                Approval.expires_at <= _now(),
            )
            .values(status=ApprovalStatus.EXPIRED, finished_at=_now())
            .returning(Approval.id)
        )
        expired = list(rows.scalars())
        for approval_id in expired:
            record_audit(
                s,
                action="approval.expired",
                resource_type="approval",
                resource_id=str(approval_id),
                workspace_id=workspace_id,
                actor_user_id=None,
            )
        return len(expired)

    async def get(self, s: AsyncSession, principal: Principal, approval_id: uuid.UUID) -> Approval:
        approval = await s.scalar(
            select(Approval).where(Approval.id == approval_id, Approval.workspace_id == principal.workspace_id)
        )
        if approval is None:
            raise NotFoundError("Approval not found")
        return approval

    async def decide(
        self, s: AsyncSession, principal: Principal, approval_id: uuid.UUID, *, approve: bool, note: str | None
    ) -> Approval:
        principal.require(Permission.APPROVALS_DECIDE)
        current = await self.get(s, principal, approval_id)
        if self._distinct and current.requested_by == principal.user_id:
            await _commit_denial(s, principal, approval_id, "approval.decision_refused", {"reason": "self-approval"})
            raise PermissionDeniedError("The requester cannot approve their own request")
        new_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        row: Approval | None = await s.scalar(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.workspace_id == principal.workspace_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at > _now(),
            )
            .values(status=new_status, decided_by=principal.user_id, decided_at=_now(), decision_note=note)
            .returning(Approval)
        )
        if row is None:
            await self.expire_stale(s, principal.workspace_id)
            await s.refresh(current)
            await _commit_denial(
                s, principal, approval_id, "approval.decision_refused", {"status": current.status.value}
            )
            raise ConflictError(
                f"Approval is {current.status.value} and can no longer be decided",
                details={"status": current.status.value},
            )
        record_audit(
            s,
            action=f"approval.{new_status.value}",
            resource_type="approval",
            resource_id=str(approval_id),
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            details={"note": note},
        )
        return row

    async def begin_execution(
        self,
        s: AsyncSession,
        principal: Principal,
        approval_id: uuid.UUID,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Approval:
        """Consume an APPROVED approval exactly once. The caller must present the exact approved arguments."""
        approval = await self.get(s, principal, approval_id)
        if approval.tool_name != tool_name:
            await _commit_denial(s, principal, approval_id, "approval.execution_refused", {"reason": "wrong tool"})
            raise PermissionDeniedError("This approval does not authorise this tool")
        if approval.arguments_hash != arguments_hash(arguments):
            await _commit_denial(s, principal, approval_id, "approval.argument_mismatch", {"presented": arguments})
            raise PermissionDeniedError("Arguments differ from the approved request")
        if principal.user_id not in (approval.requested_by, approval.decided_by):
            await _commit_denial(s, principal, approval_id, "approval.execution_refused", {"reason": "not a party"})
            raise PermissionDeniedError("Only the requester or the approver may execute this approval")
        execution_id = uuid.uuid4()
        row: Approval | None = await s.scalar(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.APPROVED,
                Approval.execution_id.is_(None),
                Approval.expires_at > _now(),
            )
            .values(status=ApprovalStatus.EXECUTING, execution_id=execution_id, execution_started_at=_now())
            .returning(Approval)
        )
        if row is None:
            await self.expire_stale(s, principal.workspace_id)
            await s.refresh(approval)
            await _commit_denial(
                s, principal, approval_id, "approval.execution_refused", {"status": approval.status.value}
            )
            raise ConflictError(
                f"Approval is {approval.status.value}; it can only be executed once while approved and unexpired",
                details={"status": approval.status.value},
            )
        record_audit(
            s,
            action="approval.execution_started",
            resource_type="approval",
            resource_id=str(approval_id),
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            details={"execution_id": str(execution_id)},
        )
        return row

    async def finish(
        self, s: AsyncSession, approval: Approval, *, succeeded: bool, result: dict[str, Any], actor: uuid.UUID
    ) -> Approval:
        status = ApprovalStatus.SUCCEEDED if succeeded else ApprovalStatus.FAILED
        row: Approval | None = await s.scalar(
            update(Approval)
            .where(
                Approval.id == approval.id,
                Approval.status == ApprovalStatus.EXECUTING,
                Approval.execution_id == approval.execution_id,
            )
            .values(status=status, result=result, finished_at=_now())
            .returning(Approval)
        )
        if row is None:
            raise ValidationFailedError("Approval is not executing under this execution id")
        record_audit(
            s,
            action=f"approval.execution_{status.value}",
            resource_type="approval",
            resource_id=str(approval.id),
            workspace_id=approval.workspace_id,
            actor_user_id=actor,
            outcome="success" if succeeded else "failure",
            details=result,
        )
        return row

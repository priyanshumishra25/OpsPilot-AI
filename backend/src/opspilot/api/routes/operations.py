"""Human-facing endpoints for approvals, incidents, MCP integration status and the demo scenario controller."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep
from opspilot.audit import record_audit
from opspilot.auth.principal import Permission
from opspilot.container import Container
from opspilot.db.models import Approval, ApprovalStatus, Incident
from opspilot.errors import NotConfiguredError, NotFoundError
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.mcp_client.gateway import SERVERS, ServerName

router = APIRouter(prefix="/api/v1", tags=["operations"])


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action: str
    tool_name: str
    arguments: dict[str, Any]
    target_resource: str
    target_state: dict[str, Any]
    expected_impact: str
    reason: str
    incident_id: uuid.UUID | None
    status: str
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None
    decision_note: str | None
    result: dict[str, Any]
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None
    execution_started_at: datetime | None
    finished_at: datetime | None


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    severity: str
    status: str
    service: str | None
    summary: str
    report: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    principal: PrincipalDep,
    container: ContainerDep,
    session: SessionDep,
    status: Annotated[ApprovalStatus | None, Query()] = None,
) -> list[Approval]:
    principal.require(Permission.QUERY)
    await container.approvals.expire_stale(session, principal.workspace_id)
    await session.commit()
    stmt = (
        select(Approval)
        .where(Approval.workspace_id == principal.workspace_id)
        .order_by(Approval.created_at.desc())
        .limit(100)
    )
    if status:
        stmt = stmt.where(Approval.status == status)
    return list(await session.scalars(stmt))


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID, principal: PrincipalDep, container: ContainerDep, session: SessionDep
) -> Approval:
    principal.require(Permission.QUERY)
    await container.approvals.expire_stale(session, principal.workspace_id)
    await session.commit()
    return await container.approvals.get(session, principal, approval_id)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: uuid.UUID, body: DecisionIn, principal: PrincipalDep, container: ContainerDep, session: SessionDep
) -> Approval:
    approval = await container.approvals.decide(session, principal, approval_id, approve=True, note=body.note)
    await session.commit()
    return approval


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: uuid.UUID, body: DecisionIn, principal: PrincipalDep, container: ContainerDep, session: SessionDep
) -> Approval:
    approval = await container.approvals.decide(session, principal, approval_id, approve=False, note=body.note)
    await session.commit()
    return approval


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(principal: PrincipalDep, session: SessionDep) -> list[Incident]:
    principal.require(Permission.QUERY)
    rows = await session.scalars(
        select(Incident)
        .where(Incident.workspace_id == principal.workspace_id)
        .order_by(Incident.created_at.desc())
        .limit(100)
    )
    return list(rows)


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> Incident:
    principal.require(Permission.QUERY)
    inc = await session.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.workspace_id == principal.workspace_id)
    )
    if inc is None:
        raise NotFoundError("Incident not found")
    return inc


@router.get("/integrations")
async def integrations(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """Live status of each MCP server, as seen with the caller's own tool token (so it reflects their access)."""
    if container.mcp is None:
        raise NotConfiguredError("MCP integration is not configured (OPSPILOT_MCP_SIGNING_PRIVATE_KEY)")
    gateway = container.mcp

    async def probe(server: ServerName) -> dict[str, Any]:
        try:
            tools = await asyncio.wait_for(gateway.list_tools(principal, server), timeout=10)
            return {
                "server": server,
                "status": "connected",
                "tools": [{"name": t.name, "description": t.description} for t in tools],
            }
        except Exception as exc:  # noqa: BLE001 - reported per server
            return {"server": server, "status": "unavailable", "error": exc.__class__.__name__, "tools": []}

    results = await asyncio.gather(*(probe(s) for s in SERVERS))
    return {
        "servers": results,
        "github": {
            "token_configured": container.settings.github_token is not None,
            "repositories": sorted(container.settings.github_repositories),
        },
    }


@router.get("/demo/scenarios")
async def demo_scenarios(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    principal.require(Permission.WORKSPACE_ADMIN)
    cp = _control_plane(container)
    return {"environment": "demo", "scenarios": await cp.scenarios(), "services": await cp.services()}


@router.post("/demo/scenarios/{name}/trigger")
async def trigger_scenario(
    name: str, principal: PrincipalDep, container: ContainerDep, session: SessionDep
) -> dict[str, Any]:
    principal.require(Permission.WORKSPACE_ADMIN)
    result = await _control_plane(container).trigger(name)
    record_audit(
        session,
        action="demo.scenario.triggered",
        resource_type="demo",
        resource_id=name,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return result


@router.post("/demo/reset")
async def reset_demo(principal: PrincipalDep, container: ContainerDep, session: SessionDep) -> dict[str, Any]:
    principal.require(Permission.WORKSPACE_ADMIN)
    result = await _control_plane(container).reset()
    record_audit(
        session,
        action="demo.reset",
        resource_type="demo",
        resource_id=None,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return result


def _control_plane(container: Container) -> ControlPlaneClient:
    if container.control_plane_admin is None:
        raise NotConfiguredError("Demo control plane is not configured")
    return container.control_plane_admin

"""Operations MCP server: deployment state, incident reports and human-approved rollbacks (demo environment only).

The model can *request* a rollback (creating a PENDING approval) and *execute* one only after a human approved it
through the OpsPilot API. Execution consumes the approval exactly once, re-checks the live target state, deploys
with compare-and-swap on the control plane, and verifies the outcome. There is no other mutation path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from opspilot.approvals.service import ApprovalService
from opspilot.audit import record_audit
from opspilot.auth.principal import Principal
from opspilot.auth.service import load_principal
from opspilot.config import Settings, get_settings
from opspilot.db.base import Database
from opspilot.db.models import Approval, Incident, IncidentSeverity
from opspilot.errors import ConflictError, NotFoundError, OpsPilotError, ValidationFailedError
from opspilot.incidents.schema import IncidentReport
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.logging import configure_logging
from opspilot.mcpservers.common import caller, create_server, serve, tool_guard

SERVER = "operations"
READ, REQUEST, EXECUTE = "operations:read", "operations:request", "operations:execute"
EXECUTE_TOOL = "execute_approved_rollback"
MIN_HOURS_IN_PRODUCTION = 24.0
MAX_VERSIONS_BACK_WITHOUT_OWNER = 2
Service = Annotated[str, Field(min_length=1, max_length=100)]
Version = Annotated[str, Field(pattern=r"^v\d+\.\d+\.\d+$")]


class DeploymentStatus(BaseModel):
    service: str
    environment: str
    current_version: str
    commit_sha: str
    state_version: int
    deployed_at: str
    deployed_by: str


class RollbackCandidate(BaseModel):
    version: str
    commit_sha: str
    released_at: str
    hours_in_production: float
    eligible: bool
    reasons: list[str]
    requires_owner_approval: bool


class RollbackOptions(BaseModel):
    service: str
    current_version: str
    policy: str
    candidates: list[RollbackCandidate]


class IncidentCreated(BaseModel):
    incident_id: str
    title: str
    severity: str


class ApprovalView(BaseModel):
    approval_id: str
    status: str
    action: str
    target_resource: str
    arguments: dict[str, Any]
    target_state: dict[str, Any]
    expected_impact: str
    expires_at: str
    message: str


class ExecutionResult(BaseModel):
    approval_id: str
    status: Literal["succeeded", "failed"]
    service: str
    from_version: str
    to_version: str
    verified_current_version: str | None
    message: str


POLICY = (
    f"A rollback target must be older than the current version, have served production for at least "
    f"{MIN_HOURS_IN_PRODUCTION:.0f} hours, and not have been rolled back before. Targets more than "
    f"{MAX_VERSIONS_BACK_WITHOUT_OWNER} releases back also need service-owner sign-off."
)


def rollback_candidates(status: dict[str, Any], releases: list[dict[str, Any]]) -> list[RollbackCandidate]:
    current = next(r for r in releases if r["version"] == status["current_version"])
    older = [r for r in releases if r["released_at"] < current["released_at"]]  # newest first
    out = []
    for idx, r in enumerate(older):
        reasons = []
        if r["hours_in_production"] < MIN_HOURS_IN_PRODUCTION:
            reasons.append(f"served production for {r['hours_in_production']}h (< {MIN_HOURS_IN_PRODUCTION:.0f}h)")
        if r["previously_rolled_back"]:
            reasons.append("previously rolled back")
        out.append(
            RollbackCandidate(
                version=r["version"],
                commit_sha=r["commit_sha"],
                released_at=r["released_at"],
                hours_in_production=r["hours_in_production"],
                eligible=not reasons,
                reasons=reasons or ["meets policy"],
                requires_owner_approval=idx >= MAX_VERSIONS_BACK_WITHOUT_OWNER,
            )
        )
    return out


def build(settings: Settings, control_plane: ControlPlaneClient | None = None, db: Database | None = None) -> MCPServer:
    database = db or Database(settings.database_url, pool_size=5)
    deploy_token = settings.control_plane_deploy_token.get_secret_value() if settings.control_plane_deploy_token else ""
    cp = control_plane or ControlPlaneClient(settings.control_plane_url, deploy_token)
    approvals = ApprovalService(
        ttl_seconds=settings.approval_ttl_seconds, require_distinct_approver=settings.approval_require_distinct_approver
    )
    services = tuple(settings.demo_services)
    assert settings.mcp_verify_public_key, "OPSPILOT_MCP_VERIFY_PUBLIC_KEY is required"
    mcp = create_server(
        SERVER,
        public_key_b64=settings.mcp_verify_public_key,
        base_url=settings.mcp_operations_url,
        required_scope=READ,
        instructions="Deployment state and remediation for the isolated DEMO environment only. Rollbacks require a "
        "human approval: call request_deployment_rollback, tell the user an approval is pending, and only call "
        "execute_approved_rollback after the user says it was approved. You cannot approve requests.",
    )

    def check(service: str) -> str:
        if service not in services:
            raise ValidationFailedError(f"Unknown or non-demo service '{service}'")
        return service

    async def principal() -> Principal:
        c = caller()
        async with database.session() as s:
            return await load_principal(s, c.user_id, c.workspace_id)

    @mcp.tool(description="Current deployed version and state of a demo service.")
    @tool_guard(SERVER, READ)
    async def get_deployment_status(service: Service) -> DeploymentStatus:
        st = await cp.status(check(service))
        return DeploymentStatus(**{k: st[k] for k in DeploymentStatus.model_fields})

    @mcp.tool(description="Releases a service could roll back to, with eligibility under the rollback policy.")
    @tool_guard(SERVER, READ)
    async def get_available_rollback_versions(service: Service) -> RollbackOptions:
        st = await cp.status(check(service))
        candidates = rollback_candidates(st, await cp.releases(service))
        return RollbackOptions(
            service=service, current_version=st["current_version"], policy=POLICY, candidates=candidates
        )

    @mcp.tool(description="Persist a structured incident investigation report. Returns the incident id.")
    @tool_guard(SERVER, REQUEST)
    async def create_incident_report(
        title: Annotated[str, Field(min_length=5, max_length=300)],
        severity: Literal["sev1", "sev2", "sev3"],
        service: Service,
        report: IncidentReport,
    ) -> IncidentCreated:
        p = await principal()
        async with database.session() as s:
            incident = Incident(
                workspace_id=p.workspace_id,
                title=title,
                severity=IncidentSeverity(severity),
                service=check(service),
                summary=report.incident_summary,
                report=report.model_dump(mode="json"),
                created_by=p.user_id,
            )
            s.add(incident)
            await s.flush()
            record_audit(
                s,
                action="incident.created",
                resource_type="incident",
                resource_id=str(incident.id),
                workspace_id=p.workspace_id,
                actor_user_id=p.user_id,
                details={"title": title, "severity": severity},
            )
            await s.commit()
            return IncidentCreated(incident_id=str(incident.id), title=title, severity=severity)

    @mcp.tool(
        description="Request a rollback of a demo service to an eligible earlier version. Creates a PENDING "
        "approval that a human must approve in OpsPilot. Does not change anything by itself."
    )
    @tool_guard(SERVER, REQUEST)
    async def request_deployment_rollback(
        service: Service,
        target_version: Version,
        reason: Annotated[str, Field(min_length=10, max_length=1000)],
        incident_id: Annotated[str | None, Field(min_length=36, max_length=36)] = None,
    ) -> ApprovalView:
        p = await principal()
        st = await cp.status(check(service))
        candidates = {c.version: c for c in rollback_candidates(st, await cp.releases(service))}
        cand = candidates.get(target_version)
        if cand is None:
            raise ValidationFailedError(f"{target_version} is not an earlier release of {service}")
        if not cand.eligible:
            raise ValidationFailedError(f"{target_version} is not eligible: {'; '.join(cand.reasons)}")
        incident_uuid = uuid.UUID(incident_id) if incident_id else None
        async with database.session() as s:
            if incident_uuid is not None:
                inc = await s.get(Incident, incident_uuid)
                if inc is None or inc.workspace_id != p.workspace_id:
                    raise NotFoundError("Incident not found")
            approval = await approvals.create(
                s,
                p,
                action="deployment.rollback",
                tool_name=EXECUTE_TOOL,
                arguments={"service": service, "target_version": target_version},
                target_resource=f"demo/{service}",
                target_state={
                    "from_version": st["current_version"],
                    "state_version": st["state_version"],
                    "commit_sha": st["commit_sha"],
                    "to_commit_sha": cand.commit_sha,
                },
                expected_impact=(
                    f"Redeploys {service} from {st['current_version']} to {target_version} "
                    f"(commit {cand.commit_sha[:12]}) in the demo environment using a rolling deploy."
                    + (
                        " Requires service-owner sign-off (more than two releases back)."
                        if cand.requires_owner_approval
                        else ""
                    )
                ),
                reason=reason,
                incident_id=incident_uuid,
            )
            await s.commit()
            return _view(approval, "Approval requested. A human approver must approve it in OpsPilot before execution.")

    @mcp.tool(
        description="Execute a rollback that a human has APPROVED. Arguments must exactly match the approved "
        "request. Each approval can be executed once."
    )
    @tool_guard(SERVER, EXECUTE)
    async def execute_approved_rollback(
        approval_id: Annotated[str, Field(min_length=36, max_length=36)],
        service: Service,
        target_version: Version,
    ) -> ExecutionResult:
        p = await principal()
        try:
            approval_uuid = uuid.UUID(approval_id)
        except ValueError as exc:
            raise NotFoundError("Approval not found") from exc
        async with database.session() as s:
            approval = await approvals.begin_execution(
                s,
                p,
                approval_uuid,
                tool_name=EXECUTE_TOOL,
                arguments={"service": service, "target_version": target_version},
            )
            await s.commit()
        planned = approval.target_state
        try:
            live = await cp.status(check(service))
            if live["current_version"] != planned["from_version"] or live["state_version"] != planned["state_version"]:
                return await _finish(
                    approval,
                    p,
                    False,
                    service,
                    live["current_version"],
                    f"Aborted: target changed since approval (planned {planned['from_version']} "
                    f"@state {planned['state_version']}, now {live['current_version']} "
                    f"@state {live['state_version']}). Request a new approval.",
                )
            result = await cp.deploy(
                service,
                version=target_version,
                expected_state_version=planned["state_version"],
                idempotency_key=str(approval.execution_id),
                actor=f"opspilot:{p.email}",
                reason=f"Approved rollback {approval.id}: {approval.reason}"[:1000],
            )
            after = await cp.status(service)
            ok = after["current_version"] == target_version
            msg = (
                f"Rolled back {service} from {planned['from_version']} to {target_version}; verified running version."
                if ok
                else f"Deploy returned but running version is {after['current_version']}"
            )
            return await _finish(
                approval, p, ok, service, after["current_version"], msg, deployment=result["deployment"]
            )
        except ConflictError as exc:
            return await _finish(approval, p, False, service, None, f"Aborted by control plane: {exc.message}")
        except OpsPilotError as exc:
            await _finish(approval, p, False, service, None, f"Execution failed: {exc.message}")
            raise

    async def _finish(
        approval: Approval,
        p: Principal,
        ok: bool,
        service: str,
        current: str | None,
        message: str,
        deployment: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        result = {"message": message, "verified_current_version": current, "deployment": deployment}
        async with database.session() as s:
            await approvals.finish(s, approval, succeeded=ok, result=result, actor=p.user_id)
            await s.commit()
        return ExecutionResult(
            approval_id=str(approval.id),
            status="succeeded" if ok else "failed",
            service=service,
            from_version=approval.target_state["from_version"],
            to_version=approval.arguments["target_version"],
            verified_current_version=current,
            message=message,
        )

    return mcp


def _view(a: Approval, message: str) -> ApprovalView:
    return ApprovalView(
        approval_id=str(a.id),
        status=a.status.value,
        action=a.action,
        target_resource=a.target_resource,
        arguments=a.arguments,
        target_state=a.target_state,
        expected_impact=a.expected_impact,
        expires_at=_iso(a.expires_at),
        message=message,
    )


def _iso(d: datetime) -> str:
    return d.isoformat()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, "opspilot-mcp-operations")
    serve(build(settings), 8104)


if __name__ == "__main__":
    main()

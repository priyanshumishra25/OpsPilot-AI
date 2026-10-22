"""Operations MCP server + approval state machine, against the real demo control plane (in-process) and Postgres."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import ModuleType

import pytest
from sqlalchemy import select, update

from opspilot.approvals.service import ApprovalService
from opspilot.db.models import Approval, ApprovalStatus, AuditLog, Incident, Role
from opspilot.errors import ConflictError, PermissionDeniedError
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.mcpservers import operations
from opspilot.mcpservers.tokens import ToolTokenIssuer
from tests.conftest import Env, make_member
from tests.control_plane_fixture import TOKENS, transport
from tests.mcp_harness import PRIVATE_B64, PUBLIC_B64, client, free_port, payload, running

pytestmark = pytest.mark.integration
REPORT = {
    "incident_summary": "orders-api 5xx after v2.16.0",
    "observed_symptoms": ["30% HTTP 500"],
    "relevant_services": ["orders-api"],
    "evidence": [{"id": "E1", "kind": "metric", "source": "get_error_rate", "observation": "5xx ratio 0.3"}],
    "timeline": [{"time": "14:02", "event": "v2.16.0 deployed", "evidence": ["E1"]}],
    "candidate_causes": [{"cause": "pool exhaustion", "assessment": "likely", "supporting_evidence": ["E1"]}],
    "most_likely_cause": "pool exhaustion",
    "confidence": {"evidence_strength": "moderate", "explanation": "one source"},
    "risk_assessment": "low risk rollback",
}


class Ctx:
    def __init__(self, url: str, cp: ModuleType, env: Env, users: dict[str, object]) -> None:
        self.url, self.cp, self.env, self.users = url, cp, env, users
        self.issuer = ToolTokenIssuer(PRIVATE_B64)

    def token(self, who: str) -> str:
        return self.issuer.issue(self.users[who], "operations")  # type: ignore[arg-type]

    async def call(self, who: str, tool: str, args: dict[str, object]) -> dict[str, object]:
        async with client(self.url, self.token(who)) as c:
            return payload(await c.call_tool(tool, args))

    async def approve(self, approval_id: str, who: str = "sre", approve: bool = True) -> Approval:
        async with self.env.container.db.session() as s:
            a = await ApprovalService().decide(s, self.users[who], uuid.UUID(approval_id), approve=approve, note="ok")  # type: ignore[arg-type]
            await s.commit()
            return a

    def bad_deploy(self) -> None:
        self.cp.trigger("orders-bad-deploy")


@pytest.fixture
async def ops(env: Env, control_plane_app: ModuleType):  # type: ignore[no-untyped-def]
    db = env.container.db
    users = {
        "eng": await make_member(db, email="eng@acme.test", workspace_slug="acme", role=Role.ENGINEER),
        "sre": await make_member(db, email="sre@acme.test", workspace_slug="acme", role=Role.OPERATOR),
        "eng2": await make_member(db, email="eng2@acme.test", workspace_slug="acme", role=Role.ENGINEER),
        "other": await make_member(db, email="sre@globex.test", workspace_slug="globex", role=Role.OPERATOR),
        "viewer": await make_member(db, email="viewer@acme.test", workspace_slug="acme", role=Role.VIEWER),
    }
    port = free_port()
    settings = env.container.settings.model_copy(
        update={"mcp_verify_public_key": PUBLIC_B64, "mcp_operations_url": f"http://127.0.0.1:{port}/mcp"}
    )
    cp_client = ControlPlaneClient("http://cp.test", TOKENS["deploy"], transport=transport(control_plane_app))
    async with running(operations.build(settings, cp_client, db), port) as url:
        yield Ctx(url, control_plane_app, env, users)


async def _request(ops: Ctx, target: str = "v2.15.0") -> dict[str, object]:
    ops.bad_deploy()
    r = await ops.call(
        "eng",
        "request_deployment_rollback",
        {"service": "orders-api", "target_version": target, "reason": "5xx spike after v2.16.0 deploy"},
    )
    return r


async def test_rollback_candidates_follow_policy(ops: Ctx) -> None:
    ops.bad_deploy()
    r = await ops.call("eng", "get_available_rollback_versions", {"service": "orders-api"})
    c = {x["version"]: x for x in r["candidates"]}  # type: ignore[attr-defined]
    assert r["current_version"] == "v2.16.0"
    assert c["v2.15.0"]["eligible"] and not c["v2.15.0"]["requires_owner_approval"]
    assert not c["v2.14.0"]["eligible"] and "previously rolled back" in c["v2.14.0"]["reasons"]
    assert c["v2.13.2"]["eligible"] and c["v2.13.2"]["requires_owner_approval"]


async def test_approved_rollback_end_to_end(ops: Ctx) -> None:
    req = await _request(ops)
    assert req["status"] == "pending" and req["target_state"]["from_version"] == "v2.16.0"  # type: ignore[index]
    args = {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"}
    early = await ops.call("eng", "execute_approved_rollback", args)
    assert early["error"]["code"] == "conflict"  # type: ignore[index]
    assert ops.cp.service_status("orders-api")["current_version"] == "v2.16.0"
    await ops.approve(str(req["approval_id"]))
    done = await ops.call("eng", "execute_approved_rollback", args)
    assert done["status"] == "succeeded" and done["verified_current_version"] == "v2.15.0"
    assert ops.cp.service_status("orders-api")["current_version"] == "v2.15.0"
    history = ops.cp.deployments("orders-api", 3)
    assert history[0]["version"] == "v2.15.0" and history[0]["actor"] == "opspilot:eng@acme.test"
    assert history[1]["version"] == "v2.16.0" and history[1]["status"] == "rolled_back"
    replay = await ops.call("eng", "execute_approved_rollback", args)
    assert replay["error"]["code"] == "conflict"  # type: ignore[index]
    async with ops.env.container.db.session() as s:
        a = await s.get(Approval, uuid.UUID(str(req["approval_id"])))
        actions = list(
            await s.scalars(
                select(AuditLog.action).where(AuditLog.resource_id == str(req["approval_id"])).order_by(AuditLog.id)
            )
        )
    assert a is not None and a.status == ApprovalStatus.SUCCEEDED and a.result["deployment"]["version"] == "v2.15.0"
    assert actions == [
        "approval.requested",
        "approval.execution_refused",  # executed before approval
        "approval.approved",
        "approval.execution_started",
        "approval.execution_succeeded",
        "approval.execution_refused",  # replay after success
    ]


async def test_changed_arguments_and_forged_approvals_refused(ops: Ctx) -> None:
    req = await _request(ops)
    await ops.approve(str(req["approval_id"]))
    swapped = await ops.call(
        "eng",
        "execute_approved_rollback",
        {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.13.2"},
    )
    assert swapped["error"]["code"] == "permission_denied"
    forged = await ops.call(
        "eng",
        "execute_approved_rollback",
        {"approval_id": str(uuid.uuid4()), "service": "orders-api", "target_version": "v2.15.0"},
    )
    assert forged["error"]["code"] == "not_found"
    foreign = await ops.call(
        "other",
        "execute_approved_rollback",
        {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"},
    )
    assert foreign["error"]["code"] == "not_found"
    bystander = await ops.call(
        "eng2",
        "execute_approved_rollback",
        {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"},
    )
    assert bystander["error"]["code"] == "permission_denied"
    assert ops.cp.service_status("orders-api")["current_version"] == "v2.16.0"


async def test_target_changed_after_approval_aborts(ops: Ctx) -> None:
    req = await _request(ops)
    await ops.approve(str(req["approval_id"]))
    ops.cp.trigger("checkout-latency-regression")
    ops.cp.trigger("orders-bad-deploy")  # re-deploys v2.16.0: same version, new state_version
    r = await ops.call(
        "eng",
        "execute_approved_rollback",
        {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"},
    )
    assert r["status"] == "failed" and "target changed" in str(r["message"])
    assert ops.cp.service_status("orders-api")["current_version"] == "v2.16.0"


async def test_self_approval_expiry_and_rejection(ops: Ctx) -> None:
    req = await _request(ops)
    with pytest.raises(PermissionDeniedError):
        await ops.approve(str(req["approval_id"]), who="eng")  # requester lacks approvals:decide
    req2 = await ops.call(
        "sre",
        "request_deployment_rollback",
        {"service": "orders-api", "target_version": "v2.15.0", "reason": "operator-requested rollback"},
    )
    with pytest.raises(PermissionDeniedError, match="own request"):
        await ops.approve(str(req2["approval_id"]), who="sre")
    async with ops.env.container.db.session() as s:
        await s.execute(
            update(Approval)
            .where(Approval.id == uuid.UUID(str(req["approval_id"])))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    with pytest.raises(ConflictError, match="expired"):
        await ops.approve(str(req["approval_id"]))
    rejected = await ops.call(
        "eng",
        "request_deployment_rollback",
        {"service": "orders-api", "target_version": "v2.15.0", "reason": "another request here"},
    )
    await ops.approve(str(rejected["approval_id"]), approve=False)
    r = await ops.call(
        "eng",
        "execute_approved_rollback",
        {"approval_id": rejected["approval_id"], "service": "orders-api", "target_version": "v2.15.0"},
    )
    assert r["error"]["code"] == "conflict" and "rejected" in r["error"]["message"]  # type: ignore[index]


async def test_concurrent_executions_run_once(ops: Ctx) -> None:
    req = await _request(ops)
    await ops.approve(str(req["approval_id"]))
    args = {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"}
    results = await asyncio.gather(*(ops.call("eng", "execute_approved_rollback", args) for _ in range(5)))
    assert sum(1 for r in results if r.get("status") == "succeeded") == 1
    assert sum(1 for r in results if r.get("is_error")) == 4
    assert [d["version"] for d in ops.cp.deployments("orders-api", 2)] == ["v2.15.0", "v2.16.0"]


async def test_invalid_targets_and_scopes(ops: Ctx) -> None:
    ops.bad_deploy()
    for args in (
        {"service": "orders-api", "target_version": "v2.14.0", "reason": "previously bad version"},
        {"service": "orders-api", "target_version": "v2.16.0", "reason": "current version itself"},
        {"service": "prod-payments", "target_version": "v1.0.0", "reason": "not a demo service"},
        {"service": "orders-api", "target_version": "latest", "reason": "not a version string"},
    ):
        r = await ops.call("eng", "request_deployment_rollback", args)
        assert r["is_error"] and r["error"]["code"] == "invalid_argument", (args, r)  # type: ignore[index]
    with pytest.raises(BaseException):  # noqa: B017,PT011 - viewer token lacks operations:read: HTTP 401/403
        await ops.call("viewer", "get_deployment_status", {"service": "orders-api"})


async def test_incident_report_schema_enforced(ops: Ctx) -> None:
    ok = await ops.call(
        "eng",
        "create_incident_report",
        {"title": "orders-api 5xx", "severity": "sev2", "service": "orders-api", "report": REPORT},
    )
    async with ops.env.container.db.session() as s:
        inc = await s.get(Incident, uuid.UUID(str(ok["incident_id"])))
    assert inc is not None and inc.report["most_likely_cause"] == "pool exhaustion"
    bad = await ops.call(
        "eng",
        "create_incident_report",
        {
            "title": "orders-api 5xx",
            "severity": "sev2",
            "service": "orders-api",
            "report": {**REPORT, "confidence_score": 0.93},
        },
    )
    assert bad["is_error"] and bad["error"]["code"] == "invalid_argument"

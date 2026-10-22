"""Fault injection: dependencies failing must produce structured errors and consistent state, never hangs or
half-finished privileged actions."""

import uuid
from types import ModuleType

import httpx
import pytest
from redis.asyncio import Redis

from opspilot.approvals.service import ApprovalService
from opspilot.db.models import Approval, ApprovalStatus, Role
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.mcp_client.gateway import McpGateway
from opspilot.mcpservers import observability, operations
from opspilot.mcpservers.tokens import ToolTokenIssuer
from tests.conftest import Env, make_member
from tests.control_plane_fixture import TOKENS, transport
from tests.mcp_harness import PRIVATE_B64, PUBLIC_B64, client, free_port, payload, running

pytestmark = pytest.mark.integration


class FlakyTransport(httpx.AsyncBaseTransport):
    """Delegates to the real control plane until `down` is set, then fails like a network outage."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner, self.down = inner, False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("control plane unreachable", request=request)
        return await self.inner.handle_async_request(request)


async def test_gateway_reports_unreachable_server_as_structured_error(env: Env) -> None:
    user = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    gateway = McpGateway(
        {"observability": f"http://127.0.0.1:{free_port()}/mcp"}, ToolTokenIssuer(PRIVATE_B64), timeout=3
    )  # type: ignore[dict-item]
    outcome = await gateway.call_tool(user, "observability", "get_service_health", {"service": "orders-api"})
    assert not outcome.ok and outcome.error is not None and outcome.error["code"] == "unavailable"


async def test_readiness_reports_failed_dependency(env: Env, client: httpx.AsyncClient) -> None:
    env.container.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    r = await client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["redis"].startswith("error")
    assert r.json()["checks"]["database"] == "ok"


async def test_telemetry_outage_is_a_structured_tool_error(env: Env, control_plane_app: ModuleType) -> None:
    user = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    port = free_port()
    settings = env.container.settings.model_copy(
        update={"mcp_verify_public_key": PUBLIC_B64, "mcp_observability_url": f"http://127.0.0.1:{port}/mcp"}
    )
    dead = observability.Telemetry(f"http://127.0.0.1:{free_port()}", f"http://127.0.0.1:{free_port()}", timeout=1)
    cp = ControlPlaneClient("http://cp", TOKENS["read"], transport=transport(control_plane_app))
    token = ToolTokenIssuer(PRIVATE_B64).issue(user, "observability")
    async with running(observability.build(settings, dead, cp), port) as url, client(url, token) as c:
        r = payload(await c.call_tool("get_error_rate", {"service": "orders-api"}))
    assert r["is_error"] and r["error"]["code"] == "upstream_unavailable"


async def test_control_plane_outage_mid_execution_fails_the_approval_cleanly(
    env: Env, control_plane_app: ModuleType
) -> None:
    eng = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme", role=Role.ENGINEER)
    sre = await make_member(env.container.db, email="sre@acme.test", workspace_slug="acme", role=Role.OPERATOR)
    control_plane_app.trigger("orders-bad-deploy")
    flaky = FlakyTransport(transport(control_plane_app))
    port = free_port()
    settings = env.container.settings.model_copy(
        update={"mcp_verify_public_key": PUBLIC_B64, "mcp_operations_url": f"http://127.0.0.1:{port}/mcp"}
    )
    ops = operations.build(
        settings, ControlPlaneClient("http://cp", TOKENS["deploy"], transport=flaky), env.container.db
    )
    token = ToolTokenIssuer(PRIVATE_B64).issue(eng, "operations")
    async with running(ops, port) as url:
        async with client(url, token) as c:
            req = payload(
                await c.call_tool(
                    "request_deployment_rollback",
                    {"service": "orders-api", "target_version": "v2.15.0", "reason": "fault injection test"},
                )
            )
        async with env.container.db.session() as s:
            await ApprovalService().decide(s, sre, uuid.UUID(str(req["approval_id"])), approve=True, note=None)
            await s.commit()
        flaky.down = True
        async with client(url, token) as c:
            r = payload(
                await c.call_tool(
                    "execute_approved_rollback",
                    {"approval_id": req["approval_id"], "service": "orders-api", "target_version": "v2.15.0"},
                )
            )
    assert r["is_error"] and r["error"]["code"] == "upstream_unavailable"
    async with env.container.db.session() as s:
        a = await s.get(Approval, uuid.UUID(str(req["approval_id"])))
    assert a is not None and a.status == ApprovalStatus.FAILED and "unreachable" in a.result["message"]
    assert control_plane_app.service_status("orders-api")["current_version"] == "v2.16.0"

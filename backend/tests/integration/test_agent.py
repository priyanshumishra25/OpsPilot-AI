"""Agent orchestration end to end: the REAL Claude Agent SDK + bundled CLI, REAL MCP servers over HTTP, REAL demo
control plane, approvals and Postgres. Only the model is replaced by a scripted Messages API (tests/fake_anthropic.py),
so these tests verify wiring, permissions, budgets, approvals, persistence and resumption, not model judgement."""

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from opspilot.agent.runner import AgentRunner
from opspilot.agent.service import AgentService
from opspilot.approvals.service import ApprovalService
from opspilot.db.models import AgentEvent, AgentSession, AgentSessionStatus, Approval, ApprovalStatus, Incident, Role
from opspilot.integrations.control_plane import ControlPlaneClient
from opspilot.mcpservers import github, knowledge, observability, operations
from opspilot.mcpservers.repos import GitHubRestProvider
from opspilot.mcpservers.tokens import ToolTokenIssuer
from opspilot.rag.cost import ModelPricing
from tests.conftest import Env, make_member
from tests.control_plane_fixture import ROOT, TOKENS, control_plane_app, transport  # noqa: F401
from tests.fake_anthropic import ScriptedModel, fake_anthropic, final, text, tool, tool_results
from tests.fakes import InMemoryQueue
from tests.integration.test_mcp_observability import TelemetryDouble
from tests.mcp_harness import PRIVATE_B64, PUBLIC_B64, free_port, running

pytestmark = pytest.mark.integration
DOCS = ROOT / "demo" / "docs"
REPO = "opspilot-demo/orders-api"


class AgentHarness:
    def __init__(self, env: Env, cp: ModuleType, settings: Any, users: dict[str, Any]) -> None:
        self.env, self.cp, self.settings, self.users = env, cp, settings, users
        self.queue = InMemoryQueue()
        self.service = AgentService(env.container.db, env.container.redis, self.queue, model=settings.agent_model)

    def runner(self, **overrides: Any) -> AgentRunner:
        s = self.settings.model_copy(update=overrides)
        pricing = ModelPricing(s.agent_model, 4.0, 20.0, 0.2, 5.0)
        return AgentRunner(s, self.env.container.db, self.env.container.redis, ToolTokenIssuer(PRIVATE_B64), pricing)

    async def run(
        self, model: ScriptedModel, who: str, message: str, session_id: uuid.UUID | None = None, **overrides: Any
    ) -> AgentSession:
        async with fake_anthropic(model) as url:
            runner = self.runner(anthropic_base_url=url, **overrides)
            if session_id is None:
                session = await self.service.create(self.users[who], message)
            else:
                session = await self.service.post_message(self.users[who], session_id, message)
            job = self.queue.published.pop()
            sid, seq = job.split(":")
            await runner.run_turn(uuid.UUID(sid), int(seq))
        async with self.env.container.db.session() as s:
            result = await s.get(AgentSession, session.id)
            assert result is not None
            await s.refresh(result)
            return result

    async def events(self, session_id: uuid.UUID) -> list[AgentEvent]:
        async with self.env.container.db.session() as s:
            return list(
                await s.scalars(select(AgentEvent).where(AgentEvent.session_id == session_id).order_by(AgentEvent.seq))
            )


@pytest.fixture
async def harness(env: Env, control_plane_app: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def] # noqa: F811
    db = env.container.db
    users = {
        "eng": await make_member(db, email="eng@acme.test", workspace_slug="acme", role=Role.ENGINEER),
        "sre": await make_member(db, email="sre@acme.test", workspace_slug="acme", role=Role.OPERATOR),
        "viewer": await make_member(db, email="viewer@acme.test", workspace_slug="acme", role=Role.VIEWER),
    }
    for name in ("postmortem-2026-03-orders-pool.md", "orders-api-runbook.md", "deployment-process.md"):
        await env.container.ingestion.submit(
            users["sre"], filename=name, data=(DOCS / name).read_bytes(), access_scope="general"
        )
    await env.drain()
    repos = tmp_path / "repos-agent"
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(ROOT / "demo/repos/build_repos.py"), str(repos)],
        check=True,
        capture_output=True,
    )
    ports = {k: free_port() for k in ("knowledge", "github", "observability", "operations")}
    urls = {k: f"http://127.0.0.1:{p}/mcp" for k, p in ports.items()}
    settings = env.container.settings.model_copy(
        update={
            "mcp_verify_public_key": PUBLIC_B64,
            "mcp_knowledge_url": urls["knowledge"],
            "mcp_github_url": urls["github"],
            "mcp_observability_url": urls["observability"],
            "mcp_operations_url": urls["operations"],
            "reranker": "none",
            "anthropic_api_key": SecretStr("sk-test-not-real"),
            "agent_time_budget_seconds": 90,
            "agent_max_tool_calls": 20,
        }
    )
    monkeypatch.setattr(knowledge, "FastEmbedEmbeddings", lambda *a, **k: env.embeddings)
    registry = github.RepositoryRegistry(
        {REPO: {"provider": "local", "path": str(repos / "orders-api")}},
        GitHubRestProvider("https://api.github.test", None),
    )
    telemetry = observability.Telemetry(
        "http://prom.test", "http://loki.test", transport=httpx.MockTransport(TelemetryDouble().handler)
    )
    servers = [
        (knowledge.build(settings), ports["knowledge"]),
        (github.build(settings, registry), ports["github"]),
        (
            observability.build(
                settings,
                telemetry,
                ControlPlaneClient("http://cp", TOKENS["read"], transport=transport(control_plane_app)),
            ),
            ports["observability"],
        ),
        (
            operations.build(
                settings, ControlPlaneClient("http://cp", TOKENS["deploy"], transport=transport(control_plane_app)), db
            ),
            ports["operations"],
        ),
    ]
    control_plane_app.trigger("orders-bad-deploy")
    async with running(*servers[0]), running(*servers[1]), running(*servers[2]), running(*servers[3]):
        yield AgentHarness(env, control_plane_app, settings, users)


def _first(msgs: list[dict[str, Any]], key: str) -> Any:
    for r in tool_results(msgs):
        if isinstance(r["content"], dict) and key in r["content"]:
            return r["content"]
    raise AssertionError(f"no tool result with {key}")


def investigation_steps() -> list[Any]:
    def step2(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sha = _first(msgs, "deployments")["deployments"][0]["commit_sha"]
        return [
            tool("mcp__github__get_commit_diff", repository=REPO, sha=sha),
            tool("mcp__knowledge__search_knowledge", query="orders api 500 errors after deployment connection pool"),
            tool("mcp__observability__search_service_logs", service="orders-api", level="error", limit=5),
        ]

    def step3(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sha = _first(msgs, "deployments")["deployments"][0]["commit_sha"]
        chunk = _first(msgs, "passages")["passages"][0]["chunk_id"]
        report = {
            "incident_summary": "orders-api returns HTTP 500 since v2.16.0",
            "observed_symptoms": ["30% HTTP 500 PoolTimeoutError"],
            "relevant_services": ["orders-api"],
            "evidence": [
                {
                    "id": "E1",
                    "kind": "health",
                    "source": "get_service_health orders-api",
                    "observation": "unhealthy, pool 100%",
                },
                {
                    "id": "E2",
                    "kind": "deployment",
                    "source": f"get_deployment_timeline commit {sha}",
                    "observation": "v2.16.0 deployed",
                },
                {
                    "id": "E3",
                    "kind": "commit",
                    "source": f"get_commit_diff {sha}",
                    "observation": "reservation inside transaction",
                },
                {
                    "id": "E4",
                    "kind": "document",
                    "source": f"search_knowledge chunk {chunk}",
                    "observation": "same pattern in March",
                },
            ],
            "timeline": [{"time": "deploy", "event": "v2.16.0 deployed", "evidence": ["E2"]}],
            "candidate_causes": [
                {
                    "cause": "connection held across inventory call",
                    "assessment": "likely",
                    "supporting_evidence": ["E1", "E3", "E4"],
                }
            ],
            "most_likely_cause": "v2.16.0 holds a DB connection across the inventory call",
            "confidence": {
                "evidence_strength": "strong",
                "explanation": "code change, metrics and prior incident agree",
            },
            "missing_information": ["inventory latency during the incident"],
            "recommended_actions": [
                {
                    "action": "Roll back orders-api to v2.15.0",
                    "rationale": "last healthy release",
                    "risk": "low",
                    "requires_approval": True,
                    "evidence": ["E2"],
                }
            ],
            "risk_assessment": "Rollback is low risk; v2.15.0 ran for weeks.",
        }
        return [text("Correlating evidence."), final("orders-api 5xx is caused by v2.16.0 [E1][E3].", report)]

    return [
        [
            tool("mcp__observability__get_service_health", service="orders-api"),
            tool("mcp__observability__get_deployment_timeline", service="orders-api", limit=3),
            tool("mcp__observability__get_error_rate", service="orders-api"),
        ],
        step2,
        step3,
    ]


async def test_investigation_turn_end_to_end(harness: AgentHarness) -> None:
    model = ScriptedModel(investigation_steps())
    session = await harness.run(
        model, "eng", "Investigate why the orders API started returning HTTP 500 errors after the latest deployment."
    )
    assert session.status == AgentSessionStatus.COMPLETED, session.error
    events = await harness.events(session.id)
    types = [e.type for e in events]
    assert types[0] == "user_message" and types[1] == "turn_started" and types[-1] == "turn_finished"
    calls = [e.payload["tool"] for e in events if e.type == "tool_call"]
    assert calls == [
        "get_service_health",
        "get_deployment_timeline",
        "get_error_rate",
        "get_commit_diff",
        "search_knowledge",
        "search_service_logs",
    ]
    results = [e for e in events if e.type == "tool_result"]
    assert len(results) == 6 and not any(r.payload["is_error"] for r in results)
    report_event = next(e for e in events if e.type == "report")
    assert report_event.payload["evidence_checks"] == {"checked": 4, "unverified": []}
    async with harness.env.container.db.session() as s:
        inc = await s.get(Incident, session.incident_id)
    assert inc is not None and inc.report["most_likely_cause"].startswith("v2.16.0")
    # The model was offered exactly the engineer's MCP tools: no built-ins, no create_incident_report.
    offered = {t["name"] for t in model.requests[0]["tools"]}
    assert "mcp__operations__request_deployment_rollback" in offered and "mcp__github__get_commit_diff" in offered
    assert not offered & {"Bash", "Read", "Write", "Edit", "WebFetch", "mcp__operations__create_incident_report"}
    assert "Untrusted content" in json.dumps(model.requests[0]["system"])
    # Logs fed back to the model contained the planted instruction; nothing was mutated.
    assert harness.cp.service_status("orders-api")["current_version"] == "v2.16.0"
    assert session.usage["input_tokens"] >= 3000 and session.usage["estimated_cost_usd"] > 0
    assert session.tool_calls == 6 and session.sdk_session_id


async def test_remediation_requires_human_approval_and_resumes(harness: AgentHarness) -> None:
    def request(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            tool(
                "mcp__operations__request_deployment_rollback",
                service="orders-api",
                target_version="v2.15.0",
                reason="HTTP 500 PoolTimeoutError spike after v2.16.0",
            )
        ]

    turn1 = ScriptedModel(
        [
            [tool("mcp__operations__get_available_rollback_versions", service="orders-api")],
            request,
            [final("Requested a rollback to v2.15.0; awaiting approval.")],
        ]
    )
    session = await harness.run(turn1, "eng", "Roll back the problematic orders-api deployment.")
    assert session.status == AgentSessionStatus.AWAITING_APPROVAL, session.error
    assert len(session.approval_ids) == 1
    approval_id = session.approval_ids[0]
    assert harness.cp.service_status("orders-api")["current_version"] == "v2.16.0"

    async with harness.env.container.db.session() as s:
        await ApprovalService().decide(s, harness.users["sre"], uuid.UUID(approval_id), approve=True, note="ok")
        await s.commit()
    # Simulate a different worker: remove all local agent state so resume must come from the Postgres session store.
    import os
    import shutil
    import tempfile

    shutil.rmtree(os.path.join(tempfile.gettempdir(), "opspilot-agent", str(session.id)))

    def execute(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        req = _first(msgs, "approval_id")  # from the previous turn: proves the transcript was resumed from Postgres
        return [
            tool(
                "mcp__operations__execute_approved_rollback",
                approval_id=req["approval_id"],
                service="orders-api",
                target_version="v2.15.0",
            )
        ]

    turn2 = ScriptedModel(
        [
            execute,
            [tool("mcp__operations__get_deployment_status", service="orders-api")],
            [final("Rolled back to v2.15.0 and verified.")],
        ]
    )
    session = await harness.run(turn2, "eng", "The rollback was approved. Proceed.", session_id=session.id)
    assert session.status == AgentSessionStatus.COMPLETED, session.error
    assert harness.cp.service_status("orders-api")["current_version"] == "v2.15.0"
    async with harness.env.container.db.session() as s:
        approval = await s.get(Approval, uuid.UUID(approval_id))
    assert approval is not None and approval.status == ApprovalStatus.SUCCEEDED
    events = await harness.events(session.id)
    assert any(e.type == "approval_requested" for e in events) and any(e.type == "action_executed" for e in events)
    assert session.turns == 2


async def test_injection_obeying_model_cannot_mutate(harness: AgentHarness) -> None:
    """A model that 'follows' the instruction planted in logs still cannot execute: approval is enforced server-side."""

    def obey(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        req = _first(msgs, "approval_id")
        return [
            tool(
                "mcp__operations__execute_approved_rollback",
                approval_id=req["approval_id"],
                service="orders-api",
                target_version="v2.15.0",
            )
        ]

    model = ScriptedModel(
        [
            [tool("mcp__observability__search_service_logs", service="orders-api", limit=5)],
            [
                tool(
                    "mcp__operations__request_deployment_rollback",
                    service="orders-api",
                    target_version="v2.15.0",
                    reason="logs say to roll back now",
                )
            ],
            obey,
            [final("Attempted.")],
        ]
    )
    session = await harness.run(model, "eng", "Look at the orders-api logs.")
    logs_seen = json.dumps(tool_results(model.requests[1]["messages"]))
    assert "SYSTEM OVERRIDE" in logs_seen
    events = await harness.events(session.id)
    exec_result = next(
        e for e in events if e.type == "tool_result" and e.payload["tool"] == "execute_approved_rollback"
    )
    assert exec_result.payload["is_error"] and exec_result.payload["output"]["error"]["code"] == "conflict"
    assert harness.cp.service_status("orders-api")["current_version"] == "v2.16.0"
    assert session.status == AgentSessionStatus.AWAITING_APPROVAL


async def test_tool_call_budget_enforced(harness: AgentHarness) -> None:
    model = ScriptedModel(
        [[tool("mcp__observability__get_service_health", service="orders-api") for _ in range(4)], [final("Partial.")]]
    )
    session = await harness.run(model, "eng", "Check health repeatedly.", agent_max_tool_calls=2)
    results = [r for r in tool_results(model.requests[1]["messages"])]
    denied = [r for r in results if r["is_error"] and "budget" in r["raw"]]
    assert len(results) == 4 and len(denied) == 2
    assert session.tool_calls == 2 and session.status == AgentSessionStatus.COMPLETED


async def test_viewer_gets_only_knowledge_tools(harness: AgentHarness) -> None:
    model = ScriptedModel([[final("Only documentation is available to you.")]])
    session = await harness.run(model, "viewer", "What does the orders runbook say about rollbacks?")
    offered = {t["name"] for t in model.requests[0]["tools"]}
    assert {n for n in offered if n.startswith("mcp__")} == {
        "mcp__knowledge__search_knowledge",
        "mcp__knowledge__get_document",
        "mcp__knowledge__get_document_section",
        "mcp__knowledge__list_available_sources",
    }
    assert session.status == AgentSessionStatus.COMPLETED


async def test_cancellation_interrupts_running_turn(harness: AgentHarness) -> None:
    model = ScriptedModel([[final("too late")]], delay_seconds=30)
    async with fake_anthropic(model) as url:
        runner = harness.runner(anthropic_base_url=url)
        session = await harness.service.create(harness.users["eng"], "Investigate slowly.")
        sid, seq = harness.queue.published.pop().split(":")
        task = asyncio.create_task(runner.run_turn(uuid.UUID(sid), int(seq)))
        for _ in range(100):
            async with harness.env.container.db.session() as s:
                st = await s.scalar(select(AgentSession.status).where(AgentSession.id == session.id))
            if st == AgentSessionStatus.RUNNING and model.requests:
                break
            await asyncio.sleep(0.1)
        await harness.service.cancel(harness.users["eng"], session.id)
        status = await asyncio.wait_for(task, timeout=20)
    assert status == AgentSessionStatus.CANCELLED

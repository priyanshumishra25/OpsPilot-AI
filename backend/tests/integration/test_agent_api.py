import uuid

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from opspilot.agent.events import EventSink
from opspilot.agent.service import AgentService
from opspilot.db.models import AgentSession, AgentSessionStatus, Role
from tests.conftest import Env, login, make_member
from tests.fakes import InMemoryQueue
from tests.integration.test_api import parse_sse

pytestmark = pytest.mark.integration


@pytest.fixture
def agent_queue(env: Env) -> InMemoryQueue:
    q = InMemoryQueue()
    env.container.settings.anthropic_api_key = SecretStr("sk-test-not-real")
    env.container.agent = AgentService(env.container.db, env.container.redis, q, model="claude-opus-5-5")
    return q


async def test_session_lifecycle_and_stream(env: Env, client: httpx.AsyncClient, agent_queue: InMemoryQueue) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme", role=Role.ENGINEER)
    h = await login(client, "eng@acme.test")
    r = await client.post("/api/v1/agent/sessions", headers=h, json={"message": "Investigate orders-api 500s"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    sid = r.json()["id"]
    assert agent_queue.published == [f"{sid}:1"]
    # A second message while a turn is queued is refused.
    assert (
        await client.post(f"/api/v1/agent/sessions/{sid}/messages", headers=h, json={"message": "and checkout?"})
    ).status_code == 409

    # Simulate a finished turn (the worker's job) and stream it.
    sink = EventSink(env.container.db, Redis.from_url(env.container.settings.redis_url), uuid.UUID(sid), 1)
    await sink.emit(
        "tool_call", {"server": "observability", "tool": "get_service_health", "input": {"service": "orders-api"}}
    )
    await sink.emit("turn_finished", {"status": "completed"})
    async with env.container.db.session() as s:
        session = await s.get(AgentSession, uuid.UUID(sid))
        assert session is not None
        session.status = AgentSessionStatus.COMPLETED
        await s.commit()
    events = parse_sse((await client.get(f"/api/v1/agent/sessions/{sid}/stream", headers=h)).text)
    assert [n for n, _ in events] == ["user_message", "tool_call", "turn_finished", "idle"]
    resumed = parse_sse(
        (await client.get(f"/api/v1/agent/sessions/{sid}/stream", headers={**h, "Last-Event-ID": "2"})).text
    )
    assert [n for n, _ in resumed] == ["turn_finished", "idle"]
    detail = (await client.get(f"/api/v1/agent/sessions/{sid}", headers=h)).json()
    assert [e["type"] for e in detail["events"]] == ["user_message", "tool_call", "turn_finished"]
    follow = await client.post(f"/api/v1/agent/sessions/{sid}/messages", headers=h, json={"message": "and checkout?"})
    assert follow.status_code == 202 and agent_queue.published[-1] == f"{sid}:4"


async def test_sessions_are_private_and_cancellable(
    env: Env, client: httpx.AsyncClient, agent_queue: InMemoryQueue
) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    await make_member(env.container.db, email="eng2@acme.test", workspace_slug="acme")
    await make_member(env.container.db, email="eng@globex.test", workspace_slug="globex")
    owner = await login(client, "eng@acme.test")
    sid = (await client.post("/api/v1/agent/sessions", headers=owner, json={"message": "Investigate"})).json()["id"]
    for other in ("eng2@acme.test", "eng@globex.test"):
        h = await login(client, other)
        assert (await client.get(f"/api/v1/agent/sessions/{sid}", headers=h)).status_code == 404
        assert (await client.get(f"/api/v1/agent/sessions/{sid}/stream", headers=h)).status_code == 404
        assert (await client.post(f"/api/v1/agent/sessions/{sid}/cancel", headers=h)).status_code == 404
        assert (await client.get("/api/v1/agent/sessions", headers=h)).json() == []
    cancelled = await client.post(f"/api/v1/agent/sessions/{sid}/cancel", headers=owner)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (await client.post(f"/api/v1/agent/sessions/{sid}/cancel", headers=owner)).status_code == 409


async def test_agent_refuses_to_start_without_model_key(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    env.container.agent = AgentService(env.container.db, env.container.redis, InMemoryQueue(), model="m")
    env.container.settings.anthropic_api_key = None
    h = await login(client, "eng@acme.test")
    r = await client.post("/api/v1/agent/sessions", headers=h, json={"message": "Investigate"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["error"]["message"]

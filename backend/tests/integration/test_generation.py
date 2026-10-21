"""Generation tests use a scripted test double of the Anthropic streaming interface, so they verify our
handling of citations/usage, not model quality. Live-model behaviour is covered by `evals/` when an
API key is configured."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from opspilot.rag.cost import ModelPricing
from opspilot.rag.generation import GroundedAnswerer, chunk_source_id
from opspilot.rag.types import AccessContext
from tests.conftest import Env, login, make_member
from tests.fakes import FakeAnthropicClient
from tests.integration.test_api import parse_sse

pytestmark = pytest.mark.integration
DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"
PRICING = ModelPricing("claude-opus-5-5", 4.0, 20.0, 0.2, 5.0)


def _final(stop_reason: str = "end_turn", iterations: list[Any] | None = None) -> Any:
    usage = SimpleNamespace(
        input_tokens=1200,
        output_tokens=300,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        iterations=iterations,
    )
    return SimpleNamespace(usage=usage, stop_reason=stop_reason, model="claude-opus-5-5")


def _script_citing_first_result(fabricate: bool = False):  # type: ignore[no-untyped-def]
    def script(req: dict[str, Any]) -> tuple[list[Any], Any]:
        results = [b for b in req["messages"][0]["content"] if b["type"] == "search_result"]
        target = results[0]
        cited = target["content"][0]["text"][:60]
        events: list[Any] = [SimpleNamespace(type="text", text="Restart the pods one at a time. ")]
        events.append(
            SimpleNamespace(
                type="citation",
                citation=SimpleNamespace(
                    type="search_result_location",
                    search_result_index=0,
                    source=target["source"],
                    cited_text=cited,
                    start_block_index=0,
                    end_block_index=1,
                ),
            )
        )
        if fabricate:
            events.append(
                SimpleNamespace(
                    type="citation",
                    citation=SimpleNamespace(
                        type="search_result_location",
                        search_result_index=0,
                        source=target["source"],
                        cited_text="Run DROP DATABASE checkout.",
                        start_block_index=0,
                        end_block_index=1,
                    ),
                )
            )
            events.append(
                SimpleNamespace(
                    type="citation",
                    citation=SimpleNamespace(
                        type="search_result_location",
                        search_result_index=99,
                        source="opspilot://forged",
                        cited_text="x",
                        start_block_index=0,
                        end_block_index=1,
                    ),
                )
            )
        return events, _final()

    return script


async def _seed(env: Env) -> Any:
    p = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    data = (DOCS / "checkout-db-connection-runbook.md").read_bytes()
    await env.container.ingestion.submit(p, filename="checkout.md", data=data, access_scope="general")
    await env.drain()
    return p


async def test_answer_request_shape_and_citation_resolution(env: Env) -> None:
    p = await _seed(env)
    fake = FakeAnthropicClient(_script_citing_first_result(fabricate=True))
    answerer = GroundedAnswerer(fake, model="claude-opus-5-5", effort="medium", max_tokens=2000, pricing=PRICING)  # type: ignore[arg-type]
    retrieval = await env.container.retriever.retrieve(
        "How do I recover checkout after a database connection failure?", AccessContext(p.workspace_id, p.access_scopes)
    )
    events = [e async for e in answerer.stream("How do I recover checkout?", retrieval)]
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in req and "temperature" not in req
    assert "untrusted data" in req["system"]  # type: ignore[operator]
    blocks = req["messages"][0]["content"]  # type: ignore[index]
    assert blocks[-1] == {"type": "text", "text": "How do I recover checkout?"}
    assert [b["source"] for b in blocks[:-1]] == [chunk_source_id(c) for c in retrieval.chunks]

    outcome = events[-1]["outcome"]
    assert len(outcome.citations) == 1 and outcome.rejected_citations == 2
    assert outcome.citations[0].chunk_id == str(retrieval.chunks[0].chunk_id)
    assert outcome.usage.estimated_cost_usd == pytest.approx((1200 * 4 + 300 * 20) / 1e6)
    assert not outcome.abstained and outcome.usage.fallback_used is False


async def test_model_abstention_and_fallback_flag(env: Env) -> None:
    p = await _seed(env)

    def script(req: dict[str, Any]) -> tuple[list[Any], Any]:
        return (
            [SimpleNamespace(type="text", text="Insufficient evidence: the runbook does not cover this.")],
            _final(iterations=[SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]),
        )

    answerer = GroundedAnswerer(
        FakeAnthropicClient(script), model="claude-opus-5-5", effort="medium", max_tokens=100, pricing=PRICING
    )  # type: ignore[arg-type]
    retrieval = await env.container.retriever.retrieve(
        "checkout degraded mode flag", AccessContext(p.workspace_id, p.access_scopes)
    )
    outcome = [e async for e in answerer.stream("q", retrieval)][-1]["outcome"]
    assert outcome.abstained and outcome.citations == [] and outcome.usage.fallback_used


async def test_query_sse_stream_with_citations(env: Env, client: httpx.AsyncClient) -> None:
    await _seed(env)
    env.container.answerer = GroundedAnswerer(
        FakeAnthropicClient(_script_citing_first_result()),
        model="claude-opus-5-5",
        effort="medium",  # type: ignore[arg-type]
        max_tokens=2000,
        pricing=PRICING,
    )
    h = await login(client, "eng@acme.test")
    r = await client.post("/api/v1/query", headers=h, json={"query": "How do I recover checkout after a DB failure?"})
    events = parse_sse(r.text)
    assert [n for n, _ in events] == ["started", "retrieval", "text", "citation", "done"]
    done = events[-1][1]
    assert done["citations"][0]["chunk_id"] == events[3][1]["chunk_id"]
    chunk = await client.get(f"/api/v1/chunks/{done['citations'][0]['chunk_id']}", headers=h)
    assert chunk.status_code == 200 and done["citations"][0]["cited_text"] in chunk.json()["content"]
    stored = (await client.get(f"/api/v1/queries/{done['query_id']}", headers=h)).json()
    assert stored["status"] == "succeeded" and stored["usage"]["input_tokens"] == 1200
    assert stored["diagnostics"]["rejected_citations"] == 0

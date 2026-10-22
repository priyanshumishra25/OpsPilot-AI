"""Trace context propagates from the API's MCP gateway into MCP server spans (real HTTP)."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from opspilot.mcp_client.gateway import McpGateway
from opspilot.mcpservers import knowledge
from opspilot.mcpservers.tokens import ToolTokenIssuer
from tests.conftest import Env, make_member
from tests.mcp_harness import PRIVATE_B64, PUBLIC_B64, free_port, running

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


async def test_mcp_spans_join_the_callers_trace(
    env: Env, spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    port = free_port()
    settings = env.container.settings.model_copy(
        update={
            "mcp_verify_public_key": PUBLIC_B64,
            "mcp_knowledge_url": f"http://127.0.0.1:{port}/mcp",
            "reranker": "none",
        }
    )
    monkeypatch.setattr(knowledge, "FastEmbedEmbeddings", lambda *a, **k: env.embeddings)
    gateway = McpGateway({"knowledge": f"http://127.0.0.1:{port}/mcp"}, ToolTokenIssuer(PRIVATE_B64))  # type: ignore[dict-item]
    spans.clear()
    async with running(knowledge.build(settings), port):
        with trace.get_tracer("test").start_as_current_span("api.request") as root:
            outcome = await gateway.call_tool(user, "knowledge", "list_available_sources", {})
    assert outcome.ok
    trace_id = root.get_span_context().trace_id
    names = {s.name for s in spans.get_finished_spans() if s.context.trace_id == trace_id}
    assert {"api.request", "mcp.call_tool", "mcp.tool list_available_sources"} <= names

"""Knowledge MCP server over real Streamable HTTP, with real EdDSA tool tokens and real Postgres."""

import time
from pathlib import Path

import jwt
import pytest

from opspilot.db.models import Role
from opspilot.mcpservers import knowledge
from opspilot.mcpservers.tokens import ToolTokenIssuer
from tests.conftest import Env, make_member
from tests.mcp_harness import PRIVATE_B64, PUBLIC_B64, client, free_port, payload, running

pytestmark = pytest.mark.integration
DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"


@pytest.fixture
async def knowledge_url(env: Env, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    port = free_port()
    settings = env.container.settings.model_copy(
        update={
            "mcp_verify_public_key": PUBLIC_B64,
            "mcp_knowledge_url": f"http://127.0.0.1:{port}/mcp",
            "reranker": "none",
        }
    )
    # Swap in the deterministic test embedder (semantic quality is measured by evals/, not here).
    monkeypatch.setattr(knowledge, "FastEmbedEmbeddings", lambda *a, **k: env.embeddings)
    async with running(knowledge.build(settings), port) as url:
        yield url


async def _seed(env: Env):  # type: ignore[no-untyped-def]
    db = env.container.db
    sre = await make_member(
        db, email="sre@acme.test", workspace_slug="acme", role=Role.ADMIN, scopes=["general", "sre-restricted"]
    )
    eng = await make_member(db, email="eng@acme.test", workspace_slug="acme", scopes=["general"])
    other = await make_member(db, email="eng@globex.test", workspace_slug="globex", scopes=["general"])
    for name, scope in (
        ("checkout-db-connection-runbook.md", "general"),
        ("database-credentials-rotation.md", "sre-restricted"),
    ):
        await env.container.ingestion.submit(sre, filename=name, data=(DOCS / name).read_bytes(), access_scope=scope)
    await env.drain()
    return sre, eng, other


async def test_tools_listed_with_schemas(env: Env, knowledge_url: str) -> None:
    _, eng, _ = await _seed(env)
    token = ToolTokenIssuer(PRIVATE_B64).issue(eng, "knowledge")
    async with client(knowledge_url, token) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"search_knowledge", "get_document", "get_document_section", "list_available_sources"}
    schema = tools["search_knowledge"].input_schema
    assert schema["properties"]["query"]["maxLength"] == 500 and schema["required"] == ["query"]
    assert tools["search_knowledge"].output_schema is not None


async def test_search_respects_scopes_and_tenants(env: Env, knowledge_url: str) -> None:
    sre, eng, other = await _seed(env)
    issuer = ToolTokenIssuer(PRIVATE_B64)
    q = {"query": "break-glass database administrator account rotation", "top_k": 5}
    async with client(knowledge_url, issuer.issue(sre, "knowledge")) as c:
        sre_res = payload(await c.call_tool("search_knowledge", q))
    async with client(knowledge_url, issuer.issue(eng, "knowledge")) as c:
        eng_res = payload(await c.call_tool("search_knowledge", q))
        sources = payload(await c.call_tool("list_available_sources", {}))
    async with client(knowledge_url, issuer.issue(other, "knowledge")) as c:
        other_res = payload(await c.call_tool("search_knowledge", q))
    assert any("dba_breakglass" in p["content"] for p in sre_res["passages"])
    assert not any("breakglass" in p["content"] for p in eng_res["passages"])
    assert [s["access_scope"] for s in sources["sources"]] == ["general"]
    assert other_res["passages"] == [] and other_res["evidence_sufficient"] is False
    assert "untrusted" in sre_res["note"]


async def test_read_document_and_section_with_permission_checks(env: Env, knowledge_url: str) -> None:
    sre, eng, other = await _seed(env)
    issuer = ToolTokenIssuer(PRIVATE_B64)
    async with client(knowledge_url, issuer.issue(sre, "knowledge")) as c:
        listing = payload(await c.call_tool("list_available_sources", {}))
    ids = {s["title"]: s["document_id"] for s in listing["sources"]}
    runbook = ids["Checkout Service Runbook: Database Connection Failure"]
    restricted = ids["Database Credential Rotation (Restricted: SRE only)"]
    async with client(knowledge_url, issuer.issue(eng, "knowledge")) as c:
        doc = payload(await c.call_tool("get_document", {"document_id": runbook}))
        assert ["Checkout Service Runbook: Database Connection Failure", "Recovery procedure"] in [
            s["section_path"] for s in doc["sections"]
        ]
        sec = payload(
            await c.call_tool("get_document_section", {"document_id": runbook, "section": "Recovery procedure"})
        )
        assert "opsctl restart checkout-api --strategy rolling" in sec["content"]
        denied = payload(await c.call_tool("get_document", {"document_id": restricted}))
        assert denied["error"]["code"] == "not_found"
        missing = payload(await c.call_tool("get_document_section", {"document_id": runbook, "section": "Nope"}))
        assert missing["error"]["code"] == "not_found"
    async with client(knowledge_url, issuer.issue(other, "knowledge")) as c:
        assert payload(await c.call_tool("get_document", {"document_id": runbook}))["error"]["code"] == "not_found"


async def test_invalid_arguments_rejected(env: Env, knowledge_url: str) -> None:
    _, eng, _ = await _seed(env)
    async with client(knowledge_url, ToolTokenIssuer(PRIVATE_B64).issue(eng, "knowledge")) as c:
        for args in (
            {"query": "x"},
            {"query": "ok query", "top_k": 99},
            {"query": "a" * 501},
            {"top_k": 3},
            {"query": "ok", "source_types": ["ftp"]},
        ):
            r = await c.call_tool("search_knowledge", args)
            assert r.is_error, args


async def test_token_validation(env: Env, knowledge_url: str) -> None:
    _, eng, _ = await _seed(env)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    good = ToolTokenIssuer(PRIVATE_B64).issue(eng, "knowledge")
    wrong_audience = ToolTokenIssuer(PRIVATE_B64).issue(eng, "operations")
    claims = jwt.decode(good, options={"verify_signature": False})
    forged = jwt.encode(claims, Ed25519PrivateKey.generate(), algorithm="EdDSA")
    expired = jwt.encode({**claims, "exp": int(time.time()) - 5}, ToolTokenIssuer(PRIVATE_B64)._key, algorithm="EdDSA")
    escalated = jwt.encode(
        {**claims, "doc_scopes": ["general", "sre-restricted"]}, Ed25519PrivateKey.generate(), algorithm="EdDSA"
    )
    for bad in (None, "garbage", wrong_audience, forged, expired, escalated):
        with pytest.raises(BaseException):  # noqa: B017,PT011 - transport surfaces HTTP 401 as an exception group
            async with client(knowledge_url, bad) as c:
                await c.list_tools()


async def test_revoked_membership_takes_effect_before_token_expiry(env: Env, knowledge_url: str) -> None:
    _, eng, _ = await _seed(env)
    token = ToolTokenIssuer(PRIVATE_B64).issue(eng, "knowledge")
    async with env.container.db.session() as s:
        from sqlalchemy import delete

        from opspilot.db.models import Membership

        await s.execute(delete(Membership).where(Membership.user_id == eng.user_id))
        await s.commit()
    async with client(knowledge_url, token) as c:
        r = payload(await c.call_tool("search_knowledge", {"query": "checkout recovery"}))
    assert r["is_error"] and r["error"]["code"] == "permission_denied"

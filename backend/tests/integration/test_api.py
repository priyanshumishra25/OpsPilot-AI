import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from opspilot.db.models import Role
from tests.conftest import Env, login, make_member

pytestmark = pytest.mark.integration
DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"


async def _upload(client: httpx.AsyncClient, h: dict[str, str], name: str, scope: str = "general") -> dict[str, Any]:
    r = await client.post(
        "/api/v1/documents",
        headers=h,
        files={"file": (name, (DOCS / name).read_bytes(), "text/markdown")},
        data={"access_scope": scope},
    )
    assert r.status_code == 202, r.text
    return r.json()  # type: ignore[no-any-return]


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if name and data:
            events.append((name, json.loads("\n".join(data))))
    return events


async def test_health_and_readiness(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert r.json()["generation_configured"] is False
    assert "opspilot_http_requests_total" in (await client.get("/metrics")).text


async def test_auth_required_and_login_failures(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    assert (await client.get("/api/v1/documents")).status_code == 401
    assert (await client.get("/api/v1/documents", headers={"Authorization": "Bearer junk"})).status_code == 401
    bad = await client.post("/api/v1/auth/login", json={"email": "eng@acme.test", "password": "wrong-password"})
    unknown = await client.post("/api/v1/auth/login", json={"email": "nobody@acme.test", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"]["message"] == unknown.json()["error"]["message"]
    h = await login(client, "eng@acme.test")
    me = (await client.get("/api/v1/me", headers=h)).json()
    assert me["role"] == "engineer" and "documents:write" in me["permissions"]


async def test_login_rate_limited(env: Env, client: httpx.AsyncClient) -> None:
    env.container.settings.rate_limit_login_per_minute = 3
    codes = [
        (await client.post("/api/v1/auth/login", json={"email": "x@acme.test", "password": "nope"})).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


async def test_workspace_header_cannot_select_foreign_workspace(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    globex = await make_member(env.container.db, email="eng@globex.test", workspace_slug="globex")
    h = await login(client, "eng@acme.test")
    r = await client.get("/api/v1/documents", headers={**h, "X-Workspace-Id": str(globex.workspace_id)})
    assert r.status_code == 403


async def test_document_lifecycle_and_cross_tenant_isolation(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="admin@acme.test", workspace_slug="acme", role=Role.ADMIN)
    await make_member(env.container.db, email="eng@globex.test", workspace_slug="globex")
    acme = await login(client, "admin@acme.test")
    globex = await login(client, "eng@globex.test")

    up = await _upload(client, acme, "checkout-db-connection-runbook.md")
    await env.drain()
    doc_id = up["document_id"]
    listing = (await client.get("/api/v1/documents", headers=acme)).json()
    assert listing[0]["status"] == "indexed" and listing[0]["latest_job"]["status"] == "succeeded"
    detail = (await client.get(f"/api/v1/documents/{doc_id}", headers=acme)).json()
    assert detail["current_version"]["chunk_count"] > 0 and len(detail["versions"]) == 1
    chunks = (await client.get(f"/api/v1/documents/{doc_id}/chunks", headers=acme)).json()
    assert chunks and chunks[0]["document_title"].startswith("Checkout Service Runbook")

    # Another tenant sees nothing and gets 404 (not 403) for direct access.
    assert (await client.get("/api/v1/documents", headers=globex)).json() == []
    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=globex)).status_code == 404
    assert (await client.get(f"/api/v1/chunks/{chunks[0]['id']}", headers=globex)).status_code == 404
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=globex)).status_code in (403, 404)
    search = (
        await client.post("/api/v1/search", headers=globex, json={"query": "checkout database connection"})
    ).json()
    assert search["results"] == []

    assert (await client.post(f"/api/v1/documents/{doc_id}/reindex", headers=acme)).status_code == 202
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=acme)).status_code == 204
    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=acme)).status_code == 404
    audit = (await client.get("/api/v1/audit", headers=acme)).json()
    assert {"document.upload", "document.reindex", "document.delete"} <= {a["action"] for a in audit}


async def test_restricted_scope_hidden_from_general_users(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(
        env.container.db,
        email="sre@acme.test",
        workspace_slug="acme",
        role=Role.ADMIN,
        scopes=["general", "sre-restricted"],
    )
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    sre, eng = await login(client, "sre@acme.test"), await login(client, "eng@acme.test")
    up = await _upload(client, sre, "database-credentials-rotation.md", scope="sre-restricted")
    await env.drain()
    assert (await client.get("/api/v1/documents", headers=eng)).json() == []
    assert (await client.get(f"/api/v1/documents/{up['document_id']}", headers=eng)).status_code == 404
    sre_chunks = (await client.get(f"/api/v1/documents/{up['document_id']}/chunks", headers=sre)).json()
    assert (await client.get(f"/api/v1/chunks/{sre_chunks[0]['id']}", headers=eng)).status_code == 404
    r = await client.post(
        "/api/v1/search",
        headers=eng,
        json={"query": "breakglass account rotation", "filters": {"access_scopes": ["sre-restricted"]}},
    )
    assert r.json()["results"] == []


async def test_upload_abuse_rejected(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    await make_member(env.container.db, email="viewer@acme.test", workspace_slug="acme", role=Role.VIEWER)
    h = await login(client, "eng@acme.test")
    big = await client.post(
        "/api/v1/documents", headers=h, files={"file": ("a.md", b"x" * (300 * 1024), "text/markdown")}
    )
    assert big.status_code == 422
    spoof = await client.post(
        "/api/v1/documents", headers=h, files={"file": ("a.pdf", b"MZ\x90\x00", "application/pdf")}
    )
    assert spoof.status_code == 422
    exe = await client.post(
        "/api/v1/documents", headers=h, files={"file": ("a.exe", b"MZ", "application/octet-stream")}
    )
    assert exe.status_code == 422
    traversal = await client.post(
        "/api/v1/documents", headers=h, files={"file": ("../../etc/x.md", b"# ok", "text/markdown")}
    )
    assert traversal.status_code == 202
    assert all("/../" not in k and not k.endswith("etc/x.md") for k in env.store.objects)
    viewer = await login(client, "viewer@acme.test")
    denied = await client.post("/api/v1/documents", headers=viewer, files={"file": ("a.md", b"# a", "text/markdown")})
    assert denied.status_code == 403


async def test_query_stream_without_model_key_reports_unavailable(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    h = await login(client, "eng@acme.test")
    await _upload(client, h, "checkout-db-connection-runbook.md")
    await env.drain()
    r = await client.post(
        "/api/v1/query", headers=h, json={"query": "How do I recover checkout after a DB connection failure?"}
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[:2] == ["started", "retrieval"] and names[-1] == "error"
    assert events[1][1]["results"], "retrieval evidence is still returned"
    assert events[-1][1]["code"] == "not_configured"
    history = (await client.get("/api/v1/queries", headers=h)).json()
    assert history[0]["status"] == "generation_unavailable" and history[0]["diagnostics"]["results"]


async def test_query_abstains_deterministically_without_evidence(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    h = await login(client, "eng@acme.test")
    r = await client.post("/api/v1/query", headers=h, json={"query": "How do I recover checkout?"})
    events = parse_sse(r.text)
    done = next(d for n, d in events if n == "done")
    assert done["abstained"] is True and done["citations"] == [] and done["usage"] == {}

from pathlib import Path

import pytest

from opspilot.db.models import Role
from opspilot.rag.types import AccessContext, SearchFilters
from tests.conftest import Env, make_member

pytestmark = pytest.mark.integration
DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"


async def _seed(env: Env) -> dict[str, object]:
    db = env.container.db
    acme = await make_member(
        db, email="sre@acme.test", workspace_slug="acme", role=Role.ADMIN, scopes=["general", "sre-restricted"]
    )
    globex = await make_member(db, email="eng@globex.test", workspace_slug="globex", scopes=["general"])
    for name in ("checkout-db-connection-runbook.md", "orders-api-runbook.md"):
        await env.container.ingestion.submit(
            acme, filename=name, data=(DOCS / name).read_bytes(), access_scope="general"
        )
    await env.container.ingestion.submit(
        acme,
        filename="database-credentials-rotation.md",
        data=(DOCS / "database-credentials-rotation.md").read_bytes(),
        access_scope="sre-restricted",
    )
    # Globex has its own document that mentions the same terms.
    await env.container.ingestion.submit(
        globex,
        filename="globex-checkout.md",
        data=b"# Globex checkout\n\nGlobex checkout database connection failure: call Bob.",
        access_scope="general",
    )
    await env.drain()
    return {"acme": acme, "globex": globex}


@pytest.mark.parametrize("strategy", ["dense", "lexical", "hybrid"])
async def test_retrieval_never_crosses_tenants(env: Env, strategy: str) -> None:
    seeded = await _seed(env)
    acme, globex = seeded["acme"], seeded["globex"]
    q = "checkout database connection failure"
    acme_res = await env.container.retriever.retrieve(
        q,
        AccessContext(acme.workspace_id, acme.access_scopes),
        strategy=strategy,  # type: ignore[attr-defined]
    )
    globex_res = await env.container.retriever.retrieve(
        q,
        AccessContext(globex.workspace_id, globex.access_scopes),
        strategy=strategy,  # type: ignore[attr-defined]
    )
    assert acme_res.chunks and globex_res.chunks
    assert all("Globex" not in c.content for c in acme_res.chunks)
    assert all(c.document_title == "Globex checkout" for c in globex_res.chunks)


async def test_access_scope_filtering_and_filters_cannot_widen(env: Env) -> None:
    seeded = await _seed(env)
    acme = seeded["acme"]
    general_user = await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme", scopes=["general"])
    q = "rotate database credentials breakglass account"
    sre = await env.container.retriever.retrieve(q, AccessContext(acme.workspace_id, acme.access_scopes))  # type: ignore[attr-defined]
    assert any(c.access_scope == "sre-restricted" for c in sre.chunks)
    restricted_ctx = AccessContext(general_user.workspace_id, general_user.access_scopes)
    plain = await env.container.retriever.retrieve(q, restricted_ctx)
    assert all(c.access_scope == "general" for c in plain.chunks)
    assert all("breakglass" not in c.content for c in plain.chunks)
    # A caller-supplied scope filter can only narrow, never grant.
    widened = await env.container.retriever.retrieve(
        q, restricted_ctx, filters=SearchFilters(access_scopes=("sre-restricted",))
    )
    assert widened.chunks == []
    assert widened.evidence_sufficient is False


async def test_lexical_search_handles_hostile_query_text(env: Env) -> None:
    seeded = await _seed(env)
    acme = seeded["acme"]
    ctx = AccessContext(acme.workspace_id, acme.access_scopes)  # type: ignore[attr-defined]
    for q in ["'; DROP TABLE chunks; --", "a & b | !c <-> (d", '"unterminated', "OR OR OR"]:
        res = await env.container.retriever.retrieve(q, ctx, strategy="lexical")
        assert isinstance(res.chunks, list)


async def test_hybrid_diagnostics_and_budget(env: Env) -> None:
    seeded = await _seed(env)
    acme = seeded["acme"]
    res = await env.container.retriever.retrieve(
        "rolling restart checkout pods",
        AccessContext(acme.workspace_id, acme.access_scopes),
        final_k=3,  # type: ignore[attr-defined]
    )
    d = res.diagnostics()
    assert d["strategy"] == "hybrid" and len(d["results"]) <= 3
    assert {"embed_query", "dense_search", "lexical_search"} <= set(d["timings_ms"])
    assert any(r["dense_rank"] and r["lexical_rank"] for r in d["results"])

"""Operational commands: worker, bootstrap, user management. Run via `python -m opspilot.cli`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from opspilot.auth.passwords import ahash_password
from opspilot.config import get_settings
from opspilot.container import build_container
from opspilot.db.models import Membership, Role, User, Workspace
from opspilot.ingestion.worker import IngestionWorker
from opspilot.logging import configure_logging, get_logger
from opspilot.observability.tracing import configure_tracing

log = get_logger("opspilot.cli")


async def run_worker(concurrency: int) -> None:
    settings = get_settings()
    configure_tracing("opspilot-worker")
    c = build_container(settings)
    worker = IngestionWorker(
        c.db,
        c.store,
        c.queue,
        c.embeddings,
        c.chunker,
        max_pdf_pages=settings.max_pdf_pages,
        backoff_base=settings.ingestion_backoff_base_seconds,
        backoff_cap=settings.ingestion_backoff_max_seconds,
        stale_after_seconds=settings.ingestion_job_stale_seconds,
        concurrency=concurrency,
    )
    # Load models before taking work so the first job doesn't pay the download/initialisation cost.
    await c.embeddings.aembed_query("warmup")
    try:
        await worker.run_forever()
    finally:
        await c.close()


async def run_agent_worker() -> None:
    from redis.asyncio import Redis

    from opspilot.agent.runner import AgentRunner
    from opspilot.agent.service import AGENT_STREAM
    from opspilot.agent.worker import AgentWorker
    from opspilot.mcpservers.tokens import ToolTokenIssuer
    from opspilot.queue.redis_streams import RedisStreamQueue
    from opspilot.rag.cost import ModelPricing

    settings = get_settings()
    configure_tracing("opspilot-agent-worker")
    if settings.mcp_signing_private_key is None:
        sys.exit("OPSPILOT_MCP_SIGNING_PRIVATE_KEY is required")
    c = build_container(settings)
    pricing = ModelPricing(
        settings.agent_model,
        settings.model_input_price_per_mtok,
        settings.model_output_price_per_mtok,
        settings.model_cache_read_price_per_mtok,
        settings.model_cache_write_price_per_mtok,
    )
    runner = AgentRunner(
        settings, c.db, c.redis, ToolTokenIssuer(settings.mcp_signing_private_key.get_secret_value()), pricing
    )
    queue = RedisStreamQueue(
        Redis.from_url(settings.redis_url),
        stream=AGENT_STREAM,
        group="agent-workers",
        claim_idle_ms=(settings.agent_time_budget_seconds + 120) * 1000,
    )
    worker = AgentWorker(
        c.db,
        queue,
        runner,
        concurrency=settings.agent_worker_concurrency,
        stale_after_seconds=settings.agent_time_budget_seconds * 2,
    )
    try:
        await worker.run_forever()
    finally:
        await queue.close()
        await c.close()


async def bootstrap() -> None:
    """Create the object-store bucket (local MinIO only) and warm model caches."""
    c = build_container(get_settings())
    try:
        if c.settings.s3_endpoint_url:
            await c.store.ensure_bucket()
        dim = len(await c.embeddings.aembed_query("warmup"))
        if c.reranker is not None:
            await c.reranker.score("warmup", ["warmup"])
        log.info("bootstrap_complete", embedding_dim=dim)
    finally:
        await c.close()


async def create_user(email: str, name: str, password: str, workspace: str, role: str, scopes: list[str]) -> None:
    if len(password) < 12:
        sys.exit("Password must be at least 12 characters")
    c = build_container(get_settings())
    try:
        async with c.db.session() as s:
            ws = await s.scalar(select(Workspace).where(Workspace.slug == workspace))
            if ws is None:
                ws = Workspace(slug=workspace, name=workspace.replace("-", " ").title(), settings={})
                s.add(ws)
                await s.flush()
            user = await s.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                user = User(email=email.lower(), display_name=name, password_hash=await ahash_password(password))
                s.add(user)
                await s.flush()
            else:
                user.password_hash = await ahash_password(password)
            m = await s.scalar(
                select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
            )
            if m is None:
                s.add(Membership(user_id=user.id, workspace_id=ws.id, role=Role(role), access_scopes=scopes))
            else:
                m.role, m.access_scopes = Role(role), scopes
            await s.commit()
        log.info("user_upserted", email=email, workspace=workspace, role=role, scopes=scopes)
    finally:
        await c.close()


DEMO_USERS = [
    ("admin@opspilot.demo", "Demo Admin", Role.ADMIN, ["general", "sre-restricted"]),
    ("sre@opspilot.demo", "Demo SRE", Role.OPERATOR, ["general", "sre-restricted"]),
    ("engineer@opspilot.demo", "Demo Engineer", Role.ENGINEER, ["general"]),
    ("viewer@opspilot.demo", "Demo Viewer", Role.VIEWER, ["general"]),
]
RESTRICTED_DEMO_DOCS = {"database-credentials-rotation.md": "sre-restricted"}


async def seed_demo(password: str, docs_dir: str) -> None:
    """Create the labelled demo workspace and users, and submit the demo corpus for ingestion."""
    from pathlib import Path

    from opspilot.auth.service import load_principal

    for email, name, role, scopes in DEMO_USERS:
        await create_user(email, name, password, "demo", role.value, scopes)
    c = build_container(get_settings())
    try:
        async with c.db.session() as s:
            ws = await s.scalar(select(Workspace).where(Workspace.slug == "demo"))
            assert ws is not None
            ws.name = "Demo Workspace"
            ws.settings = {**(ws.settings or {}), "demo": True}
            admin_id = await s.scalar(select(User.id).where(User.email == DEMO_USERS[0][0]))
            assert admin_id is not None
            await s.commit()
            principal = await load_principal(s, admin_id, ws.id)
        paths = await asyncio.to_thread(lambda: sorted(Path(docs_dir).glob("*.md")))
        for path in paths:
            if path.name == "README.md":
                continue
            result = await c.ingestion.submit(
                principal,
                filename=path.name,
                data=await asyncio.to_thread(path.read_bytes),
                access_scope=RESTRICTED_DEMO_DOCS.get(path.name, "general"),
                is_demo_seed=True,
            )
            log.info("demo_document_submitted", file=path.name, deduplicated=result.deduplicated)
    finally:
        await c.close()


async def mint_tool_token(email: str, workspace: str, server: str, ttl: int) -> None:
    """For connecting independent MCP clients (e.g. MCP Inspector). Requires the private signing key, so only an
    operator with access to the API's secrets can run it. Permissions come from the user's real membership."""
    from opspilot.auth.service import load_principal
    from opspilot.mcpservers.tokens import ToolTokenIssuer

    settings = get_settings()
    if settings.mcp_signing_private_key is None:
        sys.exit("OPSPILOT_MCP_SIGNING_PRIVATE_KEY is not configured")
    c = build_container(settings)
    try:
        async with c.db.session() as s:
            user_id = await s.scalar(select(User.id).where(User.email == email.lower()))
            ws_id = await s.scalar(select(Workspace.id).where(Workspace.slug == workspace))
            if user_id is None or ws_id is None:
                sys.exit("Unknown user or workspace")
            principal = await load_principal(s, user_id, ws_id)
        issuer = ToolTokenIssuer(settings.mcp_signing_private_key.get_secret_value(), ttl_seconds=min(ttl, 3600))
        print(issuer.issue(principal, server))
    finally:
        await c.close()


def main() -> None:
    configure_logging(get_settings().log_level, "opspilot-cli")
    p = argparse.ArgumentParser(prog="opspilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker", help="Run the ingestion worker")
    w.add_argument("--concurrency", type=int, default=int(os.environ.get("OPSPILOT_WORKER_CONCURRENCY", "2")))
    sub.add_parser("bootstrap", help="Create local bucket and warm model caches")
    sub.add_parser("agent-worker", help="Run the agent worker (Claude Agent SDK turns)")
    u = sub.add_parser("create-user", help="Create or update a user and workspace membership")
    u.add_argument("--email", required=True)
    u.add_argument("--name", required=True)
    u.add_argument("--password", default=os.environ.get("OPSPILOT_NEW_USER_PASSWORD"))
    u.add_argument("--workspace", required=True)
    u.add_argument("--role", choices=[r.value for r in Role], default="engineer")
    u.add_argument("--scopes", default="general", help="Comma-separated readable access scopes")
    d = sub.add_parser("seed-demo", help="Create the demo workspace/users and ingest the demo corpus")
    d.add_argument("--password", default=os.environ.get("OPSPILOT_DEMO_PASSWORD"))
    d.add_argument("--docs", default=os.environ.get("OPSPILOT_DEMO_DOCS", "/demo/docs"))
    sub.add_parser("gen-mcp-keys", help="Print a new Ed25519 key pair for MCP tool tokens")
    m = sub.add_parser("mint-tool-token", help="Mint a short-lived MCP tool token for a user (operator tool)")
    m.add_argument("--email", required=True)
    m.add_argument("--workspace", required=True, help="workspace slug")
    m.add_argument("--server", required=True, choices=["knowledge", "github", "observability", "operations"])
    m.add_argument("--ttl", type=int, default=300)
    args = p.parse_args()
    if args.cmd == "worker":
        asyncio.run(run_worker(args.concurrency))
    elif args.cmd == "agent-worker":
        asyncio.run(run_agent_worker())
    elif args.cmd == "bootstrap":
        asyncio.run(bootstrap())
    elif args.cmd == "create-user":
        if not args.password:
            sys.exit("Provide --password or OPSPILOT_NEW_USER_PASSWORD")
        asyncio.run(
            create_user(args.email, args.name, args.password, args.workspace, args.role, args.scopes.split(","))
        )
    elif args.cmd == "seed-demo":
        if not args.password:
            sys.exit("Provide --password or OPSPILOT_DEMO_PASSWORD")
        asyncio.run(seed_demo(args.password, args.docs))
    elif args.cmd == "mint-tool-token":
        asyncio.run(mint_tool_token(args.email, args.workspace, args.server, args.ttl))
    elif args.cmd == "gen-mcp-keys":
        import base64

        from cryptography.hazmat.primitives import serialization as ser
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
        pub = key.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
        print(f"OPSPILOT_MCP_SIGNING_PRIVATE_KEY={base64.b64encode(raw).decode()}")
        print(f"OPSPILOT_MCP_VERIFY_PUBLIC_KEY={base64.b64encode(pub).decode()}")
    else:  # pragma: no cover - argparse enforces the choices
        p.error(f"unknown command {args.cmd}")


if __name__ == "__main__":
    main()

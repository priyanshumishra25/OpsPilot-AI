"""Operational commands: worker, bootstrap, user management. Run via `python -m opspilot.cli`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from opspilot.auth.passwords import hash_password
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
                user = User(email=email.lower(), display_name=name, password_hash=hash_password(password))
                s.add(user)
                await s.flush()
            else:
                user.password_hash = hash_password(password)
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


def main() -> None:
    configure_logging(get_settings().log_level, "opspilot-cli")
    p = argparse.ArgumentParser(prog="opspilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker", help="Run the ingestion worker")
    w.add_argument("--concurrency", type=int, default=int(os.environ.get("OPSPILOT_WORKER_CONCURRENCY", "2")))
    sub.add_parser("bootstrap", help="Create local bucket and warm model caches")
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
    args = p.parse_args()
    if args.cmd == "worker":
        asyncio.run(run_worker(args.concurrency))
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
    else:  # pragma: no cover - argparse enforces the choices
        p.error(f"unknown command {args.cmd}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from opspilot.api.deps import ContainerDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(container: ContainerDep, response: Response) -> dict[str, Any]:
    async def db() -> None:
        async with container.db.session() as s:
            await s.execute(text("SELECT 1"))

    checks = {
        "database": db(),
        "redis": container.redis.ping(),
        "object_store": container.store.ping(),
        "queue": container.queue.ping(),
    }
    results: dict[str, Any] = {}
    for name, coro in checks.items():
        try:
            await asyncio.wait_for(coro, timeout=3)
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - reported in the readiness payload
            results[name] = f"error: {exc.__class__.__name__}"
    ready = all(v == "ok" for v in results.values())
    response.status_code = 200 if ready else 503
    return {
        "status": "ready" if ready else "not_ready",
        "checks": results,
        "generation_configured": container.answerer.configured,
    }


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

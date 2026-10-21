"""Demo service (orders-api or checkout-api). Behaviour comes from the control plane: the deployed version
determines what the code does (e.g. orders-api v2.16.0 holds DB connections across the inventory call), and
scenario faults inject dependency failures. Exposes Prometheus metrics and writes JSON logs to stdout and Loki.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

SERVICE = os.environ["SERVICE_NAME"]
CONTROL_PLANE = os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8090")
LOKI = os.environ.get("LOKI_PUSH_URL", "")
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20" if SERVICE == "orders-api" else "40"))
POOL_TIMEOUT_MS = 3000
RNG = random.Random(int(os.environ.get("DEMO_SEED", "7")))

REQUESTS = Counter("http_requests_total", "Requests", ["service", "route", "status", "error_type"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Latency",
    ["service", "route"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.5, 5.0),
)
POOL_IN_USE = Gauge("db_pool_in_use", "Connections in use", ["service"])
POOL_MAX_G = Gauge("db_pool_max", "Pool size", ["service"])
INFO = Gauge("service_build_info", "Running version", ["service", "version"])

behaviour: dict[str, Any] = {"version": "unknown"}
in_use = 0
log_buffer: list[tuple[str, str]] = []


def log(level: str, message: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "level": level,
        "service": SERVICE,
        "version": behaviour.get("version"),
        "message": message,
        **fields,
    }
    line = json.dumps(record)
    print(line, file=sys.stdout, flush=True)
    log_buffer.append((str(time.time_ns()), line))


async def ship_logs(client: httpx.AsyncClient) -> None:
    while True:
        await asyncio.sleep(2)
        if not LOKI or not log_buffer:
            continue
        batch = log_buffer[:500]
        del log_buffer[:500]
        by_level: dict[str, list[list[str]]] = {}
        for ts, line in batch:
            by_level.setdefault(json.loads(line)["level"], []).append([ts, line])
        streams = [
            {"stream": {"service": SERVICE, "level": lvl, "environment": "demo"}, "values": v}
            for lvl, v in by_level.items()
        ]
        try:
            await client.post(LOKI, json={"streams": streams}, timeout=5)
        except httpx.HTTPError:
            pass  # telemetry loss is acceptable in the demo; stdout still has the logs


async def poll_behaviour(client: httpx.AsyncClient) -> None:
    global behaviour
    last_version = None
    while True:
        try:
            r = await client.get(f"{CONTROL_PLANE}/api/services/{SERVICE}/behavior", timeout=2)
            behaviour = r.json()
            if behaviour["version"] != last_version:
                INFO.clear()
                INFO.labels(SERVICE, behaviour["version"]).set(1)
                log("info", "service version active", event="version_change", previous_version=last_version)
                last_version = behaviour["version"]
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    POOL_MAX_G.labels(SERVICE).set(POOL_MAX)
    async with httpx.AsyncClient() as client:
        tasks = [asyncio.create_task(poll_behaviour(client)), asyncio.create_task(ship_logs(client))]
        yield
        for t in tasks:
            t.cancel()


app = FastAPI(lifespan=lifespan)


async def with_connection(hold_ms: float) -> bool:
    """Simulated pool: wait up to POOL_TIMEOUT_MS for a free connection, then hold it for hold_ms."""
    global in_use
    waited = 0.0
    while in_use >= POOL_MAX:
        if waited >= POOL_TIMEOUT_MS:
            return False
        await asyncio.sleep(0.05)
        waited += 50
    in_use += 1
    POOL_IN_USE.labels(SERVICE).set(in_use)
    try:
        await asyncio.sleep(hold_ms / 1000)
    finally:
        in_use -= 1
        POOL_IN_USE.labels(SERVICE).set(in_use)
    return True


def respond(route: str, start: float, status: int, error_type: str = "", **body: Any) -> JSONResponse:
    REQUESTS.labels(SERVICE, route, str(status), error_type).inc()
    LATENCY.labels(SERVICE, route).observe(time.perf_counter() - start)
    return JSONResponse(body, status_code=status)


@app.post("/api/orders")
async def create_order(payload: dict[str, Any] | None = None) -> JSONResponse:
    start, trace_id = time.perf_counter(), secrets.token_hex(16)
    b = behaviour
    if RNG.random() < float(b.get("inventory_timeout_rate", 0)):
        await asyncio.sleep(2.0)
        log(
            "error",
            "inventory reservation timed out after 2000 ms",
            error_type="InventoryTimeout",
            trace_id=trace_id,
            route="/api/orders",
            status=504,
        )
        return respond("/api/orders", start, 504, "InventoryTimeout", error="INVENTORY_TIMEOUT")
    if b.get("holds_connection_across_inventory"):
        # Regressed code path: the connection is held while the (batched, slower) inventory call runs.
        ok = await with_connection(RNG.uniform(20, 60) + RNG.uniform(1500, 3000))
    else:
        ok = await with_connection(RNG.uniform(20, 60))
        if ok:
            await asyncio.sleep(RNG.uniform(0.2, 0.4))  # inventory call happens outside the transaction
    if not ok:
        log(
            "error",
            f"timed out waiting {POOL_TIMEOUT_MS} ms for a database connection (pool {in_use}/{POOL_MAX} in use)",
            error_type="PoolTimeoutError",
            trace_id=trace_id,
            route="/api/orders",
            status=500,
            code_path="orders/service.py:create_order",
        )
        return respond("/api/orders", start, 500, "PoolTimeoutError", error="INTERNAL_ERROR")
    notes = (payload or {}).get("notes")
    if notes:
        log("info", "order created with customer note", trace_id=trace_id, customer_note=str(notes)[:300])
    return respond("/api/orders", start, 201, order_id=secrets.token_hex(6))


@app.post("/api/checkout")
async def checkout(payload: dict[str, Any] | None = None) -> JSONResponse:
    start, trace_id = time.perf_counter(), secrets.token_hex(16)
    b = behaviour
    if b.get("db_pool_exhausted"):
        POOL_IN_USE.labels(SERVICE).set(POOL_MAX)
        if RNG.random() < 0.45:
            await asyncio.sleep(0.05)
            log(
                "error",
                "DatabaseConnectionError: could not acquire connection to checkout-db",
                trace_id=trace_id,
                error_type="DatabaseConnectionError",
                route="/api/checkout",
                status=503,
            )
            return respond("/api/checkout", start, 503, "DatabaseConnectionError", error="CHECKOUT_DB_UNAVAILABLE")
    extra = float(b.get("extra_latency_ms", 0))
    if extra:
        await asyncio.sleep(RNG.uniform(0.6, 1.2) * extra / 1000)
        if RNG.random() < 0.1:
            log("warning", "fraud scoring call slow", trace_id=trace_id, duration_ms=round(extra), dependency="fraud")
    ok = await with_connection(RNG.uniform(30, 90))
    if not ok:
        return respond("/api/checkout", start, 503, "DatabaseConnectionError", error="CHECKOUT_DB_UNAVAILABLE")
    await asyncio.sleep(RNG.uniform(0.05, 0.15))  # payment provider
    return respond("/api/checkout", start, 200, charge_id=secrets.token_hex(6))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE, "version": str(behaviour.get("version"))}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

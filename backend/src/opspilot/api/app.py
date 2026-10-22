from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from opspilot.api.errors import install_error_handlers
from opspilot.api.routes import agent, auth, documents, health, operations, query
from opspilot.config import Settings, get_settings
from opspilot.container import Container, build_container
from opspilot.logging import configure_logging, get_logger
from opspilot.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from opspilot.observability.tracing import configure_tracing

log = get_logger(__name__)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.service_name)
    if container is None:
        configure_tracing(settings.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings)
        log.info("api_started", environment=settings.environment)
        yield
        log.info("api_stopping")
        if container is None:
            await app.state.container.close()

    app = FastAPI(
        title="OpsPilot AI API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Workspace-Id", "X-Request-Id"],
        expose_headers=["X-Request-Id", "X-Trace-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request_id = request_id[:64]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route_path).observe(elapsed)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if route_path not in ("/healthz", "/metrics"):
            log.info(
                "http_request",
                method=request.method,
                route=route_path,
                status=response.status_code,
                duration_ms=round(elapsed * 1000, 1),
                request_id=request_id,
            )
        return response

    install_error_handlers(app)
    for r in (health.router, auth.router, documents.router, query.router, operations.router, agent.router):
        app.include_router(r)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,metrics")
    return app

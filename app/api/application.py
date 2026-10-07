import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.core.config import Settings
from app.core.errors import Conflict, InvalidToolInput, NotFound, ProviderUnavailable
from app.models.schemas import (
    ContextPackage,
    ContextRequest,
    Event,
    EventCreate,
    EventKind,
    MemoryCreate,
    ModelResult,
    RouteRequest,
    Session,
    SessionCreate,
    ToolRequest,
    ToolResult,
    new_id,
)
from app.observability.metrics import Metrics
from app.observability.tracing import request_id_context
from app.providers.embeddings import HashEmbeddingProvider
from app.providers.models import MockModelProvider, ModelProvider, OpenAICompatibleProvider
from app.repositories.cache import Cache, LocalCache, RedisCache
from app.repositories.sql import SQLRepository
from app.services.context import ContextService
from app.services.router import ModelRouter
from app.services.sessions import SessionService
from app.services.tools import ToolService

logger = logging.getLogger("broker")


class Container:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.metrics = Metrics()
        self.repository = SQLRepository(settings.database_url)
        self.embeddings = HashEmbeddingProvider()
        self.cache: Cache = (
            RedisCache(settings.redis_url, self.metrics) if settings.redis_url else LocalCache()
        )
        self.sessions = SessionService(self.repository, self.embeddings)
        self.context = ContextService(
            self.repository,
            self.embeddings,
            self.cache,
            self.metrics,
            settings.cache_ttl,
            settings.candidate_limit,
        )
        self.tools = ToolService(self.repository, self.sessions, self.metrics)
        self.providers: list[ModelProvider] = []
        if settings.openai_api_key:
            self.providers.append(
                OpenAICompatibleProvider(
                    settings.openai_base_url,
                    settings.openai_api_key,
                    settings.openai_model,
                    settings.input_cost_per_million or None,
                    settings.output_cost_per_million or None,
                )
            )
        self.providers.append(MockModelProvider())
        self.router = ModelRouter(self.providers, self.context, self.sessions, self.metrics)

    async def close(self) -> None:
        for provider in self.providers:
            if isinstance(provider, OpenAICompatibleProvider):
                await provider.close()
        await self.cache.close()
        await self.repository.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    container = Container(settings or Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await container.repository.initialize()
        yield
        await container.close()

    app = FastAPI(
        title="Agent Context Broker",
        version="0.1.0",
        lifespan=lifespan,
        description="Persistent context, memory, tool execution and model routing.",
    )
    app.state.container = container

    @app.middleware("http")
    async def instrument(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")
        if (
            not request_id
            or len(request_id) > 128
            or not request_id.isascii()
            or not all(c.isalnum() or c in "-_." for c in request_id)
        ):
            request_id = new_id()
        start = time.perf_counter()
        if container.settings.api_key and request.url.path not in ("/health",):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(
                supplied.encode(), f"Bearer {container.settings.api_key}".encode()
            ):
                return JSONResponse(
                    {"error": {"code": "unauthorized"}},
                    status_code=401,
                    headers={"x-request-id": request_id},
                )
        # Reject oversized declared requests; reverse proxy must cap streamed/chunked bodies.
        declared = request.headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > 262144):
            return JSONResponse({"error": {"code": "payload_too_large"}}, status_code=413)
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_context.reset(token)
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        container.metrics.requests.labels(route_name, str(response.status_code)).inc()
        container.metrics.latency.labels(route_name).observe(elapsed)
        response.headers["x-request-id"] = request_id
        logger.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "trace_id": request_id,
                    "session_id": request.path_params.get("session_id")
                    or getattr(request.state, "session_id", None),
                    "agent_id": getattr(request.state, "agent_id", None),
                    "route": route_name,
                    "status": response.status_code,
                    "latency_ms": round(elapsed * 1000, 3),
                }
            )
        )
        return response

    async def domain_error(request: Request, error: Exception):
        status = {NotFound: 404, Conflict: 409, InvalidToolInput: 422, ProviderUnavailable: 503}
        return JSONResponse(
            {
                "error": {"code": type(error).__name__, "message": str(error)},
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=status[type(error)],
        )

    for error in (NotFound, Conflict, InvalidToolInput, ProviderUnavailable):
        app.add_exception_handler(error, domain_error)

    Key = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200, min_length=1)]

    @app.post("/sessions", response_model=Session, status_code=201)
    async def create_session(data: SessionCreate, request: Request):
        request.state.agent_id = data.agent_id
        return await container.repository.create_session(data)

    @app.get("/sessions/{session_id}", response_model=Session)
    async def get_session(session_id: str, request: Request):
        session = await container.repository.get_session(session_id)
        request.state.agent_id = session.agent_id
        return session

    @app.post("/sessions/{session_id}/events", response_model=Event, status_code=201)
    async def append_event(session_id: str, data: EventCreate, idempotency_key: Key = None):
        return await container.sessions.append(session_id, data, idempotency_key)

    @app.get("/sessions/{session_id}/events", response_model=list[Event])
    async def list_events(
        session_id: str, limit: int = Query(100, ge=1, le=2000), offset: int = Query(0, ge=0)
    ):
        return await container.repository.list_events(session_id, limit=limit, offset=offset)

    @app.post("/sessions/{session_id}/memory", response_model=Event, status_code=201)
    async def remember(session_id: str, data: MemoryCreate, idempotency_key: Key = None):
        return await container.sessions.remember(session_id, data, idempotency_key)

    @app.get("/sessions/{session_id}/memory", response_model=list[Event])
    async def memories(
        session_id: str, limit: int = Query(100, ge=1, le=2000), offset: int = Query(0, ge=0)
    ):
        return await container.repository.list_events(session_id, limit, EventKind.memory, offset)

    @app.post("/context/build", response_model=ContextPackage)
    async def context(data: ContextRequest, request: Request):
        request.state.session_id = data.session_id
        request.state.agent_id = (await container.repository.get_session(data.session_id)).agent_id
        return await container.context.build(data)

    @app.get("/tools")
    async def list_tools():
        return {"tools": container.tools.list_tools()}

    @app.post("/tools/{tool_name}/execute", response_model=ToolResult)
    async def execute(tool_name: str, data: ToolRequest, request: Request):
        request.state.session_id = data.session_id
        request.state.agent_id = (await container.repository.get_session(data.session_id)).agent_id
        return await container.tools.execute(tool_name, data)

    @app.post("/models/route", response_model=ModelResult)
    async def route(data: RouteRequest, request: Request):
        request.state.session_id = data.session_id
        request.state.agent_id = (await container.repository.get_session(data.session_id)).agent_id
        return await container.router.route(data)

    @app.get("/health")
    async def health():
        # Actual durable-storage round trip. Redis is a degradable dependency.
        from sqlalchemy import text

        async with container.repository.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "storage": container.repository.engine.dialect.name,
            "cache": "redis" if container.settings.redis_url else "local",
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(container.metrics.render(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    return app

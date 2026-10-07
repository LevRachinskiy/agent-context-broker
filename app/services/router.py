import asyncio
import time

from app.core.errors import ProviderUnavailable
from app.models.schemas import EventCreate, EventKind, ModelResult, RouteRequest
from app.observability.metrics import Metrics
from app.observability.tracing import request_id_context
from app.providers.models import ModelProvider
from app.services.context import ContextService, estimate_tokens
from app.services.sessions import SessionService


class ModelRouter:
    def __init__(
        self,
        providers: list[ModelProvider],
        context: ContextService,
        sessions: SessionService,
        metrics: Metrics,
    ):
        self.providers, self.context, self.sessions, self.metrics = (
            providers,
            context,
            sessions,
            metrics,
        )

    async def route(self, request: RouteRequest) -> ModelResult:
        package = await self.context.build(request)
        providers = [
            p
            for p in self.providers
            if request.task_type in p.task_types
            and (request.allow_mock_fallback or p.name != "mock" or len(self.providers) == 1)
        ]
        if request.policy == "latency":
            providers.sort(key=lambda p: p.expected_latency_ms)
        elif request.policy == "cost":
            providers.sort(key=lambda p: p.input_cost if p.input_cost is not None else float("inf"))
        if request.preferred_model:
            providers.sort(key=lambda p: p.model != request.preferred_model)
        start = time.perf_counter()
        attempts: list[str] = []
        for provider in providers:
            for attempt in range(2):
                attempts.append(provider.name)
                try:
                    completion = await asyncio.wait_for(
                        provider.complete(request.query, package.text), timeout=20
                    )
                except (ProviderUnavailable, TimeoutError):
                    self.metrics.models.labels(provider.name, "failure").inc()
                    if attempt == 0:
                        await asyncio.sleep(0.025)
                    continue
                self.metrics.models.labels(provider.name, "success").inc()
                input_tokens = estimate_tokens(request.query + package.text)
                output_tokens = estimate_tokens(completion.content)
                cost = None
                if provider.input_cost is not None and provider.output_cost is not None:
                    cost = (
                        input_tokens * provider.input_cost + output_tokens * provider.output_cost
                    ) / 1e6
                result = ModelResult(
                    provider=provider.name,
                    model=provider.model,
                    content=completion.content,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    estimated_input_tokens=input_tokens,
                    estimated_output_tokens=output_tokens,
                    estimated_cost=cost,
                    attempts=attempts,
                    context=package,
                )
                await self.sessions.append(
                    request.session_id,
                    EventCreate(
                        kind=EventKind.model_response,
                        content=completion.content[:16000],
                        metadata={
                            "request_id": request_id_context.get(),
                            "provider": provider.name,
                            "model": provider.model,
                            "latency_ms": result.latency_ms,
                            "estimated_cost": cost,
                            "attempts": attempts,
                        },
                    ),
                )
                return result
        await self.sessions.append(
            request.session_id,
            EventCreate(
                kind=EventKind.error,
                content="All eligible model providers failed",
                metadata={"attempts": attempts},
            ),
        )
        raise ProviderUnavailable("All eligible model providers failed")

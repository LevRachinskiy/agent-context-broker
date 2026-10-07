import httpx
import pytest

from app.core.errors import ProviderUnavailable
from app.models.schemas import RouteRequest
from app.providers.models import MockModelProvider, OpenAICompatibleProvider
from app.repositories.cache import LocalCache, RedisCache


async def test_model_fallback_and_disabled_mock(client, session_id):
    class Broken(MockModelProvider):
        name = "broken"
        model = "broken-model"

        async def complete(self, query, context):
            raise ProviderUnavailable("injected failure")

    router = client.broker.router
    router.providers = [Broken(), MockModelProvider()]
    result = await router.route(
        RouteRequest(session_id=session_id, query="redis", allow_mock_fallback=True)
    )
    assert result.provider == "mock" and result.attempts == ["broken", "broken", "mock"]
    assert result.estimated_cost == 0.0
    with pytest.raises(ProviderUnavailable):
        await router.route(RouteRequest(session_id=session_id, query="redis"))


async def test_router_policy_and_preference(client, session_id):
    class Fast(MockModelProvider):
        name = "fast"
        model = "fast-model"
        expected_latency_ms = 0.1
        input_cost = 2.0
        output_cost = 2.0

    class Cheap(MockModelProvider):
        name = "cheap"
        model = "cheap-model"
        expected_latency_ms = 10.0
        input_cost = 0.1
        output_cost = 0.1

    router = client.broker.router
    router.providers = [Fast(), Cheap()]
    for policy, expected in [("cost", "cheap"), ("latency", "fast")]:
        result = await router.route(
            RouteRequest(session_id=session_id, query="redis", policy=policy)
        )
        assert result.provider == expected and result.estimated_cost > 0
    result = await router.route(
        RouteRequest(
            session_id=session_id, query="redis", policy="latency", preferred_model="cheap-model"
        )
    )
    assert result.provider == "cheap"


async def test_openai_compatible_protocol_and_error_sanitization():
    provider = OpenAICompatibleProvider("https://example.invalid/v1", "secret", "example-model")

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await provider.client.aclose()
    provider.client = httpx.AsyncClient(
        base_url="https://example.invalid/v1/",
        headers={"Authorization": "Bearer secret"},
        transport=httpx.MockTransport(handler),
    )
    assert (await provider.complete("question", "evidence")).content == "ok"
    await provider.close()
    provider.client = httpx.AsyncClient(
        base_url="https://example.invalid/v1/",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="sensitive")),
    )
    with pytest.raises(ProviderUnavailable, match="request failed"):
        await provider.complete("question", "evidence")
    await provider.close()


async def test_redis_failure_is_cache_miss(client, session_id):
    cache = RedisCache("redis://127.0.0.1:1/0", client.broker.metrics)
    assert await cache.get("key") is None
    await cache.set("key", "value", 1)
    client.broker.context.cache = cache
    response = await client.post(
        "/context/build", json={"session_id": session_id, "query": "redis"}
    )
    assert response.status_code == 200
    assert "broker_cache_errors_total" in client.broker.metrics.render().decode()
    await cache.close()


async def test_local_cache_capacity_and_expiry():
    cache = LocalCache(capacity=1)
    await cache.set("old", "one", 100)
    await cache.set("new", "two", 0)
    assert await cache.get("old") is None and await cache.get("new") is None
    await cache.close()

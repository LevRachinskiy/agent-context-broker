import asyncio

import httpx

from app.api.application import create_app
from app.core.config import Settings


async def test_session_event_memory_roundtrip(client, session_id):
    response = await client.post(
        f"/sessions/{session_id}/events",
        json={"kind": "user", "content": "redis cache invalidation"},
        headers={"Idempotency-Key": "one"},
    )
    assert response.status_code == 201
    assert "embedding" not in response.json()
    retry = await client.post(
        f"/sessions/{session_id}/events",
        json={"kind": "user", "content": "redis cache invalidation"},
        headers={"Idempotency-Key": "one"},
    )
    assert retry.json()["event_id"] == response.json()["event_id"]
    assert (await client.get(f"/sessions/{session_id}")).json()["revision"] == 1
    conflict = await client.post(
        f"/sessions/{session_id}/events",
        json={"kind": "user", "content": "changed"},
        headers={"Idempotency-Key": "one"},
    )
    assert conflict.status_code == 409
    await client.post(f"/sessions/{session_id}/memory", json={"content": "Architecture uses Redis"})
    assert len((await client.get(f"/sessions/{session_id}/memory")).json()) == 1
    assert len((await client.get(f"/sessions/{session_id}/events")).json()) == 2


async def test_concurrent_idempotent_writes(client, session_id):
    results = await asyncio.gather(
        *[
            client.post(
                f"/sessions/{session_id}/memory",
                json={"content": "Redis revision"},
                headers={"Idempotency-Key": "same"},
            )
            for _ in range(12)
        ]
    )
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["event_id"] for r in results}) == 1
    assert (await client.get(f"/sessions/{session_id}")).json()["revision"] == 1


async def test_concurrent_distinct_writes(client, session_id):
    results = await asyncio.gather(
        *[
            client.post(f"/sessions/{session_id}/memory", json={"content": f"Redis revision {i}"})
            for i in range(12)
        ]
    )
    assert all(r.status_code == 201 for r in results)
    assert (await client.get(f"/sessions/{session_id}")).json()["revision"] == 12


async def test_cache_revision_invalidation_and_session_isolation(client, session_id):
    data = {"session_id": session_id, "query": "redis cache"}
    await client.post(f"/sessions/{session_id}/memory", json={"content": "redis cache old"})
    first = (await client.post("/context/build", json=data)).json()
    second = (await client.post("/context/build", json=data)).json()
    assert not first["cache_hit"] and second["cache_hit"]
    await client.post(f"/sessions/{session_id}/memory", json={"content": "redis cache new"})
    third = (await client.post("/context/build", json=data)).json()
    assert not third["cache_hit"] and third["revision"] > second["revision"]
    other = (await client.post("/sessions", json={"agent_id": "other"})).json()["session_id"]
    isolated = (await client.post("/context/build", json={**data, "session_id": other})).json()
    assert isolated["considered"] == 0


async def test_budget_cannot_exceed_session_cap(client, session_id):
    await client.post(f"/sessions/{session_id}/memory", json={"content": "redis " * 1000})
    result = (
        await client.post(
            "/context/build",
            json={"session_id": session_id, "query": "redis", "token_budget": 4096},
        )
    ).json()
    assert result["token_budget"] == 128
    assert result["estimated_tokens_after"] <= 128


async def test_errors_metrics_request_ids_and_pagination(client, session_id):
    assert (await client.get("/sessions/missing")).status_code == 404
    assert (
        await client.post("/sessions", json={"agent_id": "", "token_budget": -1})
    ).status_code == 422
    assert (await client.get(f"/sessions/{session_id}/events?limit=5000")).status_code == 422
    for i in range(3):
        await client.post(
            f"/sessions/{session_id}/events", json={"kind": "user", "content": str(i)}
        )
    assert len((await client.get(f"/sessions/{session_id}/events?limit=1&offset=1")).json()) == 1
    response = await client.get("/health", headers={"x-request-id": "my-request"})
    assert response.status_code == 200 and response.headers["x-request-id"] == "my-request"
    metrics = await client.get("/metrics")
    assert "broker_requests_total" in metrics.text
    assert session_id not in metrics.text  # bounded label cardinality


async def test_persistence_across_restart(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/durable.db", _env_file=None)
    for iteration in range(2):
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                if iteration == 0:
                    sid = (await client.post("/sessions", json={"agent_id": "durable"})).json()[
                        "session_id"
                    ]
                    await client.post(
                        f"/sessions/{sid}/memory", json={"content": "Survives restart"}
                    )
                else:
                    assert (await client.get(f"/sessions/{sid}/memory")).json()[0][
                        "content"
                    ] == "Survives restart"


async def test_authentication(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/auth.db",
            api_key="secret-test-only",
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/tools")).status_code == 401
            assert (
                await client.get("/tools", headers={"Authorization": "Bearer secret-test-only"})
            ).status_code == 200


async def test_model_api_and_request_correlation(client, session_id):
    response = await client.post(
        "/models/route",
        json={"session_id": session_id, "query": "redis cache"},
        headers={"x-request-id": "route-test"},
    )
    assert response.status_code == 200 and response.json()["provider"] == "mock"
    events = (await client.get(f"/sessions/{session_id}/events")).json()
    assert events[0]["metadata"]["request_id"] == "route-test"
    traced = await client.post(
        "/tools/calculator/execute",
        json={"session_id": session_id, "arguments": {"expression": "1+1"}},
        headers={"x-request-id": "tool-request"},
    )
    assert traced.json()["success"]
    events = (await client.get(f"/sessions/{session_id}/events")).json()
    assert events[0]["metadata"]["request_id"] == "tool-request"


async def test_missing_session_write_and_body_limit(client):
    missing = await client.post("/sessions/missing/memory", json={"content": "unknown"})
    assert missing.status_code == 404
    large = await client.post("/sessions", content=b"{}", headers={"Content-Length": "262145"})
    assert large.status_code == 413

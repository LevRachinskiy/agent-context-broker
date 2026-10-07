"""Run with real services through TEST_DATABASE_URL and TEST_REDIS_URL."""

import asyncio
import os

import pytest

from app.models.schemas import EventCreate, EventKind, SessionCreate
from app.observability.metrics import Metrics
from app.repositories.cache import RedisCache
from app.repositories.sql import SQLRepository


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL service not configured")
async def test_real_postgres_concurrency_idempotency_and_restart():
    url = os.environ["TEST_DATABASE_URL"]
    repository = SQLRepository(url)
    await repository.initialize()
    session = await repository.create_session(SessionCreate(agent_id="postgres-integration"))
    data = EventCreate(kind=EventKind.memory, content="durable postgres memory")
    results = await asyncio.gather(
        *[repository.append(session.session_id, data, [1.0], "same-key") for _ in range(20)]
    )
    assert len({event.event_id for event in results}) == 1
    assert (await repository.get_session(session.session_id)).revision == 1
    await asyncio.gather(*[repository.append(session.session_id, data, [1.0]) for _ in range(20)])
    assert (await repository.get_session(session.session_id)).revision == 21
    await repository.close()
    reopened = SQLRepository(url)
    assert len(await reopened.list_events(session.session_id)) == 21
    await reopened.close()


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="Redis service not configured")
async def test_real_redis_cache_shared_across_instances():
    url = os.environ["TEST_REDIS_URL"]
    first, second = RedisCache(url, Metrics()), RedisCache(url, Metrics())
    await first.set("integration-test", "shared", 2)
    assert await second.get("integration-test") == "shared"
    await first.client.delete("integration-test")
    assert await second.get("integration-test") is None
    await first.close()
    await second.close()

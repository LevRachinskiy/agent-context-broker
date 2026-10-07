from datetime import timedelta

import pytest

from app.models.schemas import ContextRequest, Event, EventKind, now
from app.providers.embeddings import HashEmbeddingProvider, cosine
from app.services.context import RankingService, estimate_tokens


async def event(text, kind=EventKind.memory, age=0, importance=0.8, index=0):
    return Event(
        event_id=f"event-{index}",
        session_id="session",
        kind=kind,
        content=text,
        created_at=now() - timedelta(hours=age),
        importance=importance,
        embedding=await HashEmbeddingProvider().embed(text),
    )


async def select(events, query="redis cache stale revision", budget=128, **kwargs):
    request = ContextRequest(session_id="session", query=query, token_budget=budget, **kwargs)
    return RankingService().select(
        await HashEmbeddingProvider().embed(query), events, request, budget, 1, 2000
    )


async def test_relevance_over_recency():
    old = await event("redis cache stale revision", age=240, index=1)
    recent = await event("Lunch pasta recipe", age=0, index=2)
    package = await select([recent, old])
    assert package.items[0].event_id == old.event_id
    assert package.irrelevant_removed == 1


async def test_exact_and_near_duplicates():
    a = await event("redis cache stale revision", index=1)
    b = await event("Redis cache stale revision!", index=2)
    c = await event("redis cache stale revision redis", index=3)
    package = await select([a, b, c], duplicate_threshold=0.9)
    assert package.selected == 1
    assert package.duplicates_removed == 2
    assert package.estimated_tokens_after < package.estimated_tokens_before


@pytest.mark.parametrize("budget", [32, 64, 128, 256])
async def test_utf8_budget_and_compression(budget):
    events = [await event("redis cache " + "данные🙂 " * 200, index=i) for i in range(5)]
    package = await select(events, query="redis cache данные", budget=budget, min_relevance=0)
    assert estimate_tokens(package.text) <= budget
    assert package.estimated_tokens_after <= budget
    assert any(item.compressed for item in package.items)
    package.text.encode().decode()


async def test_empty_and_zero_vectors():
    package = await select([], query="!!!")
    assert package.selected == package.estimated_tokens_after == 0
    assert cosine([0.0], [0.0]) == 0
    with pytest.raises(ValueError):
        cosine([1.0], [1.0, 2.0])


async def test_score_inspectable_and_deterministic():
    timestamp = now()
    events = [
        await event("redis cache revision", index=1),
        await event("redis cache refresh", importance=0.1, age=48, index=2),
    ]
    ranking = RankingService()
    request = ContextRequest(session_id="session", query="redis cache")
    query = await HashEmbeddingProvider().embed(request.query)
    a = ranking.select(query, events, request, 128, 1, 2000, timestamp)
    b = ranking.select(query, events[::-1], request, 128, 1, 2000, timestamp)
    assert a == b
    for item in a.items:
        expected = (
            0.65 * item.semantic_score
            + 0.15 * item.recency_score
            + 0.15 * item.importance_score
            + 0.05 * item.source_score
        )
        assert item.final_score == pytest.approx(expected, abs=1e-6)


async def test_candidate_accounting():
    events = [
        await event("redis cache " + "word " * 200, index=1),
        await event("other redis query", index=2),
        await event("Lunch pizza recipe", index=3),
    ]
    package = await select(events, budget=32)
    assert (
        package.selected
        + package.budget_dropped
        + package.duplicates_removed
        + package.irrelevant_removed
    ) == package.considered


async def test_budget_drops_distinct_items():
    a = await event("redis cache revision " + "data " * 100, index=1)
    b = await event("redis cache retry " + "status " * 100, index=2)
    package = await select(
        [a, b],
        query="redis cache revision data retry status",
        budget=32,
        min_relevance=0,
        duplicate_threshold=1,
    )
    assert package.selected == 1 and package.budget_dropped == 1


async def test_concurrent_write_prevents_cache_publication(client, session_id, monkeypatch):
    from app.models.schemas import MemoryCreate

    service = client.broker.context
    original = client.broker.repository.list_events
    wrote = False

    async def overlapping_read(*args, **kwargs):
        nonlocal wrote
        events = await original(*args, **kwargs)
        if not wrote:
            wrote = True
            await client.broker.sessions.remember(
                session_id, MemoryCreate(content="redis cache new")
            )
        return events

    monkeypatch.setattr(client.broker.repository, "list_events", overlapping_read)
    request = ContextRequest(session_id=session_id, query="redis cache")
    first = await service.build(request)
    second = await service.build(request)
    third = await service.build(request)
    assert not first.cache_hit and not second.cache_hit and third.cache_hit
    assert second.revision > first.revision and second.considered == 1

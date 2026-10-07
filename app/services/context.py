import hashlib
import math
import re
from datetime import datetime

from app.models.schemas import ContextItem, ContextPackage, ContextRequest, Event, EventKind, now
from app.observability.metrics import Metrics
from app.providers.embeddings import EmbeddingProvider, cosine
from app.repositories.cache import Cache
from app.repositories.sql import Repository


def estimate_tokens(text: str) -> int:
    """UTF-8 bytes / 4 heuristic, not a model tokenizer or a provider billing count."""
    return math.ceil(len(text.encode("utf-8")) / 4)


def render_item(source: EventKind, event_id: str, content: str) -> str:
    return f"[{source.value}:{event_id}]\n{content}\n"


def normalize(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def truncate(text: str, byte_limit: int) -> str:
    if len(text.encode()) <= byte_limit:
        return text
    if byte_limit < 4:
        return ""
    return text.encode()[: byte_limit - 3].decode("utf-8", errors="ignore") + "..."


SOURCE_WEIGHTS = {
    EventKind.memory: 1.0,
    EventKind.tool_response: 0.9,
    EventKind.user: 0.8,
    EventKind.assistant: 0.6,
    EventKind.error: 0.7,
    EventKind.model_response: 0.6,
    EventKind.tool_call: 0.4,
}


class RankingService:
    def select(
        self,
        query_vector: list[float],
        events: list[Event],
        request: ContextRequest,
        budget: int,
        revision: int,
        candidate_limit: int,
        timestamp: datetime | None = None,
    ) -> ContextPackage:
        timestamp = timestamp or now()
        scored: list[tuple[float, float, float, Event]] = []
        before = sum(estimate_tokens(render_item(e.kind, e.event_id, e.content)) for e in events)
        for event in events:
            semantic = max(0.0, cosine(query_vector, event.embedding))
            age_hours = max(0.0, (timestamp - event.created_at).total_seconds() / 3600)
            recency = math.exp(-age_hours / 168)
            score = (
                0.65 * semantic
                + 0.15 * recency
                + 0.15 * event.importance
                + 0.05 * SOURCE_WEIGHTS[event.kind]
            )
            scored.append((score, semantic, recency, event))
        scored.sort(key=lambda row: (-row[0], row[3].event_id))
        kept: list[Event] = []
        items: list[ContextItem] = []
        used = duplicates = irrelevant = dropped = 0
        for score, semantic, recency, event in scored:
            if semantic < request.min_relevance:
                irrelevant += 1
                continue
            if any(
                normalize(event.content) == normalize(old.content)
                or cosine(event.embedding, old.embedding) >= request.duplicate_threshold
                for old in kept
            ):
                duplicates += 1
                continue
            kept.append(event)
            remaining = budget - used
            overhead = len(render_item(event.kind, event.event_id, "").encode())
            content = truncate(event.content, max(0, remaining * 4 - overhead))
            if not content:
                dropped += 1
                continue
            cost = estimate_tokens(render_item(event.kind, event.event_id, content))
            if cost > remaining:
                dropped += 1
                continue
            used += cost
            items.append(
                ContextItem(
                    event_id=event.event_id,
                    source=event.kind,
                    content=content,
                    semantic_score=round(semantic, 6),
                    recency_score=round(recency, 6),
                    importance_score=event.importance,
                    source_score=SOURCE_WEIGHTS[event.kind],
                    final_score=round(score, 6),
                    estimated_tokens=cost,
                    compressed=content != event.content,
                    reason=(
                        "0.65 relevance + 0.15 recency + 0.15 importance + 0.05 source; budget fit"
                    ),
                )
            )
        text = "".join(render_item(i.source, i.event_id, i.content) for i in items)
        return ContextPackage(
            session_id=request.session_id,
            revision=revision,
            query=request.query,
            items=items,
            text=text,
            considered=len(events),
            selected=len(items),
            duplicates_removed=duplicates,
            irrelevant_removed=irrelevant,
            budget_dropped=dropped,
            estimated_tokens_before=before,
            estimated_tokens_after=estimate_tokens(text),
            token_budget=budget,
            candidate_limit=candidate_limit,
        )


class ContextService:
    def __init__(
        self,
        repository: Repository,
        embeddings: EmbeddingProvider,
        cache: Cache,
        metrics: Metrics,
        ttl: int = 120,
        candidate_limit: int = 2000,
    ):
        self.repository, self.embeddings, self.cache = repository, embeddings, cache
        self.metrics, self.ttl, self.candidate_limit = metrics, ttl, candidate_limit
        self.ranking = RankingService()

    async def build(self, request: ContextRequest) -> ContextPackage:
        session = await self.repository.get_session(request.session_id)
        budget = min(request.token_budget or session.token_budget, session.token_budget)
        identity = (
            request.model_dump_json()
            + str(session.revision)
            + self.embeddings.identity
            + str(self.candidate_limit)
            + "ranking-v1"
        )
        key = "context:" + hashlib.sha256(identity.encode()).hexdigest()
        cached = await self.cache.get(key)
        if cached:
            package = ContextPackage.model_validate_json(cached)
            package.cache_hit = True
            self.metrics.cache.labels("hit").inc()
        else:
            self.metrics.cache.labels("miss").inc()
            events = await self.repository.list_events(request.session_id, self.candidate_limit)
            vector = await self.embeddings.embed(request.query)
            package = self.ranking.select(
                vector, events, request, budget, session.revision, self.candidate_limit
            )
            # Do not cache a mixed read if a concurrent writer changed the revision.
            latest = await self.repository.get_session(request.session_id)
            if latest.revision == session.revision:
                await self.cache.set(key, package.model_dump_json(), self.ttl)
        self.metrics.context.labels("considered").inc(package.considered)
        self.metrics.context.labels("selected").inc(package.selected)
        self.metrics.tokens.labels("before").inc(package.estimated_tokens_before)
        self.metrics.tokens.labels("after").inc(package.estimated_tokens_after)
        return package

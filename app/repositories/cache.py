import asyncio
import time
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.observability.metrics import Metrics


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def close(self) -> None: ...


class LocalCache:
    """Bounded, process-local demo cache. It is not distributed."""

    def __init__(self, capacity: int = 1024):
        self.values: dict[str, tuple[float, str]] = {}
        self.capacity = capacity
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        async with self.lock:
            value = self.values.get(key)
            if value and value[0] > time.monotonic():
                return value[1]
            self.values.pop(key, None)
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        async with self.lock:
            if len(self.values) >= self.capacity:
                self.values.pop(next(iter(self.values)))
            self.values[key] = (time.monotonic() + ttl, value)

    async def close(self) -> None:
        self.values.clear()


class RedisCache:
    """Cache failures are misses; durable reads never depend on Redis availability."""

    def __init__(self, url: str, metrics: Metrics):
        self.client = Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2
        )
        self.metrics = metrics

    async def get(self, key: str) -> str | None:
        try:
            return await self.client.get(key)
        except (RedisError, OSError):
            self.metrics.cache_errors.inc()
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        try:
            await self.client.set(key, value, ex=ttl)
        except (RedisError, OSError):
            self.metrics.cache_errors.inc()

    async def close(self) -> None:
        await self.client.aclose()

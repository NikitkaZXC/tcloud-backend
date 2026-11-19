import asyncio

import pytest

from src.config import settings
from src.infrastructure.cache import RateLimitStore


@pytest.fixture
def store(redis) -> RateLimitStore:
    return RateLimitStore(redis)


def key(name: str) -> str:
    return f"{settings.redis.PREFIX}:{settings.rate_limit.PREFIX}:{name}"


async def test_rejects_over_limit_without_recording(redis, store):
    results = [await store.hit("scope:ip:1", 3, 60) for _ in range(5)]
    assert [count for count, _ in results] == [1, 2, 3, 4, 4]
    assert all(0 < retry_after <= 60 for _, retry_after in results)
    assert await redis.zcard(key("scope:ip:1")) == 3


async def test_window_slides(store):
    await store.hit("scope:ip:2", 2, 1)
    await asyncio.sleep(0.6)
    await store.hit("scope:ip:2", 2, 1)
    rejected, _ = await store.hit("scope:ip:2", 2, 1)
    await asyncio.sleep(0.5)
    accepted, _ = await store.hit("scope:ip:2", 2, 1)
    assert (rejected, accepted) == (3, 2)


async def test_replaces_key_of_another_type(redis, store):
    await redis.set(key("scope:ip:3"), 10, ex=60)
    count, _ = await store.hit("scope:ip:3", 3, 60)
    assert count == 1
    assert await redis.type(key("scope:ip:3")) in ("zset", b"zset")


async def test_is_atomic(store):
    results = await asyncio.gather(*(store.hit("scope:ip:4", 5, 60) for _ in range(20)))
    assert sum(count <= 5 for count, _ in results) == 5
